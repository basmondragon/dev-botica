"""S2's thirteen endpoints, all behind S0's single permission dependency and
inside the pinned transaction (§2, A1, rule 6).

Four of them are the till's -- registry, pull, push, digest -- and every one
carries the session **and** `X-Botica-Device-Key`. The session resolves the
tenant and the identity; the key resolves the device and therefore the location.
**The location is never a request parameter**, and there is no parameter on any
path here that changes which sede a device reads: a parameter that exists is a
parameter somebody eventually wires to the predicate.

The other nine are the office's: the device list and its record panel, the three
lifecycle mutations, the conflict queue and its resolution, and the `sync`
settings group.
"""

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit, scoping
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.middleware import request_id
from core.models import (
    AuditAction,
    Device,
    DeviceStatus,
    Location,
    Role,
    SyncConflict,
    SyncConflictStatus,
    SyncConflictType,
    Tenant,
)
from core.permissions import any_member, owner_or_admin
from core.sync import (
    conflicts as conflict_service,
    devices as device_service,
    digest as digest_service,
    pull as pull_service,
    push as push_service,
    registry,
    settings as sync_settings,
)
from core.tenancy import ForeignTenantRow, pinned_batch

logger = logging.getLogger(__name__)

router = Router()

SortOrder = Literal["asc", "desc"]
DeviceStatusValue = Literal["active", "revoked"]
ConflictStatusValue = Literal["open", "resolved", "dismissed"]
StoragePolicyValue = Literal["warn", "required"]


def _tenant(request):
    tenant = Tenant.objects.filter(id=request.tenant_id).first()
    if tenant is None:
        raise HttpError(403, "No hay una droguería seleccionada en esta sesión.")
    return tenant


def _options(request):
    return sync_settings.read(_tenant(request))


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class CheckpointOut(Schema):
    updated_at: str
    id: str


class CollectionOut(Schema):
    """One registry row, as a device reads it."""

    name: str
    scope: Literal["tenant", "location"]
    push: bool
    natural_key: list[str] | None
    #: What this device will actually receive, counted now -- not the pilot
    #: estimate. The first-sync card counts against these.
    rows: int


class RegistryOut(Schema):
    version: int
    device_id: UUID
    device_label: str
    device_code: str
    location_id: UUID
    location_name: str
    location_code: str
    collections: list[CollectionOut]
    server_time: datetime
    clock_skew_ms: int | None
    storage_persisted: bool | None
    storage_persistence_policy: StoragePolicyValue
    pull_interval_seconds: int
    pull_page_size: int
    push_batch_max_rows: int
    push_batch_max_bytes: int
    local_retention_days: int
    clock_skew_warn_seconds: int


class PullOut(Schema):
    documents: list[dict]
    checkpoint: CheckpointOut | None
    has_more: bool
    registry_version: int
    server_time: datetime
    #: Echoed so a client can tell that its sede changed and reset its
    #: location-scoped collections without being told to re-claim.
    location_id: UUID


class PushRowIn(Schema):
    collection: str
    client_uuid: str
    occurred_at: datetime | None = None
    payload: dict


class PushIn(Schema):
    batch_id: str
    client_time: datetime | None = None
    rows: list[PushRowIn]


class PushRowOut(Schema):
    client_uuid: str
    outcome: Literal["applied", "duplicate", "merged", "rejected"]
    id: str | None = None
    reason: str = ""


class PushOut(Schema):
    batch_id: str
    #: `rejected` means **no row in this batch was applied** -- the whole-batch
    #: half of the rejection rule. Every entry in `results` then reads
    #: `rejected` too, so a client that only looks at rows still drains
    #: correctly and still surfaces the refusal.
    batch_outcome: Literal["applied", "rejected"]
    batch_reason: str = ""
    results: list[PushRowOut]
    server_time: datetime


class DigestCollectionOut(Schema):
    count: int
    checksum: str


class DigestOut(Schema):
    collections: dict[str, DigestCollectionOut]
    registry_version: int
    server_time: datetime


class ClaimIn(Schema):
    label: str
    location_id: UUID


class DeviceOut(Schema):
    id: UUID
    label: str
    #: The short code `sales.number` is composed from at S4.
    code: str
    location_id: UUID
    location_name: str
    status: DeviceStatusValue
    last_seen_at: datetime | None
    last_synced_at: datetime | None
    last_pushed_at: datetime | None
    clock_skew_ms: int | None
    storage_persisted: bool | None
    app_version: str
    enrolled_at: datetime
    enrolled_by_name: str | None
    revoked_at: datetime | None
    open_conflicts: int


class ClaimOut(Schema):
    device: DeviceOut
    #: **Returned once and never again.** Losing it costs one re-claim and one
    #: first sync and no data -- the outbox is drained before a re-claim.
    device_key: str
    registry_version: int


class DevicePatchIn(Schema):
    label: str | None = None
    location_id: UUID | None = None


class ConflictOut(Schema):
    id: UUID
    device_id: UUID | None
    device_label: str | None
    location_id: UUID
    location_name: str
    collection: str
    client_uuid: UUID | None
    type: str
    detail: dict
    status: ConflictStatusValue
    occurred_at: datetime | None
    recorded_at: datetime
    resolved_at: datetime | None
    resolved_by_name: str | None
    resolution_note: str


class ConflictPatchIn(Schema):
    status: Literal["resolved", "dismissed"]
    note: str = ""


class SyncSettingsOut(Schema):
    pull_interval_seconds: int
    pull_page_size: int
    push_batch_max_rows: int
    push_batch_max_bytes: int
    pull_safety_horizon_seconds: int
    local_retention_days: int
    clock_skew_warn_seconds: int
    customer_recency_months: int
    storage_persistence_policy: StoragePolicyValue
    stale_device_hours: int


class SyncSettingsIn(Schema):
    pull_interval_seconds: int
    pull_page_size: int
    push_batch_max_rows: int
    push_batch_max_bytes: int
    pull_safety_horizon_seconds: int
    local_retention_days: int
    clock_skew_warn_seconds: int
    customer_recency_months: int
    storage_persistence_policy: StoragePolicyValue
    stale_device_hours: int


# ---------------------------------------------------------------------------
# The till: registry, pull, push, digest
# ---------------------------------------------------------------------------


@router.get("/sync/registry", response=RegistryOut, auth=any_member)
def sync_registry(request):
    """What this device replicates, and how much of each there is.

    The counts are read for **this** device rather than estimated, so the
    first-sync card counts against real totals and a stranger watching it cannot
    tell which figures came from the product and which from a fixture.
    """
    device = device_service.resolve(request)
    options = _options(request)
    now = device_service.touch(device, request)
    totals = registry.totals(device.tenant_id, device.location_id, options)
    location = device.location
    return {
        "version": registry.REGISTRY_VERSION,
        "device_id": device.id,
        "device_label": device.label,
        "device_code": device.code,
        "location_id": device.location_id,
        "location_name": location.name,
        "location_code": location.code,
        "collections": [
            {
                "name": one.name,
                "scope": one.scope,
                "push": one.push,
                "natural_key": list(one.natural_key) if one.natural_key else None,
                "rows": totals[one.name],
            }
            for one in registry.COLLECTIONS
        ],
        "server_time": now,
        "clock_skew_ms": device.clock_skew_ms,
        "storage_persisted": device.storage_persisted,
        "storage_persistence_policy": options["storage_persistence_policy"],
        "pull_interval_seconds": options["pull_interval_seconds"],
        "pull_page_size": options["pull_page_size"],
        "push_batch_max_rows": options["push_batch_max_rows"],
        "push_batch_max_bytes": options["push_batch_max_bytes"],
        "local_retention_days": options["local_retention_days"],
        "clock_skew_warn_seconds": options["clock_skew_warn_seconds"],
    }


@router.get("/sync/pull", response=PullOut, auth=any_member)
def sync_pull(
    request,
    collection: str = Query(...),
    updated_at: str | None = Query(None),
    id: str | None = Query(None),
    limit: int | None = Query(None),
):
    """One collection's delta after a `(updated_at, id)` cursor.

    **This is the one endpoint in the product with a p95 budget in the
    architecture** -- under 20 ms server time with no changes pending (§4). It
    is a single indexed tuple scan that usually returns zero rows, and it is
    called by every till every `pull_interval_seconds`. Anything that makes it a
    join is a defect.

    There is no `location_id` parameter here, and adding one is the defect
    criterion 29 exists to catch.
    """
    device = device_service.resolve(request)
    options = _options(request)
    try:
        target = registry.get(collection)
    except LookupError as refusal:
        raise HttpError(
            422,
            f"«{collection}» no es una colección sincronizable. El registro "
            "declara: " + ", ".join(registry.BY_NAME),
        ) from refusal

    page_size = int(options["pull_page_size"])
    if limit is not None:
        page_size = max(1, min(int(limit), page_size))

    now = device_service.touch(device, request, pulled=True)
    documents, checkpoint, has_more = pull_service.page(
        target,
        tenant_id=device.tenant_id,
        location_id=device.location_id,
        cursor=pull_service.parse_cursor(updated_at, id),
        limit=page_size,
        options=options,
        now=now,
    )
    return {
        "documents": documents,
        "checkpoint": checkpoint,
        "has_more": has_more,
        "registry_version": registry.REGISTRY_VERSION,
        "server_time": now,
        "location_id": device.location_id,
    }


@router.post("/sync/push", response=PushOut, auth=any_member)
def sync_push(request, payload: PushIn):
    """Apply one batch in one pinned transaction and answer a per-row outcome.

    Rule 6's fourth context, and S2 is the first code to use it. The pin is
    S0's; this endpoint calls it and never issues its own `SET LOCAL`.
    """
    device = device_service.resolve(request)
    options = _options(request)
    rows = [row.dict() for row in payload.rows]

    try:
        push_service.check_size(rows, options)
    except push_service.BatchTooLarge as refusal:
        raise HttpError(413, str(refusal)) from refusal

    # **A whole-batch rejection answers 200 and says so in the payload, and that
    # is not a softened refusal.** S0's middleware rolls the request's
    # transaction back on any status at or above 400 -- which is right, and
    # which would take the conflict row with it. The office learning that a till
    # tried to write into another network is the entire point of the refusal, so
    # the refusal has to commit. A batch protocol answers outcomes anyway: this
    # is one more outcome, and `batch_outcome` carries it unambiguously.
    try:
        push_service.check_provenance(device, rows)
    except ForeignTenantRow:
        logger.warning("foreign-tenant push from device %s", device.id)
        return _batch_rejected(
            device,
            payload.batch_id,
            rows,
            SyncConflictType.FOREIGN_TENANT,
            "Este envío trae filas de otra droguería y se rechazó completo. "
            "Ninguna fila quedó aplicada.",
        )
    except push_service.ForeignLocationRow:
        logger.warning("foreign-location push from device %s", device.id)
        return _batch_rejected(
            device,
            payload.batch_id,
            rows,
            SyncConflictType.FOREIGN_LOCATION,
            "Este envío trae filas de otra sede y se rechazó completo. Ninguna "
            "fila quedó aplicada.",
        )

    # `pinned_batch` re-checks the tenant of every row inside the transaction it
    # opens. The check above runs first so the conflict row and the refusal
    # naming the location happen before anything is applied; this one is the
    # guarantee, and it belongs to S0.
    with pinned_batch(device.tenant_id, [row["payload"] for row in rows]):
        result = push_service.apply_batch(
            device,
            payload.batch_id,
            rows,
            options=options,
            request_id=request_id.get(),
        )
        now = device_service.touch(device, request, pushed=True)

    return {
        "batch_id": result.batch_id,
        "batch_outcome": "applied",
        "batch_reason": "",
        "results": [
            {
                "client_uuid": one.client_uuid,
                "outcome": one.outcome,
                "id": one.id,
                "reason": one.reason,
            }
            for one in result.outcomes
        ],
        "server_time": now,
    }


def _batch_rejected(device, batch_id, rows, type, message):
    """One conflict row for a whole-batch rejection, and the answer the till
    reads. The conflict names no payload -- the collection, the reason code, the
    batch and the correlation id, and nothing a person could be identified by.
    """
    conflict_service.raise_conflict(
        device=device,
        type=type,
        # `raise_conflict` bounds it; the value is a browser's.
        collection=(rows[0].get("collection") if rows else "") or "",
        occurred_at=None,
        detail={
            "reason": str(type),
            "batch_id": str(batch_id)[:64],
            "count": len(rows),
            "request_id": request_id.get(),
        },
    )
    return {
        "batch_id": batch_id,
        "batch_outcome": "rejected",
        "batch_reason": message,
        "results": [
            {
                "client_uuid": row.get("client_uuid") or "",
                "outcome": "rejected",
                "id": None,
                "reason": message,
            }
            for row in rows
        ],
        "server_time": timezone.now(),
    }


@router.get("/sync/digest", response=DigestOut, auth=any_member)
def sync_digest(request):
    """Per-collection row count and checksum at the horizon, for this device's
    predicate. The client's daily divergence check.

    Built on the same predicate as the pull, and answered from the device's own
    key for the same reason: this is the endpoint most likely to grow a location
    parameter later.
    """
    device = device_service.resolve(request)
    options = _options(request)
    now = device_service.touch(device, request)
    return {
        "collections": digest_service.build(
            tenant_id=device.tenant_id,
            location_id=device.location_id,
            cursor_limit=pull_service.horizon(options, now),
            options=options,
        ),
        "registry_version": registry.REGISTRY_VERSION,
        "server_time": now,
    }


# ---------------------------------------------------------------------------
# Devices: claim, list, relabel, move, revoke
# ---------------------------------------------------------------------------


def _device_out(row, open_conflicts=0):
    return {
        "id": row.id,
        "label": row.label,
        "code": row.code,
        "location_id": row.location_id,
        "location_name": row.location.name,
        "status": row.status,
        "last_seen_at": row.last_seen_at,
        "last_synced_at": row.last_synced_at,
        "last_pushed_at": row.last_pushed_at,
        "clock_skew_ms": row.clock_skew_ms,
        "storage_persisted": row.storage_persisted,
        "app_version": row.app_version,
        "enrolled_at": row.enrolled_at,
        "enrolled_by_name": (
            row.enrolled_by_user.name if row.enrolled_by_user_id else None
        ),
        "revoked_at": row.revoked_at,
        "open_conflicts": open_conflicts,
    }


@router.post("/devices/claim", response=ClaimOut, auth=any_member)
def claim_device(request, payload: ClaimIn):
    """Claim this browser as a device.

    A `cashier` may claim only their own home sede -- the sede field is not a
    control for them, so this is the guard against a pasted body rather than a
    refusal anyone will see. `owner` and `admin` may claim any sede, which is
    the one path by which an office identity enrols the browser it is sitting
    at (scope 2); they are never *offered* it (A4).
    """
    user = request.user
    label = (payload.label or "").strip()
    if not label:
        raise HttpError(422, "Escriba un nombre para este equipo, como «Caja 1».")

    location = Location.objects.filter(
        id=payload.location_id, tenant_id=request.tenant_id
    ).first()
    if location is None:
        raise HttpError(422, "Esa sede no existe en esta droguería.")
    if user.role == Role.CASHIER and user.location_id != location.id:
        raise HttpError(
            403,
            "Una cuenta de mostrador solo registra equipos en su propia sede.",
        )
    if user.role not in (Role.CASHIER, Role.OWNER, Role.ADMIN, Role.PLATFORM_ADMIN):
        raise HttpError(403, "Este perfil no registra equipos.")

    options = _options(request)
    # `required` refuses the claim outright: a network that has decided an
    # evictable till is not a till it will run needs the refusal at
    # installation, not at a counter.
    if options["storage_persistence_policy"] == "required":
        if device_service._persisted(request) is not True:
            raise HttpError(
                409,
                "Esta droguería exige almacenamiento protegido y este navegador "
                "no lo concedió. Use Chrome, permita el almacenamiento "
                "persistente del sitio y vuelva a intentarlo.",
            )

    key, key_hash = device_service.mint()
    device = Device(
        tenant_id=request.tenant_id,
        location=location,
        label=label[:60],
        code=device_service.allocate_code(request.tenant_id, location),
        device_key_hash=key_hash,
        status=DeviceStatus.ACTIVE,
        enrolled_by_user=user if user.role != Role.PLATFORM_ADMIN else None,
        enrolled_at=timezone.now(),
        storage_persisted=device_service._persisted(request),
        app_version=(request.headers.get("X-Botica-App-Version") or "").strip()[:32],
    )
    # Two claims in the same instant read the same set of codes and pick the
    # same one. The label collision is a person's mistake and is answered as
    # one; a code collision is a race between two browsers, and answering it
    # with "póngale otro nombre" would tell a cashier to fix something they did
    # not do. It is retried instead, on the codes the loser can now see.
    for attempt in range(4):
        try:
            device.save()
            break
        except IntegrityError as error:
            if "one_device_code_per_tenant" not in str(error) or attempt == 3:
                raise HttpError(
                    409,
                    f"Ya hay un equipo llamado «{label}» en {location.name}. "
                    "Póngale otro nombre a este.",
                ) from error
            device.code = device_service.allocate_code(request.tenant_id, location)

    audit.record(
        actor=user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="devices",
        entity_id=device.id,
        after=audit.snapshot(device, ["label", "location", "status"]),
        request_id=request_id.get(),
    )
    return {
        "device": _device_out(device),
        "device_key": key,
        "registry_version": registry.REGISTRY_VERSION,
    }


@router.get("/devices", response=Page[DeviceOut], auth=owner_or_admin)
def list_devices(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
    location_id: UUID | None = Query(None),
    status: DeviceStatusValue | None = Query(None),
):
    """The office list, server-paginated on the grid contract (§9).

    A device list that has only ever been opened at seven rows is a list nobody
    has tried at the size it is for.
    """
    allowed = scoping.readable_locations(
        request.user,
        request.tenant_id,
        requested=[location_id] if location_id else None,
    )
    rows = (
        Device.objects.filter(tenant_id=request.tenant_id, location_id__in=allowed)
        .select_related("location", "enrolled_by_user")
        .order_by("location__name", "label")
    )
    if status:
        rows = rows.filter(status=status)

    page_rows, row_count, page, page_size = paginate(
        rows,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable={
            "label": ["label"],
            "location": ["location__name", "label"],
            "last_synced_at": ["last_synced_at"],
            "app_version": ["app_version"],
            "status": ["status"],
        },
    )
    counts = conflict_service.open_counts(
        request.tenant_id, [row.id for row in page_rows]
    )
    return {
        "rows": [_device_out(row, counts.get(str(row.id), 0)) for row in page_rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


@router.get("/devices/{device_id}", response=DeviceOut, auth=owner_or_admin)
def read_device(request, device_id: UUID):
    device = get_object_or_404(
        Device.objects.select_related("location", "enrolled_by_user"),
        id=device_id,
        tenant_id=request.tenant_id,
    )
    counts = conflict_service.open_counts(request.tenant_id, [device.id])
    return _device_out(device, counts.get(str(device.id), 0))


@router.patch("/devices/{device_id}", response=DeviceOut, auth=owner_or_admin)
def patch_device(request, device_id: UUID, payload: DevicePatchIn):
    """Relabel, or move a device to another sede.

    **A sede change is not a re-claim.** The device keeps its key and its
    outbox; what changes is its whole location-scoped predicate, so the next
    pull answers a different `location_id` and the till resets and re-pulls the
    collections that predicate reaches. That is why the pull response carries
    `location_id` at all.
    """
    device = get_object_or_404(
        Device.objects.select_related("location", "enrolled_by_user"),
        id=device_id,
        tenant_id=request.tenant_id,
    )
    before = audit.snapshot(device, ["label", "location", "status"])

    if payload.label is not None:
        label = payload.label.strip()
        if not label:
            raise HttpError(422, "Escriba un nombre para este equipo.")
        device.label = label[:60]
    if payload.location_id is not None:
        location = Location.objects.filter(
            id=payload.location_id, tenant_id=request.tenant_id
        ).first()
        if location is None:
            raise HttpError(422, "Esa sede no existe en esta droguería.")
        device.location = location

    try:
        device.save(update_fields=["label", "location", "updated_at"])
    except IntegrityError as error:
        raise HttpError(
            409,
            f"Ya hay un equipo llamado «{device.label}» en "
            f"{device.location.name}. Póngale otro nombre a este.",
        ) from error

    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="devices",
        entity_id=device.id,
        before=before,
        after=audit.snapshot(device, ["label", "location", "status"]),
        request_id=request_id.get(),
    )
    device.refresh_from_db()
    counts = conflict_service.open_counts(request.tenant_id, [device.id])
    return _device_out(device, counts.get(str(device.id), 0))


@router.post("/devices/{device_id}/revoke", response=DeviceOut, auth=owner_or_admin)
def revoke_device(request, device_id: UUID):
    """Invalidate the `device_key`.

    **Revocation is not deletion, and a revoked device keeps its data.** A till
    whose key was revoked because it was reassigned may still be holding unsent
    rows; wiping it to punish it destroys exactly what the office needs. Its
    next sync call fails on the key and it renders
    `degraded · este equipo fue dado de baja` while keeping its local store and
    its outbox.
    """
    device = get_object_or_404(
        Device.objects.select_related("location", "enrolled_by_user"),
        id=device_id,
        tenant_id=request.tenant_id,
    )
    if device.status == DeviceStatus.REVOKED:
        raise HttpError(409, "Este equipo ya está dado de baja.")

    before = audit.snapshot(device, ["label", "location", "status"])
    device.status = DeviceStatus.REVOKED
    device.revoked_at = timezone.now()
    device.revoked_by_user = request.user if request.user.tenant_id else None
    device.save(update_fields=["status", "revoked_at", "revoked_by_user", "updated_at"])

    conflict_service.raise_conflict(
        device=device,
        type=SyncConflictType.DEVICE_REVOKED,
        detail={"reason": "device_revoked", "request_id": request_id.get()},
    )
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.REVOKE,
        entity_type="devices",
        entity_id=device.id,
        before=before,
        after=audit.snapshot(device, ["label", "location", "status"]),
        request_id=request_id.get(),
    )
    counts = conflict_service.open_counts(request.tenant_id, [device.id])
    return _device_out(device, counts.get(str(device.id), 0))


# ---------------------------------------------------------------------------
# The arrival queue
# ---------------------------------------------------------------------------


def _conflict_out(row):
    return {
        "id": row.id,
        "device_id": row.device_id,
        "device_label": row.device.label if row.device_id else None,
        "location_id": row.location_id,
        "location_name": row.location.name,
        "collection": row.collection,
        "client_uuid": row.client_uuid,
        "type": row.type,
        "detail": row.detail or {},
        "status": row.status,
        "occurred_at": row.occurred_at,
        "recorded_at": row.recorded_at,
        "resolved_at": row.resolved_at,
        "resolved_by_name": (
            row.resolved_by_user.name if row.resolved_by_user_id else None
        ),
        "resolution_note": row.resolution_note,
    }


@router.get("/sync/conflicts", response=Page[ConflictOut], auth=owner_or_admin)
def list_conflicts(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
    location_id: UUID | None = Query(None),
    device_id: UUID | None = Query(None),
    type: str | None = Query(None),
    status: ConflictStatusValue | None = Query(None),
):
    """The arrival queue. S3 and S4 write into the same table and read out
    here; neither re-exposes this endpoint."""
    allowed = scoping.readable_locations(
        request.user,
        request.tenant_id,
        requested=[location_id] if location_id else None,
    )
    rows = (
        SyncConflict.objects.filter(
            tenant_id=request.tenant_id, location_id__in=allowed
        )
        .select_related("device", "location", "resolved_by_user")
        .order_by("-recorded_at", "-id")
    )
    if device_id:
        rows = rows.filter(device_id=device_id)
    if type:
        if type not in SyncConflictType.values:
            raise HttpError(
                422,
                f"«{type}» no es un tipo de conflicto. Los tipos son: "
                + ", ".join(SyncConflictType.values),
            )
        rows = rows.filter(type=type)
    if status:
        rows = rows.filter(status=status)

    page_rows, row_count, page, page_size = paginate(
        rows,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable={
            "recorded_at": ["recorded_at", "id"],
            "type": ["type"],
            "status": ["status"],
        },
    )
    return {
        "rows": [_conflict_out(row) for row in page_rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


@router.patch(
    "/sync/conflicts/{conflict_id}", response=ConflictOut, auth=owner_or_admin
)
def patch_conflict(request, conflict_id: UUID, payload: ConflictPatchIn):
    """Close a row as `resolved` or `dismissed`, with a note. **Never deletes.**"""
    row = get_object_or_404(
        SyncConflict.objects.select_related("device", "location", "resolved_by_user"),
        id=conflict_id,
        tenant_id=request.tenant_id,
    )
    if row.status != SyncConflictStatus.OPEN:
        raise HttpError(409, "Este conflicto ya está cerrado.")
    before = {"status": row.status}
    row.status = payload.status
    row.resolved_at = timezone.now()
    row.resolved_by_user = request.user if request.user.tenant_id else None
    row.resolution_note = (payload.note or "").strip()[:2000]
    row.save(
        update_fields=[
            "status",
            "resolved_at",
            "resolved_by_user",
            "resolution_note",
            "updated_at",
        ]
    )
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="sync_conflicts",
        entity_id=row.id,
        before=before,
        after={"status": row.status, "note": row.resolution_note},
        request_id=request_id.get(),
    )
    row.refresh_from_db()
    return _conflict_out(row)


# ---------------------------------------------------------------------------
# The `sync` settings group (rule 5)
# ---------------------------------------------------------------------------


@router.get("/settings/sync", response=SyncSettingsOut, auth=owner_or_admin)
def read_sync_settings(request):
    return sync_settings.read(_tenant(request))


@router.patch("/settings/sync", response=SyncSettingsOut, auth=owner_or_admin)
def write_sync_settings(request, payload: SyncSettingsIn):
    """Write the `sync` group through S0's per-group helper.

    No tenant id in the path: the tenant is pinned per request, so the write
    always targets the current one (rule 5, rule 6). The helper issues one
    `jsonb_set` and leaves S0's `tenant` group exactly as it stands.
    """
    tenant = _tenant(request)
    values = payload.dict()
    for key, (low, high) in sync_settings.BOUNDS.items():
        given = int(values[key])
        if not low <= given <= high:
            raise HttpError(
                422,
                f"«{key}» debe estar entre {low} y {high}. Recibimos {given}.",
            )
    before = sync_settings.read(tenant)
    written = sync_settings.write(tenant, values)
    audit.record(
        actor=request.user,
        tenant_id=tenant.id,
        action=AuditAction.UPDATE,
        entity_type="tenants",
        entity_id=tenant.id,
        before=before,
        after=written,
        request_id=request_id.get(),
    )
    return written
