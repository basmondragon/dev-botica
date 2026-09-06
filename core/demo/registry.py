"""The fixture registry, the guard, and the run.

A stage registers one fixture with a name, the tables it writes
(`guard_tables`), the fixtures it `requires`, and a build entry point taking the
pinned tenant and the profile. The command orders the registered fixtures
topologically, runs the whole profile in one transaction per tenant, counts
every guard table before and after each fixture, and **fails the run when a
fixture writes into a table it did not declare** -- the cheapest way to catch a
stage that seeded around another stage's service rather than through it.
"""

import uuid
from dataclasses import dataclass, field

from django.db import connection

#: The five profiles architecture.md §1 fixes. There is no default: a bare run
#: refuses and prints these names, because a seed that guessed which shape you
#: wanted is a seed that quietly builds the wrong one.
PROFILES = ("default", "young", "cold", "scale", "minimal")

#: Every seeded id is a uuid v5 over this namespace and the row's own natural
#: key. That is what makes a rebuilt seed keep the ids it had -- so a demo
#: script, a screenshot, a saved link and a bug report all still point at the
#: same row after somebody resets their database.
NAMESPACE = uuid.UUID("6b1f0c2e-9a4d-5f77-9c31-3d2ba7e0f451")


class SeedRefused(Exception):
    """The guard said no, and a failure writes nothing."""


def uid(profile, table, key):
    """The id a row gets, derived from the profile, its table and its key."""
    return uuid.uuid5(NAMESPACE, f"{profile}:{table}:{key}")


@dataclass
class SeedContext:
    """What a fixture is handed: the pinned tenant, the profile, and the report."""

    profile: str
    tenant_id: uuid.UUID
    slug: str
    written: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def uid(self, table, key):
        return uid(self.profile, table, key)

    def wrote(self, table, count):
        self.written[table] = self.written.get(table, 0) + count

    def note(self, line):
        """A line the command prints on completion -- an invitation link, say."""
        self.notes.append(line)


@dataclass(frozen=True)
class Fixture:
    name: str
    guard_tables: tuple
    requires: tuple
    build: object
    #: Every id this fixture owns for a profile. The guard compares the rows
    #: already in the database against exactly this set: one row it did not
    #: write refuses the whole run.
    owned_ids: object


REGISTRY: dict[str, Fixture] = {}


def register(name, *, tables, requires=(), build, owned_ids):
    """Register one stage's fixture. Called once per stage, at import."""
    if name in REGISTRY:
        raise RuntimeError(f"A fixture named {name!r} is already registered.")
    REGISTRY[name] = Fixture(
        name=name,
        guard_tables=tuple(tables),
        requires=tuple(requires),
        build=build,
        owned_ids=owned_ids,
    )
    return REGISTRY[name]


def ordered():
    """The registered fixtures, topologically over `requires`."""
    done: list[Fixture] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(name):
        if name in seen:
            return
        if name in visiting:
            raise RuntimeError(f"The fixture graph has a cycle at {name!r}.")
        fixture = REGISTRY.get(name)
        if fixture is None:
            raise RuntimeError(f"No fixture named {name!r} is registered.")
        visiting.add(name)
        for dependency in fixture.requires:
            visit(dependency)
        visiting.discard(name)
        seen.add(name)
        done.append(fixture)

    for name in REGISTRY:
        visit(name)
    return done


def all_guard_tables():
    tables: set[str] = set()
    for fixture in REGISTRY.values():
        tables.update(fixture.guard_tables)
    return sorted(tables)


def _key_column(table):
    """`tenants` has no `tenant_id` -- it *is* the tenant."""
    return "id" if table == "tenants" else "tenant_id"


def _counts(tables, tenant_id):
    """Row counts for this tenant.

    The pin already confines the runtime role to one network; the predicate is
    stated anyway, so that a command run as the migration role -- which holds
    BYPASSRLS and would otherwise see every network -- counts the same thing.
    """
    counts = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(
                f"SELECT count(*) FROM {table} "  # noqa: S608 -- from the registry
                f"WHERE {_key_column(table)} = %s",
                [str(tenant_id)],
            )
            counts[table] = cursor.fetchone()[0]
    return counts


def _ids(table, tenant_id):
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id FROM {table} "  # noqa: S608 -- the registry names it
            f"WHERE {_key_column(table)} = %s",
            [str(tenant_id)],
        )
        return {uuid.UUID(str(row[0])) for row in cursor.fetchall()}


def check_guard(context):
    """The second structural condition, run under the pin before anything writes.

    Every table any registered fixture declares is counted, and a single row
    whose id the seed would not have derived refuses the whole run, names the
    table and the count, and rolls back. A rerun over the seed's own rows is
    therefore idempotent, and a run against a tenant holding one real row is
    impossible -- which is the property that matters, because the failure this
    guards against is not a broken demo but synthetic rows in a client's
    database.
    """
    fixtures = ordered()
    owned: dict[str, set] = {}
    for fixture in fixtures:
        for table, ids in fixture.owned_ids(context).items():
            owned.setdefault(table, set()).update(ids)

    for table in all_guard_tables():
        foreign = _ids(table, context.tenant_id) - owned.get(table, set())
        if foreign:
            raise SeedRefused(
                f"{table} holds {len(foreign)} row(s) this seed did not write. "
                f"Nothing was written. The demo seed only ever touches rows it "
                f"derived itself."
            )


def _analysable():
    """The public tables this connection's role is allowed to `ANALYZE`.

    The runtime role owns nothing (A1: it is granted rights on tables the
    migration role owns), and `ANALYZE` on a table you do not own is not an
    error -- it is a `WARNING: permission denied to analyze` per table and no
    statistics. Asking the catalog which tables are ours turns that into an
    empty list and silence, so a seed run under either role behaves.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname = 'public' "
            "AND pg_get_userbyid(c.relowner) = current_user "
            "ORDER BY c.relname"
        )
        return [row[0] for row in cursor.fetchall()]


def _analyze(tables):
    """Refresh the planner's statistics between fixtures.

    **A seed inserts every row it will ever hold inside one transaction, and
    autovacuum cannot see a transaction that has not committed.** So each
    fixture plans against whatever statistics the tables held before the run,
    which on a database the suite creates fresh say `sales` and `sale_lines`
    are empty. The planner reads a join over two empty tables as free, picks a
    nested loop, and scans one side once per row of the other.

    On a pristine heap that plan is wrong and still fast -- there are few
    physical pages to scan, so the cost hides. It stops hiding once a test
    session has rolled back a few seeds: the dead tuples stay in the heap until
    something vacuums them, the same nested loop now walks all of them, and the
    query that took a moment on the first test takes a minute on the twelfth.
    Measured that way rather than guessed: S8's miner sat on one `sale_lines`
    join for eighty-three seconds and S7's elasticity estimator on its weekly
    aggregate for sixty-five, and `test_catalog_seed.py` -- eighteen tests, each
    building a tenant -- went from 37m35s to 6m24s with the line below and
    nothing else changed. A clean single-file run barely moves, which is the
    tell: this buys a plan that does not degrade, not a faster scan.

    `ANALYZE` runs inside the transaction, sees the uncommitted rows, and rolls
    back with everything else if the run fails, so it costs a sample per table
    and nothing in correctness.

    It runs **between** fixtures rather than once at the end because the stages
    that read at volume -- forecasting, pricing, mining -- are the later ones,
    and statistics gathered after they have already run would help nobody but
    the tests that follow.
    """
    if not tables:
        return
    with connection.cursor() as cursor:
        # Quoted from the catalog's own `relname`, so a table named for a
        # keyword cannot become one.
        cursor.execute("ANALYZE " + ", ".join(f'"{table}"' for table in tables))


def run_profile(context):
    """Run every registered fixture for one profile, in dependency order."""
    check_guard(context)

    tables = all_guard_tables()
    analysable = _analysable()
    for fixture in ordered():
        before = _counts(tables, context.tenant_id)
        fixture.build(context)
        _analyze(analysable)
        after = _counts(tables, context.tenant_id)
        undeclared = [
            table
            for table in tables
            if after[table] != before[table] and table not in fixture.guard_tables
        ]
        if undeclared:
            raise SeedRefused(
                f"The fixture {fixture.name!r} wrote into "
                f"{', '.join(undeclared)}, which it did not declare. A fixture "
                f"that seeds around another stage's service is the failure this "
                f"check exists for."
            )
    return context
