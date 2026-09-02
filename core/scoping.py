"""The one location-scoping predicate (A2, ledger cross-stage services).

A2 is the whole rule: **the tenant is the security boundary, the sede is a
scope.** Sede visibility is query-layer and UI-default, never RLS -- an owner
comparing six sedes reads all six in one query, and a policy that made that
impossible would be worked around within a week.

There is one helper. A stage writing its own `location_id IN (...)` is a defect.
"""

from ninja.errors import HttpError

from core.models import LocationStatus, Role

#: Roles whose default scope is the whole network.
NETWORK_ROLES = (Role.OWNER, Role.ADMIN, Role.PLATFORM_ADMIN)


class Misconfigured(Exception):
    """A cashier with no home sede.

    The helper refuses the request instead of falling through to all locations.
    The CHECK constraint makes that state unreachable through the API; this is
    what makes it unreachable through a management command or a bad backfill too.
    """


def _every_active_location(tenant_id):
    from core.models import Location

    return list(
        Location.objects.filter(
            tenant_id=tenant_id, status=LocationStatus.ACTIVE
        ).values_list("id", flat=True)
    )


def readable_locations(user, tenant_id, *, requested=None, network_read=False):
    """The set of `location_id`s a query may read.

    Two modes, chosen by the endpoint and never by the caller:

    **Scoped** -- the default, and the only mode for a write. A `cashier` gets
    exactly their home sede; `owner`, `admin` and a `platform_admin` inside a
    pinned tenant get every active location, narrowed by an explicit filter.

    **Network-read** -- declared by an endpoint that is read-only and
    network-wide by design. §2 grants a cashier a network-wide stock lookup and
    this is the mode that serves it.

    A scoped query narrows; it never refuses silently. An explicit filter naming
    a location outside the identity's set is rejected, not intersected away -- a
    silently emptied result is indistinguishable from a sede with nothing in it,
    and the difference matters the first time a cashier reports that Suba has no
    stock.
    """
    every = _every_active_location(tenant_id)

    if network_read or user.role in NETWORK_ROLES:
        allowed = every
    elif user.role == Role.CASHIER:
        if user.location_id is None:
            raise Misconfigured(
                "Esta cuenta de mostrador no tiene sede asignada. Pida a la "
                "administradora de su droguería que le asigne una."
            )
        allowed = [user.location_id]
    else:
        raise Misconfigured(f"El perfil {user.role} no tiene alcance de sedes.")

    if not requested:
        return list(allowed)

    allowed_set = {str(one) for one in allowed}
    outside = [str(one) for one in requested if str(one) not in allowed_set]
    if outside:
        raise HttpError(
            403,
            "Su perfil no alcanza la sede solicitada. Quite el filtro de sede "
            "para ver lo que sí alcanza.",
        )
    return [one for one in requested]


def default_location(user):
    """What the interface preselects in a sede filter -- A2's "UI default" half.

    A cashier's own sede; nothing for the office, which defaults to all of them.
    """
    return user.location_id if user.role == Role.CASHIER else None


def scope(
    queryset, user, tenant_id, *, path="location_id", requested=None, network_read=False
):
    """Narrow a queryset to the locations this identity may read."""
    allowed = readable_locations(
        user, tenant_id, requested=requested, network_read=network_read
    )
    return queryset.filter(**{f"{path}__in": allowed})
