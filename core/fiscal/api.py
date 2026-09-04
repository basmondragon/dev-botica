"""S5's endpoints, on the router `core.api` mounts.

Every path carries the `/api/` prefix and is English (§3), runs behind S0's
single permission dependency (§2) inside the pinned transaction (A1), applies
S0's location-scoping helper rather than its own filter (A2), and appends to
`audit_log` through S0's path on every elevated-role mutation (ledger).

**S5 adds no endpoint a till calls, and no unauthenticated inbound endpoint at
all.** The one fiscal read a counter makes is a read of its own sale, and it
gets identifiers rather than a status: a fiscal state is not a cashier's to read
(§8). A target that reports asynchronously is **polled** through its mapping's
query operation rather than calling us back, which is why rule 6's fifth context
has no user at v1.

**`GET /api/fiscal-documents/summary` answers `{"configured": false}` and no
counts when nothing is connected.** A body carrying `pending: 0` passes a count
check and fails the one that matters, because zero renders and absent does not
(§8) -- so the shape itself enforces what S9 is built against.
"""

import uuid
from datetime import datetime
from typing import Literal

from django.db.models import Count, Min, Q
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit, scoping
from core.fiscal import (
    document as canonical,
    delivery,
    export,
    jobs,
    service,
    settings as invoicing,
    targets,
)
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.middleware import request_id
from core.models import (
    AuditAction,
    FiscalDocument,
    FiscalDocumentStatus,
    Role,
    Sale,
    Tenant,
)
from core.permissions import any_member, owner_or_admin

router = Router()

SortOrder = Literal["asc", "desc"]
StatusValue = Literal["pending", "sent", "acknowledged", "failed"]

SORTABLE = {
    "created_at": ["created_at"],
    "attempts": ["attempts"],
    "status": ["status", "-created_at"],
    "location": ["location__name", "-created_at"],
    "document_key": ["document_key"],
}


def _tenant(request):
    return get_object_or_404(Tenant, id=request.tenant_id)


def _readable(request, requested=None):
    return scoping.readable_locations(
        request.user, request.tenant_id, requested=requested
    )


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class DocumentRow(Schema):
    id: uuid.UUID
    document_key: str
    #: `sale` or `credit_note`, **derived from the key** and never a column
    #: (*Data*).
    type: Literal["sale", "credit_note"]
    location_id: uuid.UUID | None
    location_name: str
    sale_id: uuid.UUID | None
    sale_number: str
    target: str
    target_label: str
    mapping_version: str
    status: StatusValue
    attempts: int
    #: Null means **held, not queued** -- the work list says which in words.
    next_attempt_at: datetime | None
    created_at: datetime
    sent_at: datetime | None
    acknowledged_at: datetime | None
    external_number: str
    cude: str
    pdf_url: str
    #: One Spanish sentence a person can act on. Never a bare HTTP status.
    error: str


class DocumentDetail(DocumentRow):
    #: **As sent on the last attempt, or as it renders now for a row that has
    #: not been sent** -- which is what an administrator needs to see before
    #: pressing anything.
    payload: dict
    payload_is_current_render: bool
    response: dict


class OrphanRow(Schema):
    """A sale, not a document, so it carries no badge column. Each row states in
    words why no document exists."""

    kind: Literal["sale", "credit_note"]
    id: uuid.UUID
    number: str
    sale_id: uuid.UUID
    sale_number: str
    location_id: uuid.UUID
    location_name: str
    recorded_at: datetime
    reason: str


class LocationCount(Schema):
    location_id: uuid.UUID
    location_name: str
    unsent: int
    failed: int


class SummaryOut(Schema):
    """**Two shapes, and the difference is the contract S9 is built against.**

    Unconfigured, the body's only key is `configured: false`: every other field
    is `None` and the endpoint is declared `exclude_none`, so an answer that
    carries no counts cannot be rendered as one.
    """

    configured: bool
    unsent: int | None = None
    failed: int | None = None
    oldest_unsent_at: datetime | None = None
    by_location: list[LocationCount] | None = None


class ExportRow(Schema):
    period: str
    document_count: int
    file: str
    url: str


class SaleFiscalOut(Schema):
    """The sale's fiscal read-out.

    **For a `cashier` this carries the identifiers and no `status` at all**: a
    fiscal state is an administrator's work list and not a cashier's (§8), and a
    key that is absent cannot be rendered as a badge by mistake.
    """

    configured: bool
    external_number: str | None = None
    cude: str | None = None
    pdf_url: str | None = None
    status: StatusValue | None = None
    document_key: str | None = None


class InvoicingRetry(Schema):
    cap_hours: int
    dwell_minutes: int
    clock_skew_hours: int


class InvoicingDelivery(Schema):
    mode: Literal["per_sale", "batched"]
    prefix: str
    format: Literal["csv", "json"]


class TargetOption(Schema):
    id: str
    label: str
    needs_base_url: bool
    needs_credential: bool
    mappings: list[str]


class InvoicingSettingsOut(Schema):
    target: str
    environment: Literal["test", "production"]
    base_url: str
    configured_at: str
    mapping: str
    delivery: InvoicingDelivery
    retry: InvoicingRetry
    notifications: list[str]
    #: **Whether the credential resolved**, never the credential. There is no
    #: field to type one into and no endpoint that returns one (§9).
    credential_resolved: bool
    #: The predicate itself, so the screen and the server never disagree about
    #: whether the handoff is on.
    enabled: bool
    #: Held deliveries, which is the **one bounded exception** to §8's silence:
    #: disconnecting a target while documents are in flight is a state a person
    #: created deliberately two clicks ago, and hiding it would hide their own
    #: decision from them.
    held: int
    available_targets: list[TargetOption]


class InvoicingRetryIn(Schema):
    cap_hours: int | None = None
    dwell_minutes: int | None = None
    clock_skew_hours: int | None = None


class InvoicingDeliveryIn(Schema):
    mode: Literal["per_sale", "batched"] | None = None
    prefix: str | None = None
    format: Literal["csv", "json"] | None = None


class InvoicingSettingsIn(Schema):
    target: str | None = None
    environment: Literal["test", "production"] | None = None
    base_url: str | None = None
    mapping: str | None = None
    delivery: InvoicingDeliveryIn | None = None
    retry: InvoicingRetryIn | None = None
    notifications: list[str] | None = None


# ---------------------------------------------------------------------------
# The work list
# ---------------------------------------------------------------------------


def _row(row) -> dict:
    sale = row.sale or (row.sale_return.sale if row.sale_return_id else None)
    return {
        "id": row.id,
        "document_key": row.document_key,
        "type": (
            canonical.CREDIT_NOTE if service.is_credit_note(row) else canonical.SALE
        ),
        "location_id": row.location_id,
        "location_name": row.location.name if row.location_id else "—",
        "sale_id": sale.id if sale else None,
        "sale_number": sale.number if sale else "",
        "target": row.target,
        "target_label": _target_label(row.target),
        "mapping_version": row.mapping_version,
        "status": row.status,
        "attempts": row.attempts,
        "next_attempt_at": row.next_attempt_at,
        "created_at": row.created_at,
        "sent_at": row.sent_at,
        "acknowledged_at": row.acknowledged_at,
        "external_number": row.external_number,
        "cude": row.cude,
        "pdf_url": row.pdf_url,
        "error": row.error,
    }


def _target_label(target_id: str) -> str:
    try:
        return targets.get(target_id).label
    except LookupError:
        return target_id


@router.get("/fiscal-documents", response=Page[DocumentRow], auth=owner_or_admin)
def list_fiscal_documents(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
    status: list[StatusValue] | None = Query(None),
    location_id: list[uuid.UUID] | None = Query(None),
    target: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
):
    """`Envíos a facturación`, server-paginated per the grid contract.

    **`status` takes several values, and that is not a convenience.** The work
    list's first segment is everything that has not settled -- `pending` *and*
    `sent` -- and filtering that in the browser would filter one server page
    after the server had already paginated: on a tenant whose newest rows are
    all `acknowledged`, page one would drop every row and the screen would
    announce that nothing is pending while the footer counted hundreds. The
    filter belongs where the pagination is, and it is what the
    `(tenant, location, status, created_at)` index was migrated for.

    **Unconfigured it still resolves and returns nothing**, which is what lets
    the route render §B.10.2's never-populated empty state rather than a 404 --
    a route that refused would be indistinguishable from a broken link.
    """
    queryset = FiscalDocument.objects.select_related(
        "location", "sale", "sale_return", "sale_return__sale"
    ).filter(tenant_id=request.tenant_id)
    queryset = queryset.filter(location_id__in=_readable(request, location_id))
    if status:
        queryset = queryset.filter(status__in=list(status))
    if target:
        queryset = queryset.filter(target=target)
    if since:
        queryset = queryset.filter(created_at__gte=since)
    if until:
        queryset = queryset.filter(created_at__lte=until)

    rows, row_count, page, page_size = paginate(
        queryset,
        page=page,
        page_size=page_size,
        sort=sort or "created_at",
        order=order,
        sortable=SORTABLE,
    )
    return {
        "rows": [_row(row) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/fiscal-documents/unsent-sales", response=Page[OrphanRow], auth=owner_or_admin
)
def list_unsent_sales(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
):
    """`Ventas sin enviar` -- the orphan check's own list.

    **Reported, never repaired** (*Jobs*). A document recreated by a sweep would
    hide the defect that produced the hole, and the one thing worse than a
    missing document is a mechanism that manufactures documents nobody can
    account for.
    """
    tenant = _tenant(request)
    allowed = {str(one) for one in _readable(request)}
    rows = [row for row in jobs.orphans(tenant) if row["location_id"] in allowed]
    start = max(0, (max(1, page) - 1) * page_size)
    return {
        "rows": rows[start : start + page_size],
        "row_count": len(rows),
        "page": max(1, page),
        "page_size": page_size,
    }


@router.get(
    "/fiscal-documents/summary",
    response=SummaryOut,
    auth=owner_or_admin,
    exclude_none=True,
)
def read_summary(request):
    """`configured`, and when true the counts S9's Panel reads.

    **When false the body carries no counts at all** -- absent, not zero (§8).
    S9 renders nothing whatsoever: no strip, no tile, no clause appended to the
    freshness line.
    """
    tenant = _tenant(request)
    if not service.handoff_enabled(tenant):
        return {"configured": False}

    allowed = _readable(request)
    unsent_states = (FiscalDocumentStatus.PENDING, FiscalDocumentStatus.SENT)
    scoped = FiscalDocument.objects.filter(tenant_id=tenant.id, location_id__in=allowed)
    by_location = (
        scoped.values("location_id", "location__name")
        .annotate(
            unsent=Count("id", filter=Q(status__in=unsent_states)),
            failed=Count("id", filter=Q(status=FiscalDocumentStatus.FAILED)),
        )
        .order_by("location__name")
    )
    oldest = scoped.filter(status__in=unsent_states).aggregate(at=Min("created_at"))
    return {
        "configured": True,
        "unsent": scoped.filter(status__in=unsent_states).count(),
        "failed": scoped.filter(status=FiscalDocumentStatus.FAILED).count(),
        "oldest_unsent_at": oldest["at"],
        "by_location": [
            {
                "location_id": row["location_id"],
                "location_name": row["location__name"],
                "unsent": row["unsent"],
                "failed": row["failed"],
            }
            for row in by_location
            if row["unsent"] or row["failed"]
        ],
    }


@router.get("/fiscal-documents/exports", response=list[ExportRow], auth=owner_or_admin)
def list_exports(request):
    """The generated export files, with their period, their count and a link."""
    return export.listing(_tenant(request))


def _document(request, document_id):
    row = get_object_or_404(
        FiscalDocument.objects.select_related(
            "location", "sale", "sale__location", "sale_return", "sale_return__sale"
        ),
        id=document_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, requested=[row.location_id] if row.location_id else None)
    return row


@router.get(
    "/fiscal-documents/{document_id}", response=DocumentDetail, auth=owner_or_admin
)
def read_fiscal_document(request, document_id: uuid.UUID):
    """One handoff: its key, its target and mapping version, the payload, the
    attempt trail, the parsed response and its identifiers where they exist."""
    row = _document(request, document_id)
    payload = row.payload or {}
    current = False
    if not payload:
        # **As it renders now**, for a row nothing has been sent for. That is
        # what an administrator needs to see before pressing `Reintentar`, and
        # it costs one build of a pure function.
        try:
            payload = service.render(row)
            current = True
        except Exception:  # noqa: BLE001 -- the reason is already on the row
            payload = {}
    return {
        **_row(row),
        "payload": payload,
        "payload_is_current_render": current,
        "response": row.response or {},
    }


@router.post(
    "/fiscal-documents/{document_id}/retry",
    response=DocumentDetail,
    auth=owner_or_admin,
)
def retry_fiscal_document(request, document_id: uuid.UUID):
    """Force an attempt now, **rebuilding the payload from the sale as it now
    stands**.

    Idempotent against the job's own queueing lock: pressing it twice enqueues
    one attempt. Nothing about the fiscal row is edited beyond its state, no
    migration is written, and no correction is typed into a document.
    """
    row = _document(request, document_id)
    if row.status == FiscalDocumentStatus.ACKNOWLEDGED:
        raise HttpError(
            409,
            f"El envío {row.document_key} ya está confirmado por el sistema de "
            "facturación.",
        )
    before = {"status": row.status, "attempts": row.attempts, "error": row.error}
    delivery.requeue(row)
    jobs.enqueue_delivery(row)
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="fiscal_documents",
        entity_id=row.id,
        before=before,
        after={"status": row.status, "attempts": row.attempts, "error": ""},
        request_id=request_id.get(),
    )
    return read_fiscal_document(request, row.id)


# ---------------------------------------------------------------------------
# The two sale-shaped reads
# ---------------------------------------------------------------------------


@router.get("/sales/{sale_id}/canonical-document", response=dict, auth=owner_or_admin)
def read_canonical_document(request, sale_id: uuid.UUID):
    """Render the canonical payload for one sale **without sending it**.

    This is how a mapping gets written: the first hour of integrating a client's
    system is spent answering *"what exactly do you send?"*, and a screen that
    answers it turns a week of emails into an afternoon.
    """
    sale = get_object_or_404(
        Sale.objects.select_related("location", "customer", "device"),
        id=sale_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, requested=[sale.location_id])
    tenant = _tenant(request)
    row = FiscalDocument(
        tenant_id=tenant.id,
        sale=sale,
        location_id=sale.location_id,
        document_key=service.base_key(sale),
    )
    try:
        return service.render(row, tenant=tenant)
    except canonical.Incomplete as refusal:
        raise HttpError(422, str(refusal)) from refusal


@router.get(
    "/sales/{sale_id}/fiscal-document",
    response=SaleFiscalOut,
    auth=any_member,
    exclude_none=True,
)
def read_sale_fiscal_document(request, sale_id: uuid.UUID):
    """The handoff of one sale, for the sale detail and a reprint.

    **For a `cashier` it returns the identifiers and never a status** (§8). With
    no target configured, or with nothing returned, the body is
    `{"configured": false}` and the region renders nothing at all -- not a
    placeholder, not a status, and never a skeleton that will not resolve.
    """
    sale = get_object_or_404(Sale, id=sale_id, tenant_id=request.tenant_id)
    _readable(request, requested=[sale.location_id])
    tenant = _tenant(request)
    if not service.handoff_enabled(tenant):
        return {"configured": False}
    row = (
        FiscalDocument.objects.filter(
            tenant_id=tenant.id, sale_id=sale.id, document_key=service.base_key(sale)
        )
        .order_by("created_at")
        .first()
    )
    if row is None:
        return {"configured": True}
    elevated = request.user.role in (Role.OWNER, Role.ADMIN, Role.PLATFORM_ADMIN)
    body = {
        "configured": True,
        "external_number": row.external_number or None,
        "cude": row.cude or None,
        "pdf_url": row.pdf_url or None,
    }
    if elevated:
        body["status"] = row.status
        body["document_key"] = row.document_key
    return body


# ---------------------------------------------------------------------------
# The `invoicing` settings group (ledger rule 5)
# ---------------------------------------------------------------------------


def _settings_body(tenant, group) -> dict:
    from core.fiscal import secrets

    spec = None
    if group["target"]:
        try:
            spec = targets.get(group["target"])
        except LookupError:
            spec = None
    return {
        **group,
        "credential_resolved": secrets.resolves(tenant, spec) if spec else False,
        "enabled": service.handoff_enabled(tenant),
        "held": FiscalDocument.objects.filter(
            tenant_id=tenant.id,
            status__in=(FiscalDocumentStatus.PENDING, FiscalDocumentStatus.SENT),
        ).count(),
        "available_targets": [
            {
                "id": one.id,
                "label": one.label,
                "needs_base_url": one.needs_base_url,
                "needs_credential": one.needs_credential,
                "mappings": list(one.mappings),
            }
            for one in sorted(targets.registry().values(), key=lambda one: one.label)
        ],
    }


@router.get("/settings/invoicing", response=InvoicingSettingsOut, auth=owner_or_admin)
def read_invoicing_settings(request):
    tenant = _tenant(request)
    return _settings_body(tenant, invoicing.read(tenant))


@router.patch("/settings/invoicing", response=InvoicingSettingsOut, auth=owner_or_admin)
def write_invoicing_settings(request, payload: InvoicingSettingsIn):
    """Write the group through S0's helper, which issues one `jsonb_set` and
    leaves every other group as it stands (ledger rule 5).

    **`target` is an API-key setting and is therefore `owner` only** (§2): naming
    a target is what decides which credential is read out of the secrets store,
    and §2 withholds billing and API-key settings from `admin`.
    """
    tenant = _tenant(request)
    before = invoicing.read(tenant)
    values = {
        key: value
        for key, value in payload.dict(exclude_unset=True).items()
        if value is not None
    }
    if request.user.role != Role.OWNER and any(
        key in values for key in invoicing.OWNER_ONLY
    ):
        raise HttpError(
            403,
            "Conectar o cambiar el sistema de facturación requiere el perfil "
            "Propietaria.",
        )
    try:
        after = invoicing.write(tenant, values)
    except invoicing.Invalid as refusal:
        raise HttpError(422, str(refusal)) from refusal
    audit.record(
        actor=request.user,
        tenant_id=tenant.id,
        action=AuditAction.UPDATE,
        entity_type="settings.invoicing",
        entity_id=tenant.id,
        # The group never carries the credential, so neither does this row --
        # a credential in the audit log is a credential in a JSONB column by
        # another route (§9).
        before=before,
        after=after,
        request_id=request_id.get(),
    )
    return _settings_body(tenant, after)
