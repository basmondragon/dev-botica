"""The RLS catalog audit: one query per fact, and nothing taken on trust.

Row security off, or on but not forced, is a table the runtime role reads across
tenants. A table owned by the runtime role bypasses its own policy whatever the
policy says (A1). Neither is visible in any other check, which is why this one
exists and why it runs before anything else is looked at.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

EXEMPT_PREFIXES = ("django_", "auth_", "account_", "socialaccount_", "procrastinate_")

DATA_RELKINDS = ("r", "p")
DERIVED_RELKINDS = ("v", "m", "f")

PLAIN_USING = "(tenant_id = app_current_tenant())"
TENANTS_USING = "(id = app_current_tenant())"
PICKER_USING = "(current_setting('app.platform_admin'::text, true) = 'on'::text)"
USERS_USING = (
    "((tenant_id = app_current_tenant()) "
    "OR ((tenant_id IS NULL) AND (id = app_current_user())))"
)

#: The runtime role holds INSERT and SELECT here and must hold neither UPDATE
#: nor DELETE. Append-only is a grant, not a convention.
#:
#: `stock_moves` joins `audit_log` at S3, and for a sharper reason: A3 makes the
#: whole product's stock the sum of this table, so an UPDATE granted back by a
#: later migration would corrupt the ledger and the projection **consistently**
#: -- which is the one failure the rebuild check cannot see, because it leaves
#: both sides agreeing on the same wrong number.
APPEND_ONLY_TABLES = {"audit_log", "stock_moves"}

COMMAND_NAMES = {"*": "ALL", "r": "SELECT", "a": "INSERT", "w": "UPDATE", "d": "DELETE"}


class ExpectedPolicy:
    """One expected row of `pg_policy`."""

    def __init__(self, name, command, using, with_check=None, permissive=True):
        self.name = name
        self.command = command
        self.using = _normalise(using)
        self.with_check = _normalise(with_check) if with_check else None
        self.permissive = permissive


def _normalise(expression):
    """Collapse the whitespace `pg_get_expr` uses to wrap long expressions."""
    return " ".join(expression.split()) if expression else expression


def _plain_policy():
    return (ExpectedPolicy("tenant_isolation", "*", PLAIN_USING),)


EXPECTED_POLICIES = {
    "tenants": (
        ExpectedPolicy(
            "tenant_isolation", "*", TENANTS_USING, with_check=TENANTS_USING
        ),
        ExpectedPolicy("platform_admin_may_list", "r", PICKER_USING),
    ),
    "users": (
        ExpectedPolicy("tenant_isolation", "*", USERS_USING, with_check=USERS_USING),
    ),
}


class Command(BaseCommand):
    help = (
        "Assert RLS is enabled, forced and correctly keyed everywhere, that the "
        "runtime role owns nothing, and that the append-only tables stay so."
    )

    def handle(self, *args, **options):
        failures = []
        runtime_role = settings.BOTICA_RUNTIME_DB_USER

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname, c.relkind, c.relrowsecurity,
                       c.relforcerowsecurity, pg_get_userbyid(c.relowner),
                       coalesce(c.reloptions, '{}'),
                       EXISTS (SELECT 1 FROM pg_attribute a
                               WHERE a.attrelid = c.oid
                                 AND a.attname = 'tenant_id'
                                 AND NOT a.attisdropped)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = ANY(%s)
                ORDER BY c.relname
                """,
                [list(DATA_RELKINDS + DERIVED_RELKINDS)],
            )
            relations = cursor.fetchall()

            cursor.execute(
                """
                SELECT c.relname, p.polname, p.polcmd, p.polpermissive,
                       pg_get_expr(p.polqual, p.polrelid),
                       pg_get_expr(p.polwithcheck, p.polrelid)
                FROM pg_policy p
                JOIN pg_class c ON c.oid = p.polrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                """
            )
            policies: dict[str, list] = {}
            for relname, polname, polcmd, permissive, using, check in cursor.fetchall():
                policies.setdefault(relname, []).append(
                    (polname, polcmd, permissive, _normalise(using), _normalise(check))
                )

            cursor.execute("SELECT current_user")
            whoami = cursor.fetchone()[0]

            cursor.execute(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s",
                [runtime_role],
            )
            runtime_attributes = cursor.fetchone()

            grants = {}
            for table in APPEND_ONLY_TABLES:
                cursor.execute(
                    "SELECT privilege_type FROM information_schema.table_privileges "
                    "WHERE table_schema = 'public' AND table_name = %s "
                    "AND grantee = %s",
                    [table, runtime_role],
                )
                grants[table] = {row[0] for row in cursor.fetchall()}

        if runtime_attributes is None:
            failures.append(
                f"the runtime role {runtime_role} does not exist on this instance"
            )
        else:
            bypasses, is_superuser = runtime_attributes
            if bypasses:
                failures.append(
                    f"the runtime role {runtime_role} holds BYPASSRLS — every "
                    "policy is void"
                )
            if is_superuser:
                failures.append(
                    f"the runtime role {runtime_role} is a superuser — every "
                    "policy is void"
                )

        for name, relkind, rls, forced, owner, options, has_tenant_id in relations:
            exempt = name.startswith(EXEMPT_PREFIXES)
            scoped = has_tenant_id or name == "tenants"

            if owner == runtime_role:
                failures.append(
                    f"{name}: owned by the runtime role {owner} — RLS would be "
                    "bypassed (A1)"
                )
            if exempt:
                continue
            if relkind in DERIVED_RELKINDS:
                failures.extend(_check_derived(name, relkind, options))
                continue
            if not scoped:
                failures.append(
                    f"{name}: no tenant_id column and not a known exemption"
                )
                continue
            if not rls:
                failures.append(f"{name}: row security not enabled")
            if not forced:
                failures.append(f"{name}: row security not FORCED")
            failures.extend(_check_policies(name, policies.get(name, [])))

        for table, held in grants.items():
            for forbidden in ("UPDATE", "DELETE"):
                if forbidden in held:
                    failures.append(
                        f"{table}: the runtime role holds {forbidden} — append-only "
                        "is a grant, and a later stage's 'correction' would end the "
                        "property permanently"
                    )
            for required in ("INSERT", "SELECT"):
                if required not in held:
                    failures.append(f"{table}: the runtime role has no {required}")

        self.stdout.write(
            f"checked {len(relations)} relations as {whoami}; "
            f"runtime role is {runtime_role}"
        )
        if failures:
            for failure in failures:
                self.stderr.write(self.style.ERROR(failure))
            raise SystemExit(1)
        self.stdout.write(
            self.style.SUCCESS(
                "RLS enabled, forced and keyed to the pin on every tenant table; "
                "runtime role owns nothing; "
                + " and ".join(sorted(APPEND_ONLY_TABLES))
                + " are append-only"
            )
        )


def _check_derived(name, relkind, options):
    """A view, materialized view or foreign table in public, checked for RLS safety."""
    if relkind != "v":
        return [
            f"{name}: a {'materialized view' if relkind == 'm' else 'foreign table'} "
            "in public cannot carry a policy — the rows it holds are outside "
            "every tenant rail"
        ]
    invoker = {
        option.split("=", 1)[1].lower()
        for option in (options or [])
        if option.startswith("security_invoker=")
    }
    if not invoker & {"true", "on", "1", "yes"}:
        return [
            f"{name}: a view without security_invoker=true runs as its owner, "
            "which holds BYPASSRLS — it reads across every tenant"
        ]
    return []


def _check_policies(name, found):
    """A table's actual policies against exactly the declared expected ones."""
    expected = {
        policy.name: policy for policy in EXPECTED_POLICIES.get(name, _plain_policy())
    }
    actual = {policy[0]: policy for policy in found}

    failures = []
    for extra in sorted(set(actual) - set(expected)):
        polcmd = actual[extra][1]
        failures.append(
            f"{name}: unexpected policy {extra} FOR "
            f"{COMMAND_NAMES.get(polcmd, polcmd)} — a second permissive policy "
            "ORs rows back in"
        )
    for missing in sorted(set(expected) - set(actual)):
        failures.append(f"{name}: no policy {missing}")

    for policy_name in sorted(set(expected) & set(actual)):
        want = expected[policy_name]
        _, polcmd, permissive, using, with_check = actual[policy_name]
        if polcmd != want.command:
            failures.append(
                f"{name}.{policy_name}: scoped FOR "
                f"{COMMAND_NAMES.get(polcmd, polcmd)}, expected FOR "
                f"{COMMAND_NAMES.get(want.command, want.command)}"
            )
        if permissive != want.permissive:
            failures.append(
                f"{name}.{policy_name}: "
                f"{'permissive' if permissive else 'restrictive'}, expected "
                f"{'permissive' if want.permissive else 'restrictive'}"
            )
        if using != want.using:
            failures.append(
                f"{name}.{policy_name}: USING {using}, expected {want.using}"
            )
        if with_check != want.with_check:
            failures.append(
                f"{name}.{policy_name}: WITH CHECK {with_check or '(none)'}, "
                f"expected {want.with_check or '(none)'}"
            )
    return failures
