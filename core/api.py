"""The django-ninja API.

Two shape rules bind every stage, because one typed client is generated from one
schema (§9): every application path carries the `/api/` prefix and every segment
is English; and a settings group is read with `GET /api/settings/{group}` and
written with `PATCH /api/settings/{group}` against the **pinned** tenant, never a
tenant addressed by id.
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from django.conf import settings as django_settings
from django.contrib.auth import login as django_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import NinjaAPI, Query, Schema
from ninja.errors import HttpError
from pydantic import field_validator

from core import audit, invitations as invitation_service, scoping, tenant_settings
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.middleware import SESSION_TENANT_KEY, SESSION_USER_KEY, request_id
from core.models import (
    AuditAction,
    AuditLog,
    Invitation,
    InvitationStatus,
    Location,
    Role,
    Tenant,
    User,
    UserStatus,
)
from core.permissions import (
    any_member,
    may_invite_at,
    owner_only,
    owner_or_admin,
    public,
    signed_in,
)
from core.tasks import enqueue_invitation_email
from core.tenancy import repin

logger = logging.getLogger(__name__)

api = NinjaAPI(
    title="Botica",
    version="1",
    description="La plataforma operativa para droguerías colombianas.",
    urls_namespace="botica",
    openapi_url="/openapi.json",
    docs_url=None,
)


@api.exception_handler(scoping.Misconfigured)
def _misconfigured(request, exc):
    """A cashier with no home sede is an error, not an empty list.

    If this defaulted, a cashier would silently see every sede's till -- and it
    would present as a UI bug rather than as an error.
    """
    return api.create_response(request, {"detail": str(exc)}, status=409)


@api.exception_handler(tenant_settings.UnknownTenant)
def _unknown_tenant(request, exc):
    logger.error("settings write matched no row: %s", exc)
    return api.create_response(
        request,
        {
            "detail": "No pudimos guardar los ajustes de la droguería. Recargue "
            "la página e intente de nuevo."
        },
        status=409,
    )


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

TenantStatusValue = Literal["active", "suspended"]
UserStatusValue = Literal["active", "suspended"]
LocationStatusValue = Literal["active", "closed"]
InvitationStatusValue = Literal["pending", "accepted", "revoked"]
InvitationStateValue = Literal[
    "pending", "accepted", "revoked", "expired", "delivery_failed"
]
RoleValue = Literal["platform_admin", "owner", "admin", "cashier"]
LocationTypeValue = Literal["store", "warehouse", "distribution_center"]
SortOrder = Literal["asc", "desc"]


class TenantOut(Schema):
    id: UUID
    name: str
    slug: str
    status: TenantStatusValue


class LocationOut(Schema):
    id: UUID
    code: str
    name: str
    type: LocationTypeValue
    city: str
    status: LocationStatusValue


class MeOut(Schema):
    """What the shell gates its nav on (§2, §B.8.3)."""

    id: UUID
    name: str
    email: str
    role: RoleValue
    platform_admin: bool
    tenant: TenantOut | None
    location_id: UUID | None
    location_name: str | None
    readable_location_ids: list[UUID]
    app_version: str
    admin_console: bool


class NavCountersOut(Schema):
    """The work-waiting count per nav item, as a map of route key to count and
    severity (§B.8.2). Coined, and it exists so that seven stages do not each
    invent their own counter fetch. Empty at S0 -- and zero renders nothing."""

    counters: dict[str, int]
    critical: list[str]


class UserOut(Schema):
    id: UUID
    name: str
    email: str
    role: RoleValue
    location_id: UUID | None
    location_name: str | None
    status: UserStatusValue
    last_login_at: datetime | None


class UserPatchIn(Schema):
    role: RoleValue | None = None
    location_id: UUID | None = None
    status: UserStatusValue | None = None
    #: Nullable rather than defaulted, so the generated client type makes it
    #: optional: a field with a default and no null is *required* in the
    #: typed body, and every call site would have to say `false`.
    clear_location: bool | None = None


class InvitationOut(Schema):
    id: UUID
    email: str
    role: RoleValue
    location_id: UUID | None
    location_name: str | None
    status: InvitationStatusValue
    state: InvitationStateValue
    expires_at: datetime
    invited_by_name: str | None
    created_at: datetime


class InvitationIn(Schema):
    email: str
    role: RoleValue
    location_id: UUID | None = None

    @field_validator("email")
    @classmethod
    def _address(cls, value):
        """Django's own validator rather than a second email dependency."""
        address = (value or "").strip().lower()
        try:
            validate_email(address)
        except ValidationError as error:
            raise ValueError("Escriba un correo electrónico válido.") from error
        return address


class TokenIn(Schema):
    """The token travels in a request body or a URL fragment. Never in a path."""

    token: str


class AcceptIn(TokenIn):
    name: str
    password: str


class InvitationPreviewOut(Schema):
    tenant_name: str
    email: str
    role: RoleValue
    location_name: str | None
    expires_at: datetime


class TenantSettingsOut(Schema):
    name: str
    slug: str
    nit: str
    status: TenantStatusValue
    legal_name: str
    timezone: str
    currency: str
    number_format: str
    app_version: str


class TenantSettingsIn(Schema):
    """`slug` and `status` are absent by design: a slug that changes breaks every
    saved link, and a network able to suspend itself would have no way back."""

    name: str
    nit: str
    legal_name: str
    timezone: str


class AuditRowOut(Schema):
    id: UUID
    created_at: datetime
    actor_email: str
    actor_name: str | None
    action: str
    entity_type: str
    entity_id: UUID | None
    before: dict | None
    after: dict | None
    request_id: str


# ---------------------------------------------------------------------------
# Identity and the shell
# ---------------------------------------------------------------------------


def _tenant(request):
    tenant = Tenant.objects.filter(id=request.tenant_id).first()
    if tenant is None:
        raise HttpError(403, "No hay una droguería seleccionada en esta sesión.")
    return tenant


@api.get("/me", response=MeOut, auth=signed_in, url_name="me")
def me(request):
    """The acting identity.

    A `platform_admin`'s identity comes from the session rather than from a read
    under the pin -- their `users` row carries a null `tenant_id` and belongs to
    no network, so reading it under the pin would 404 them on their own identity
    the instant they selected a tenant.
    """
    from core.middleware import admin_console_reachable

    user = request.user
    tenant = Tenant.objects.filter(id=request.tenant_id).first()
    locations: list[UUID] = []
    if tenant is not None:
        try:
            locations = scoping.readable_locations(user, tenant.id)
        except scoping.Misconfigured:
            locations = []
    home = Location.objects.filter(id=user.location_id).first()
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "platform_admin": user.platform_admin,
        "tenant": tenant,
        "location_id": user.location_id,
        "location_name": home.name if home else None,
        "readable_location_ids": locations,
        "app_version": django_settings.BOTICA_VERSION,
        "admin_console": admin_console_reachable(request),
    }


@api.get("/nav-counters", response=NavCountersOut, auth=any_member)
def nav_counters(request):
    """§B.8.2 · the nav's counters. A stage adds its key here rather than
    fetching its own count.

    **`counter` is ventas abiertas, and only for an office identity.** A
    `cashier` reads the same number from their own local store at zero latency
    and never asks the server for it -- the till is the read model that knows,
    and a nav counter that needed the network would be the one number on a till
    surface that stops working when the cable comes out (§4, A4).
    """
    from core.counter.sales import open_sales
    from core.purchasing.api import suggested_order_count

    counters: dict[str, int] = {}
    if request.user.role != Role.CASHIER:
        try:
            readable = scoping.readable_locations(request.user, request.tenant_id)
            counters["counter"] = open_sales(request.tenant_id, readable)
            # S6 · **órdenes sugeridas, which is work waiting.** Never a total
            # of every order, and zero renders nothing at all (§B.8.2).
            counters["purchasing"] = suggested_order_count(request.tenant_id, readable)
        except scoping.Misconfigured:
            counters = {}
    return {"counters": counters, "critical": []}


@api.get("/locations", response=list[LocationOut], auth=any_member)
def list_locations(request):
    """The network's **sedes**. Read by the shell, by the scoping filter every
    later grid carries, and by the roster's sede select."""
    return list(Location.objects.filter(tenant_id=request.tenant_id).order_by("name"))


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def _location_for(role, location_id, tenant_id):
    if role == Role.CASHIER:
        if location_id is None:
            raise HttpError(422, "Una cuenta de mostrador necesita una sede asignada.")
        location = Location.objects.filter(id=location_id, tenant_id=tenant_id).first()
        if location is None:
            raise HttpError(422, "Esa sede no existe en esta droguería.")
        return location
    if location_id is not None:
        raise HttpError(
            422,
            "Los perfiles de oficina ven toda la red y no llevan sede asignada.",
        )
    return None


@api.get("/invitations", response=list[InvitationOut], auth=owner_or_admin)
def list_invitations(request):
    """Outstanding and recently resolved invitations for the pinned tenant."""
    rows = (
        Invitation.objects.filter(tenant_id=request.tenant_id)
        .select_related("location", "invited_by")
        .order_by("-created_at")
    )
    return [_invitation_out(row) for row in rows]


def _invitation_out(row):
    return {
        "id": row.id,
        "email": row.email,
        "role": row.role,
        "location_id": row.location_id,
        "location_name": row.location.name if row.location_id else None,
        "status": row.status,
        "state": row.state,
        "expires_at": row.expires_at,
        "invited_by_name": row.invited_by.name if row.invited_by_id else None,
        "created_at": row.created_at,
    }


@api.post("/invitations", response=InvitationOut, auth=owner_or_admin)
def create_invitation(request, payload: InvitationIn):
    """Invite an address at a role, with a `location_id` when the role is
    `cashier`. An `admin` may invite at `cashier` only."""
    if not may_invite_at(request.user, payload.role):
        raise HttpError(
            403,
            "Una administradora puede invitar solo al perfil Mostrador. Pida a "
            "la propietaria que invite a los demás perfiles.",
        )
    location = _location_for(payload.role, payload.location_id, request.tenant_id)
    if User.objects.filter(tenant_id=request.tenant_id, email=payload.email).exists():
        raise HttpError(409, "Ya hay una persona con ese correo en esta droguería.")

    invitation = Invitation(
        tenant_id=request.tenant_id,
        email=payload.email,
        role=payload.role,
        location=location,
        invited_by=request.user,
        expires_at=invitation_service.default_expiry(),
    )
    # The token is derived from the row's own id, so the id has to exist first
    # and the hash is written in the same statement that creates the row.
    invitation.id = uuid4()
    token = invitation_service.token_for(invitation)
    invitation.token_hash = invitation_service.hash_token(token)
    try:
        invitation.save()
    except IntegrityError as error:
        raise HttpError(
            409, "Ya hay una invitación pendiente para ese correo."
        ) from error

    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="invitations",
        entity_id=invitation.id,
        after=audit.snapshot(invitation, ["email", "role", "location", "status"]),
        request_id=request_id.get(),
    )
    enqueue_invitation_email(invitation, token)
    return _invitation_out(invitation)


@api.post("/invitations/preview", response=InvitationPreviewOut, auth=public)
def preview_invitation(request, payload: TokenIn):
    """What a token names, so the accept screen can render before a password
    exists. A POST for what is logically a read, because the lookup key is a
    credential and a path segment is written into every access log."""
    with _pinned_to_token(payload.token) as invitation:
        return {
            "tenant_name": invitation.tenant.name,
            "email": invitation.email,
            "role": invitation.role,
            "location_name": (
                invitation.location.name if invitation.location_id else None
            ),
            "expires_at": invitation.expires_at,
        }


class AcceptOut(Schema):
    landing: str


@api.post("/invitations/accept", response=AcceptOut, auth=public)
def accept_invitation(request, payload: AcceptIn):
    """Consume the invitation and create the `users` row (architecture §3)."""
    with _pinned_to_token(payload.token) as invitation:
        name = payload.name.strip()
        if not name:
            raise HttpError(422, "Escriba su nombre.")
        try:
            validate_password(payload.password)
        except ValidationError as error:
            raise HttpError(422, " ".join(error.messages)) from error

        user = User(
            tenant_id=invitation.tenant_id,
            email=invitation.email,
            name=name,
            role=invitation.role,
            location_id=invitation.location_id,
            status=UserStatus.ACTIVE,
        )
        user.set_password(payload.password)
        try:
            user.save()
        except IntegrityError as error:
            raise HttpError(
                409, "Ya hay una persona con ese correo en esta droguería."
            ) from error

        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["status", "accepted_at", "updated_at"])

        # The actor is the invitee: this is the one create in the product a
        # person performs on themselves.
        audit.record(
            actor=user,
            tenant_id=invitation.tenant_id,
            action=AuditAction.CREATE,
            entity_type="users",
            entity_id=user.id,
            after=audit.snapshot(user, ["email", "name", "role", "location"]),
            request_id=request_id.get(),
        )

        django_login(request, user, backend=django_settings.AUTHENTICATION_BACKENDS[0])
        request.session[SESSION_TENANT_KEY] = str(user.tenant_id)
        request.session[SESSION_USER_KEY] = str(user.id)
        return {"landing": "/counter" if user.role == Role.CASHIER else "/dashboard"}


@contextmanager
def _pinned_to_token(token):
    """Pin to the tenant the token names, then read the invitation under it.

    The accept flow is anonymous and has no session to resolve a tenant from, so
    the token carries its own -- which is what lets this path **pin first and
    read second** rather than opening a second unpinned lookup beside sign-in.
    A forged tenant finds no invitation under that pin and is refused in the same
    words as any other unrecognised link.
    """
    claimed = invitation_service.parts(token)
    if claimed is None:
        raise HttpError(404, "No reconocemos esta invitación.")
    repin(claimed[0])
    yield _consumable(invitation_service.find_by_token(token))


def _consumable(invitation):
    """The invitation, or the refusal the accept screen renders."""
    try:
        return invitation_service.check_consumable(invitation)
    except invitation_service.InvitationRefused as refusal:
        raise HttpError(
            404 if refusal.reason == "unknown" else 409, str(refusal)
        ) from refusal


def _reissuable(invitation):
    """An invitation a resend may renew: anything still outstanding.

    Accepted and revoked are terminal; expiry is not, and refusing to renew it
    would leave an administradora with nothing to do about the very message the
    accept screen sends them.
    """
    if invitation.status != InvitationStatus.PENDING:
        raise HttpError(409, "Esta invitación ya no está pendiente.")
    return invitation


@api.post(
    "/invitations/{invitation_id}/resend", response=InvitationOut, auth=owner_or_admin
)
def resend_invitation(request, invitation_id: UUID):
    """Re-send the same token. It does not rotate it -- a link already sent by
    another channel keeps working."""
    invitation = get_object_or_404(
        Invitation, id=invitation_id, tenant_id=request.tenant_id
    )
    if not may_invite_at(request.user, invitation.role):
        raise HttpError(
            403, "Solo la propietaria puede reenviar invitaciones de este perfil."
        )
    _reissuable(invitation)

    # The same token, re-derived rather than rotated: a link an owner already
    # sent over another channel keeps working. What a resend *does* change is
    # the clock -- this is the one control an administradora has when someone
    # is told "pida un enlace nuevo", so it has to renew an expired invitation
    # rather than refuse it.
    token = invitation_service.token_for(invitation)
    invitation.expires_at = invitation_service.default_expiry()
    invitation.last_delivery_error = ""
    invitation.save(update_fields=["expires_at", "last_delivery_error", "updated_at"])

    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.SEND,
        entity_type="invitations",
        entity_id=invitation.id,
        before={"email": invitation.email},
        after={"email": invitation.email, "resent_at": timezone.now().isoformat()},
        request_id=request_id.get(),
    )
    enqueue_invitation_email(invitation, token)
    return _invitation_out(invitation)


class InvitationLinkOut(Schema):
    accept_url: str


@api.post(
    "/invitations/{invitation_id}/link", response=InvitationLinkOut, auth=owner_or_admin
)
def invitation_link(request, invitation_id: UUID):
    """`Copiar enlace`. Five failed deliveries are a channel failure and not an
    invalid invitation, so an owner sends the link over WhatsApp instead."""
    invitation = get_object_or_404(
        Invitation, id=invitation_id, tenant_id=request.tenant_id
    )
    if not may_invite_at(request.user, invitation.role):
        raise HttpError(403, "Solo la propietaria puede copiar enlaces de este perfil.")
    _consumable(invitation)

    token = invitation_service.token_for(invitation)
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.SEND,
        entity_type="invitations",
        entity_id=invitation.id,
        before={"email": invitation.email},
        after={"email": invitation.email, "copied_at": timezone.now().isoformat()},
        request_id=request_id.get(),
    )
    return {"accept_url": invitation.accept_url(token)}


@api.delete("/invitations/{invitation_id}", response=InvitationOut, auth=owner_only)
def revoke_invitation(request, invitation_id: UUID):
    """Revoke an outstanding invitation. The row stays, at `revoked`."""
    invitation = get_object_or_404(
        Invitation, id=invitation_id, tenant_id=request.tenant_id
    )
    if invitation.status != InvitationStatus.PENDING:
        raise HttpError(409, "Esta invitación ya no está pendiente.")
    before = audit.snapshot(invitation, ["email", "role", "status"])
    invitation.status = InvitationStatus.REVOKED
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["status", "revoked_at", "updated_at"])
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.REVOKE,
        entity_type="invitations",
        entity_id=invitation.id,
        before=before,
        after=audit.snapshot(invitation, ["email", "role", "status"]),
        request_id=request_id.get(),
    )
    return _invitation_out(invitation)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@api.get("/users", response=Page[UserOut], auth=owner_or_admin)
def list_users(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
):
    """The roster. The second endpoint on the grid contract."""
    queryset = (
        User.objects.filter(tenant_id=request.tenant_id)
        .select_related("location")
        .order_by("name")
    )
    rows, row_count, page, page_size = paginate(
        queryset,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable={
            "name": ["name"],
            "email": ["email"],
            "role": ["role"],
            "status": ["status"],
            "last_login": ["last_login"],
        },
    )
    return {
        "rows": [_user_out(row) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _user_out(row):
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "role": row.role,
        "location_id": row.location_id,
        "location_name": row.location.name if row.location_id else None,
        "status": row.status,
        "last_login_at": row.last_login,
    }


@api.patch("/users/{user_id}", response=UserOut, auth=owner_or_admin)
def patch_user(request, user_id: UUID, payload: UserPatchIn):
    """Change status, home sede, or role. **Role change is `owner` only** (§2)."""
    person = get_object_or_404(User, id=user_id, tenant_id=request.tenant_id)
    if payload.role is not None and request.user.role != Role.OWNER:
        raise HttpError(
            403, "Cambiar el perfil de una persona requiere el perfil Propietaria."
        )
    if payload.role == Role.PLATFORM_ADMIN:
        raise HttpError(422, "El perfil Plataforma no se asigna desde la droguería.")

    before = audit.snapshot(person, ["role", "status", "location"])
    role = payload.role or person.role
    if payload.clear_location:
        location = None
    elif payload.location_id is not None:
        location = Location.objects.filter(
            id=payload.location_id, tenant_id=request.tenant_id
        ).first()
        if location is None:
            raise HttpError(422, "Esa sede no existe en esta droguería.")
    else:
        location = person.location

    if role == Role.CASHIER and location is None:
        raise HttpError(422, "Una cuenta de mostrador necesita una sede asignada.")
    if role in (Role.OWNER, Role.ADMIN):
        location = None

    person.role = role
    person.location = location
    if payload.status is not None:
        person.status = payload.status
    person.save(update_fields=["role", "location", "status", "updated_at"])

    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="users",
        entity_id=person.id,
        before=before,
        after=audit.snapshot(person, ["role", "status", "location"]),
        request_id=request_id.get(),
    )
    person.refresh_from_db()
    return _user_out(person)


@api.delete("/users/{user_id}", auth=owner_only)
def delete_user(request, user_id: UUID):
    """Hard delete. "Delete" means hard delete (§2).

    Every stage referencing `users` does so `ON DELETE SET NULL` and stamps the
    human-readable identity it needs at write time -- `audit_log.actor_email` is
    S0's instance of the pattern.
    """
    person = get_object_or_404(User, id=user_id, tenant_id=request.tenant_id)
    if person.id == request.user.id:
        raise HttpError(409, "No puede eliminar su propia cuenta.")
    before = audit.snapshot(person, ["email", "name", "role", "status", "location"])
    person.delete()
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.DELETE,
        entity_type="users",
        entity_id=user_id,
        before=before,
        request_id=request_id.get(),
    )
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@api.get("/settings/tenant", response=TenantSettingsOut, auth=owner_or_admin)
def read_tenant_settings(request):
    tenant = _tenant(request)
    group = tenant_settings.read_group(tenant, "tenant")
    return {
        "name": tenant.name,
        "slug": tenant.slug,
        "nit": tenant.nit,
        "status": tenant.status,
        "legal_name": group.get("legal_name", ""),
        "timezone": group.get("timezone", "America/Bogota"),
        "currency": group.get("currency", "COP"),
        "number_format": group.get("number_format", "es-CO"),
        "app_version": django_settings.BOTICA_VERSION,
    }


@api.patch("/settings/tenant", response=TenantSettingsOut, auth=owner_or_admin)
def write_tenant_settings(request, payload: TenantSettingsIn):
    """Edit the network's identity. No tenant id appears in the path: the tenant
    is pinned per request, so the write always targets the current one."""
    tenant = _tenant(request)
    before = {
        "name": tenant.name,
        "nit": tenant.nit,
        **tenant_settings.read_group(tenant, "tenant"),
    }

    tenant.name = payload.name.strip()
    tenant.nit = payload.nit.strip()
    tenant.save(update_fields=["name", "nit", "updated_at"])

    group = {
        **tenant_settings.read_group(tenant, "tenant"),
        "legal_name": payload.legal_name.strip(),
        "timezone": payload.timezone.strip() or "America/Bogota",
    }
    group.setdefault("currency", "COP")
    group.setdefault("number_format", "es-CO")
    tenant_settings.write_group(tenant, "tenant", group)

    audit.record(
        actor=request.user,
        tenant_id=tenant.id,
        action=AuditAction.UPDATE,
        entity_type="tenants",
        entity_id=tenant.id,
        before=before,
        after={"name": tenant.name, "nit": tenant.nit, **group},
        request_id=request_id.get(),
    )
    return read_tenant_settings(request)


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


@api.get("/audit-log", response=Page[AuditRowOut], auth=owner_or_admin)
def read_audit_log(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
    action: str | None = Query(None),
    entity_type: str | None = Query(None),
):
    """The append-only mutation trail, server-paginated. The grid contract's
    first real consumer. S10's provisioning and impersonation entries land in the
    same table and read out here; S10 does not re-expose this endpoint."""
    queryset = AuditLog.objects.filter(tenant_id=request.tenant_id).order_by(
        "-created_at", "-id"
    )
    if action:
        queryset = queryset.filter(action=action)
    if entity_type:
        queryset = queryset.filter(entity_type=entity_type)

    rows, row_count, page, page_size = paginate(
        queryset,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable={
            "created_at": ["created_at", "id"],
            "actor": ["actor_email"],
            "action": ["action"],
            "entity_type": ["entity_type"],
        },
    )
    # One lookup for the whole page rather than a join, because the actor may
    # have been hard deleted and the row still has to read.
    names = dict(
        User.objects.filter(
            id__in=[row.actor_user_id for row in rows if row.actor_user_id]
        ).values_list("id", "name")
    )
    return {
        "rows": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "actor_email": row.actor_email,
                "actor_name": names.get(row.actor_user_id),
                "action": row.action,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "before": row.before,
                "after": row.after,
                "request_id": row.request_id,
            }
            for row in rows
        ],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# S1 · the catalog
#
# One `NinjaAPI` and one generated client, so a stage adds a `Router` rather
# than a second schema. The prefix is empty because every path in that module
# already reads `/items`, `/customers`, `/imports` -- the `/api/` half is the
# mount in `botica/urls.py` and belongs to nobody's router.
# ---------------------------------------------------------------------------

from core.catalog.api import router as catalog_router  # noqa: E402

api.add_router("", catalog_router)


# ---------------------------------------------------------------------------
# S2 · sync
#
# The same rule one stage on: a Router, not a second schema. Its paths already
# read `/sync/...`, `/devices/...` and `/settings/sync`, so the prefix is empty
# here too. **Every line of sync code it calls lives behind `core/sync/`** (§5)
# -- this is the mount and nothing else.
# ---------------------------------------------------------------------------

from core.sync.api import router as sync_router  # noqa: E402

api.add_router("", sync_router)


# ---------------------------------------------------------------------------
# S3 · inventory
#
# The same rule again: a Router, not a second schema. Its paths already read
# `/stock`, `/stock-moves`, `/lots`, `/receipts`, `/transfers`, `/stock-counts`,
# `/stock-policies` and `/settings/inventory`, so the prefix is empty here too.
#
# Importing `core.inventory.api` pulls in the package, which registers this
# stage's two push writers with S2's endpoint -- so a receipt line arriving from
# a till that was offline goes through the ledger service and not around it.
# ---------------------------------------------------------------------------

from core.inventory.api import LineRefused, router as inventory_router  # noqa: E402

api.add_router("", inventory_router)


# ---------------------------------------------------------------------------
# S4 · the counter
#
# The same rule again: a Router, not a second schema. Its paths already read
# `/sales`, `/sale-returns` and `/shifts`, so the prefix is empty here too.
#
# Importing `core.counter.api` pulls in the package, which registers this
# stage's six push writers with S2's endpoint -- so a sale rung up on a till
# that was offline goes through S3's ledger service and not around it.
# ---------------------------------------------------------------------------

from core.counter.api import router as counter_router  # noqa: E402

api.add_router("", counter_router)


# ---------------------------------------------------------------------------
# S5 · the handoff
#
# The same rule again: a Router, not a second schema. Its paths already read
# `/fiscal-documents`, `/sales/{id}/canonical-document`,
# `/sales/{id}/fiscal-document` and `/settings/invoicing`, so the prefix is
# empty here too.
#
# **No endpoint here is one a till calls**, and there is no unauthenticated
# inbound path at all: a target that reports asynchronously is polled through
# its mapping's query operation rather than calling us back (S5, ledger rule 6).
# ---------------------------------------------------------------------------

from core.fiscal.api import router as fiscal_router  # noqa: E402

api.add_router("", fiscal_router)


# ---------------------------------------------------------------------------
# S6 · purchasing
#
# The same rule again: a Router, not a second schema. Its paths already read
# `/purchase-orders`, `/goods-receipts`, `/demand-forecasts` and
# `/settings/purchasing`, so the prefix is empty here too.
#
# **No endpoint here is one a till calls, and no purchasing table reaches a
# device.** Compras is the office read model, served over the network per view,
# so S2's sync registry is not amended by this stage (rule 9, §4, A4).
# ---------------------------------------------------------------------------

from core.purchasing.api import router as purchasing_router  # noqa: E402

api.add_router("", purchasing_router)


# ---------------------------------------------------------------------------
# S7 · pricing
#
# The same rule again: a Router, not a second schema. Its paths already read
# `/pricing/items`, `/pricing/summary`, `/pricing/adoption`, `/pricing/caps` and
# `/settings/pricing`, so the prefix is empty here too.
#
# **Not one route on this router writes a price** (A11). There is no approve, no
# apply, no revert, no dismiss and no batch under `/api/pricing/` -- every one of
# those returns 404 rather than 403, because a 403 is a route standing behind a
# policy somebody can change, and the amendment replaced that with a property of
# the schema. A suggestion becomes a price in S1's editor, which is where the
# resolution is stamped.
# ---------------------------------------------------------------------------

from core.pricing.api import router as pricing_router  # noqa: E402

api.add_router("", pricing_router)


# ---------------------------------------------------------------------------
# S8 · the assistant
#
# The same rule again: a Router, not a second schema. Its paths already read
# `/assistant/...`, `/item-warnings`, `/cross-sell-rules` and
# `/settings/assistant`, so the prefix is empty here too.
#
# Importing `core.assistant.api` pulls in the package, which registers this
# stage's two push writers with S2's endpoint -- so an offer written during a
# blackout and the acceptance queued behind it go through this stage's own
# service and not around it.
#
# **Not one route on this router puts a product on a card.** The chips, the
# filter, the ranking and the type derivation all run on the device before
# `POST /api/assistant/queries` is called, which is what makes them identical
# online and offline (A8) -- and the one thing this router adds, the
# recommendation's prose, is checked on the way out and discarded rather than
# rendered when it fails.
# ---------------------------------------------------------------------------

from core.assistant.api import router as assistant_router  # noqa: E402

api.add_router("", assistant_router)


@api.exception_handler(LineRefused)
def _line_refused(request, exc):
    """One refused line of a multi-line entry, at field scope (§B.10.3).

    The body is the ordinary `detail` every other refusal carries, plus the two
    facts that let a surface put the message on the control instead of at the
    foot of the page. A client that ignores them still renders a correct
    region-scope error, which is what keeps this additive.
    """
    return api.create_response(
        request,
        {"detail": exc.detail, "line": exc.line, "field": exc.field},
        status=422,
    )
