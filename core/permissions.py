"""One permission table, checked by a single dependency (architecture §2).

Every endpoint in the product runs behind this dependency, inside the pinned
transaction. No policy engine, no per-object ACLs.
"""

from ninja.errors import HttpError
from ninja.security import SessionAuth
from ninja.utils import check_csrf

from core.models import Role, Tenant, TenantStatus

LABELS = {
    Role.PLATFORM_ADMIN: "Plataforma",
    Role.OWNER: "Propietaria",
    Role.ADMIN: "Administradora",
    Role.CASHIER: "Mostrador",
}


def name_the_roles(roles):
    """`Propietaria o Administradora` -- the phrase a refusal names."""
    labels = [LABELS[role] for role in roles]
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + f" o {labels[-1]}"


class RoleAuth(SessionAuth):
    """401 unauthenticated, 403 naming the role required.

    `tenants.status` is enforced here rather than in a view: a network at
    `suspended` is unreachable by its own members, and a `platform_admin` inside
    it is exempt because restoring one is what that identity is for.
    """

    def __init__(self, *roles, requires_tenant=True):
        self.roles = tuple(roles)
        self.requires_tenant = requires_tenant
        super().__init__()

    def authenticate(self, request, key):
        user = request.user
        if not user.is_authenticated:
            return None
        if user.role not in self.roles:
            raise HttpError(
                403, f"Esta acción requiere el perfil {name_the_roles(self.roles)}."
            )
        if request.tenant_id is None and self.requires_tenant:
            raise HttpError(403, "No hay una droguería seleccionada en esta sesión.")
        if user.role != Role.PLATFORM_ADMIN and _is_suspended(request.tenant_id):
            raise HttpError(
                403,
                "Esta droguería está suspendida. Escriba a soporte para reactivarla.",
            )
        return user


def _is_suspended(tenant_id):
    """One primary-key read per request."""
    return Tenant.objects.filter(id=tenant_id, status=TenantStatus.SUSPENDED).exists()


class PublicAuth(SessionAuth):
    """Anonymous, but not unprotected: CSRF still applies."""

    def authenticate(self, request, key):
        return True

    def _get_key(self, request):
        if not getattr(request, "_ninja_csrf_exempt", False):
            if check_csrf(request):
                raise HttpError(
                    403,
                    "No pudimos verificar esta solicitud. Recargue la página e "
                    "intente de nuevo.",
                )
        return None


owner_only = RoleAuth(Role.OWNER)
owner_or_admin = RoleAuth(Role.OWNER, Role.ADMIN, Role.PLATFORM_ADMIN)
any_member = RoleAuth(Role.OWNER, Role.ADMIN, Role.CASHIER, Role.PLATFORM_ADMIN)
signed_in = RoleAuth(
    Role.OWNER,
    Role.ADMIN,
    Role.CASHIER,
    Role.PLATFORM_ADMIN,
    requires_tenant=False,
)
public = PublicAuth()


def may_invite_at(actor, role):
    """Who may issue an invitation, and at what role.

    §2 gives `admin` user management and withholds role changes, and an
    invitation necessarily carries a role -- so issuing one at `owner` or `admin`
    is a role assignment by another name. An `admin` may invite at `cashier`
    only. The opposite error, letting an `admin` mint an `owner`, is a privilege
    escalation that no audit row undoes.
    """
    if role == Role.PLATFORM_ADMIN:
        return False
    if actor.role in (Role.OWNER, Role.PLATFORM_ADMIN):
        return True
    if actor.role == Role.ADMIN:
        return role == Role.CASHIER
    return False
