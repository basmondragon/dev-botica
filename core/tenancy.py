"""Tenant pinning: one module, five entry points, one permitted unpinned query.

Ledger rule 6 names five contexts that touch the database, and A1 makes the
runtime role a non-owner under `FORCE ROW LEVEL SECURITY` -- so an unpinned
connection reads and writes **zero rows**. Every context establishes the pin
here. No later stage issues its own `SET LOCAL`.

The five contexts:

  1. HTTP request        -- `core.middleware.TenantMiddleware`
  2. Management command  -- `core.management.commands._tenant.TenantCommand`
  3. Background job      -- `pinned_job`
  4. Sync push           -- `pinned_batch`
  5. Unauthenticated in  -- `resolve_then_pin`   (nobody at v1)

and the one deliberate exception, `RESOLVERS`, which maps an opaque external key
to a tenant id before any pin exists. Its return type is uuids and nothing else,
so the exception cannot widen by accident.
"""

import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from django.db import connection, transaction

#: What a `platform_admin` session carries before a tenant is selected. It is not
#: a pin: no policy matches it, so a request holding it reads zero tenant rows.
NO_TENANT = uuid.UUID(int=0)


class ForeignTenantRow(Exception):
    """A batch element naming a tenant other than the one the session resolved.

    The batch is rejected; the element is not filtered out of it. A silently
    dropped row is indistinguishable from a row that was never sent.
    """


@contextmanager
def pin_tenant(tenant_id, *, user_id=None):
    """Open a transaction and pin it to one tenant for its whole life.

    The previous pin is restored on the way out. In production nothing nests --
    a request, a command and a job are each one top-level transaction -- but
    `SET LOCAL` outlives a savepoint, so a block that did not restore would
    leave the pin it set behind for whatever ran next inside the same
    transaction, which is how a test suite reads as isolated and is not.
    """
    if tenant_id is None:
        raise ValueError(
            "pin_tenant needs a tenant id; use NO_TENANT for an identity that "
            "belongs to no network."
        )
    with transaction.atomic():
        previous = _current_settings()
        repin(tenant_id, user_id=user_id)
        try:
            yield tenant_id
        finally:
            # A transaction on its way to a rollback undoes every `SET LOCAL` of
            # its own accord, and a broken one answers no further statement.
            if not connection.needs_rollback:
                _restore(previous)


def _current_settings():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('app.tenant_id', true), "
            "       current_setting('app.user_id', true)"
        )
        return cursor.fetchone()


def _restore(previous):
    tenant_value, user_value = previous
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "       set_config('app.user_id', %s, true)",
            [tenant_value or "", user_value or ""],
        )


def repin(tenant_id, *, user_id=None):
    """Move the pin inside a transaction someone else already opened.

    `user_id` is the acting identity. It exists for exactly one reason: a
    `platform_admin` belongs to no network, so their own `users` row is
    invisible under every pin, and Django cannot load `request.user` without it.
    The policy that reads it admits that one row and nothing else.
    """
    if tenant_id is None:
        raise ValueError("repin needs a tenant id; use NO_TENANT for no network.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "       set_config('app.user_id', %s, true)",
            [str(tenant_id), "" if user_id is None else str(user_id)],
        )


def current_tenant():
    """The tenant this transaction is pinned to, or None if it is not pinned."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT nullif(current_setting('app.tenant_id', true), '')")
        row = cursor.fetchone()
    return uuid.UUID(row[0]) if row and row[0] else None


# ---------------------------------------------------------------------------
# The one permitted unpinned query, as a registry.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """What a resolver may return: uuids, never a row.

    If this is ever relaxed to a row, the single audited hole becomes an
    unpinned read path into a tenant table and every guarantee here goes with it.
    """

    tenant_id: uuid.UUID | None
    subject_id: uuid.UUID | None = None


RESOLVERS: dict[str, Callable] = {}


def register_resolver(name, function):
    """Register a lookup mapping one opaque external key to one tenant id.

    S0 registers `sign_in`. S2 will register the device key and S5 the provider's
    document reference; both call this module rather than opening a second hole.
    """
    if name in RESOLVERS:
        raise RuntimeError(
            f"A resolver named {name!r} is already registered. The unpinned "
            "lookups are a closed set and each one is registered once."
        )
    RESOLVERS[name] = function
    return function


def resolve(name, key):
    """Run one registered lookup, outside any pin, and check what it returned."""
    resolver = RESOLVERS.get(name)
    if resolver is None:
        raise RuntimeError(
            f"No resolver named {name!r}. The unpinned lookups are declared in "
            "core.tenancy and nowhere else."
        )
    answer = resolver(key)
    if answer is None:
        return None
    if not isinstance(answer, Resolution):
        raise RuntimeError(
            f"The resolver {name!r} returned {type(answer).__name__}. A resolver "
            "returns uuids and nothing else."
        )
    return answer


def _sign_in(email):
    """Map an address to a user id and a tenant id, before any pin exists.

    A `platform_admin` resolves to `NO_TENANT`, which is not a network and pins
    nothing; their reach is governed by which tenant they later select.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM app_resolve_sign_in(%s)", [email.strip().lower()])
        row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    return Resolution(
        tenant_id=uuid.UUID(str(row[1])) if row[1] else NO_TENANT,
        subject_id=uuid.UUID(str(row[0])),
    )


register_resolver("sign_in", _sign_in)


def resolve_tenant_for_slug(slug):
    """A tenant id from its slug, for a platform admin naming one on a command
    line. Runs through the picker grant rather than outside the rails."""
    with tenant_picker(), connection.cursor() as cursor:
        cursor.execute("SELECT id FROM tenants WHERE slug = %s", [slug])
        row = cursor.fetchone()
    return uuid.UUID(str(row[0])) if row and row[0] else None


@contextmanager
def tenant_picker():
    """Grant the `tenants` picker for the enclosing transaction, and take it back.

    This is the only widening in the system and it widens exactly one thing: the
    SELECT on `tenants` itself, so a platform admin can choose a network from
    Django admin. It reaches no tenant-scoped table -- a selected tenant is still
    a pin like any other.
    """
    with transaction.atomic():
        grant_tenant_picker()
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.platform_admin', '', true)")


def grant_tenant_picker():
    """The same grant, held for the rest of the transaction rather than a block."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.platform_admin', 'on', true)")


# ---------------------------------------------------------------------------
# Context three: a background job.
# ---------------------------------------------------------------------------


@contextmanager
def pinned_job(payload):
    """Pin from a job payload before the job touches anything.

    A job whose payload carries no `tenant_id` fails visibly in the queue's own
    job table. A job that reports success having written nothing is the failure
    this refusal exists for, and in a log it is indistinguishable from the real
    thing.
    """
    raw = (payload or {}).get("tenant_id")
    if not raw:
        raise ValueError(
            "This job carries no tenant_id. A job cannot run unpinned: it would "
            "read and write zero rows and report success."
        )
    with pin_tenant(uuid.UUID(str(raw))) as tenant_id:
        yield tenant_id


# ---------------------------------------------------------------------------
# Context four: the sync push. S2 owns the payload; the transaction semantics
# and the rejection rule are fixed here.
# ---------------------------------------------------------------------------


@contextmanager
def pinned_batch(tenant_id, rows, *, tenant_of=lambda row: row.get("tenant_id")):
    """Apply a whole push batch inside one pinned transaction.

    A row naming another tenant rejects the batch rather than being filtered out
    of it: the rows that were legitimate are not applied either. This is fixed
    here, before the payload exists, because it is the rule a later stage would
    otherwise decide row by row.
    """
    with pin_tenant(tenant_id):
        for index, row in enumerate(rows):
            named = tenant_of(row)
            if named is None:
                continue
            if str(named) != str(tenant_id):
                raise ForeignTenantRow(
                    f"Element {index} of this batch names tenant {named}, and this "
                    f"device's session resolved {tenant_id}. The batch is rejected."
                )
        yield tenant_id


# ---------------------------------------------------------------------------
# Context five: an unauthenticated inbound request. Nobody at v1.
# ---------------------------------------------------------------------------


@contextmanager
def resolve_then_pin(resolver_name, key):
    """Resolve the tenant first, pin second, and run the handler inside the pin.

    Nothing in v1 calls this: S5 polls its target rather than being called back,
    so no stage ships an unauthenticated inbound endpoint. The path exists so
    that the first stage that needs one does not invent a second.
    """
    answer = resolve(resolver_name, key)
    if answer is None or answer.tenant_id is None:
        raise LookupError(
            f"The key given to {resolver_name!r} resolves to no tenant. Nothing "
            "is pinned and the handler body does not run."
        )
    with pin_tenant(answer.tenant_id):
        yield answer
