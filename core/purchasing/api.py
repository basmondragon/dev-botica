"""S6's endpoints, on the router `core.api` mounts.

Every path carries the `/api/` prefix and is English (§3), runs behind S0's
single permission dependency (§2) inside the pinned transaction (A1), applies
S0's location-scoping helper rather than its own filter (A2), and appends to
`audit_log` through S0's path on every elevated-role mutation (ledger).

**No endpoint here is reachable by a `cashier`.** Compras is not on that role's
nav and a direct URL refuses in the content region naming the role required
(§B.8.3) -- receiving included, because it writes cost and creates lots
(*Gated on*, and the measurement that would change that answer is stated there).

**`PATCH …/lines/{line_id}` writes `approved_quantity` and never
`suggested_quantity`.** That is the single most important line in this module.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit, scoping
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate

# **S3's field-scope refusal, reused rather than coined again.** `core.api`
# already registers the handler that turns it into `{detail, line, field}`, and
# a second exception class with the same shape would be a second envelope the
# receiving screen had to learn (§B.10.3).
from core.inventory.api import LineRefused
from core.middleware import request_id
from core.models import (
    AuditAction,
    DemandForecast,
    ForecastBasis,
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
    GoodsReceiptType,
    Item,
    Location,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSource,
    PurchaseOrderStatus,
    StockOnHand,
    Supplier,
    Tenant,
)
from core.permissions import owner_or_admin
from core.purchasing import (
    forecast,
    jobs,
    orders as order_service,
    reasons,
    receiving,
    settings as purchasing_settings,
)

router = Router()

SortOrder = Literal["asc", "desc"]
StatusValue = Literal[
    "suggested", "approved", "sent", "partially_received", "received", "discarded"
]
SourceValue = Literal["model", "manual"]
BasisValue = Literal["parametric", "learning", "learned"]
BandValue = Literal["alta", "media", "baja"]
ReceiptTypeValue = Literal["receipt", "supplier_return"]
ReceiptStatusValue = Literal["draft", "confirmed"]

ORDER_SORTABLE = {
    "number": ["number"],
    "supplier": ["supplier__name", "-created_at"],
    "location": ["location__name", "-created_at"],
    "lines": ["line_count", "-created_at"],
    "total": ["total", "-created_at"],
    "created_at": ["created_at"],
    "status": ["status", "-created_at"],
}

#: The `Por qué` header sorts on `(confidence asc, basis)` -- **the sort key is
#: the one that column already displays**, so the thin lines come to the top of
#: a long order and every banded line sits above every unbanded `Alta` one.
LINE_SORTABLE = {
    "item": ["item__name"],
    "stock": ["live_stock", "item__name"],
    "weekly": ["live_weekly", "item__name"],
    "coverage": ["live_coverage", "item__name"],
    "suggested": ["approved_quantity", "item__name"],
    "reason": ["confidence", "basis", "item__name"],
}


def _tenant(request):
    return get_object_or_404(Tenant, id=request.tenant_id)


def _readable(request, requested=None):
    return scoping.readable_locations(
        request.user, request.tenant_id, requested=requested
    )


def _order(request, order_id) -> PurchaseOrder:
    order = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier", "location"),
        id=order_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, [order.location_id])
    return order


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class OrderRow(Schema):
    id: uuid.UUID
    number: int
    supplier_id: uuid.UUID
    supplier_name: str
    location_id: uuid.UUID
    location_name: str
    status: StatusValue
    source: SourceValue
    line_count: int
    total: Decimal
    created_at: datetime
    approved_at: datetime | None
    sent_at: datetime | None
    approved_by_name: str
    dispatch_attempts: int
    last_dispatch_error: str


class LineRow(Schema):
    """One row of the drawn table.

    `stock`, `weekly_sales` and `coverage_days` are **live** -- read now, from
    the projection and the current forecast, because they are what the buyer is
    deciding against this morning. The four stamped figures are on the record
    panel under `Al generar la orden`, which is where a reading a month old
    belongs.
    """

    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    presentation: str
    manufacturer_name: str | None
    category_id: uuid.UUID | None
    stock: int
    weekly_sales: Decimal | None
    coverage_days: Decimal | None
    suggested_quantity: int | None
    approved_quantity: int
    received_quantity: int
    unit_cost: Decimal | None
    basis: BasisValue | None
    confidence: Decimal | None
    band: BandValue | None
    reason: str
    reason_code: str
    reason_fallback: str
    #: The stamped copies, for the record panel and for the deviation report.
    stamped_coverage_days: Decimal | None


class KpiOut(Schema):
    """§B.9.2 tier 3 · **a figure that cannot be computed is absent, not zero.**

    `figure` is null and `reading` carries the reason, which is what the tile
    renders as an em dash with `sin proyección de venta` beneath it. Three of
    the four tiles rest on a demand estimate, and on a parametric order there is
    none -- three tiles reading `0` would be the empty state nobody designed.
    """

    key: str
    label: str
    figure: Decimal | None
    reading: str


class ProvenanceOut(Schema):
    """The filter bar's right slot, computed and never transcribed (*UI*).

    `basis` is the regime the majority of the order's lines were generated in,
    and `window` is the span of `sales` the refresh actually read for that sede
    -- `6 meses` on a tenant carrying six months, whatever a drawing says.
    """

    basis: BasisValue
    window: str
    computed_at: datetime | None
    model_prose: bool


class OrderDetail(Schema):
    order: OrderRow
    lines: list[LineRow]
    row_count: int
    page: int
    page_size: int
    #: What the footer's `de N referencias sugeridas` and tile 1 both carry:
    #: the order's own count of lines standing at a quantity above zero.
    suggested_reference_count: int
    active_reference_count: int
    kpis: list[KpiOut]
    provenance: ProvenanceOut
    basis_counts: dict[str, int]
    band_counts: dict[str, int]


class LinePatchIn(Schema):
    """`approved_quantity` and nothing else. There is no field here that could
    carry a `suggested_quantity`, which is the cheapest possible guarantee that
    no request ever moves one."""

    approved_quantity: int


class ManualLineIn(Schema):
    item_id: uuid.UUID
    quantity: int
    unit_cost: Decimal | None = None


class ManualOrderIn(Schema):
    location_id: uuid.UUID
    supplier_id: uuid.UUID
    lines: list[ManualLineIn]


class GenerateIn(Schema):
    """A location, or every readable one. Generation is asynchronous and this
    returns the handle, not the orders."""

    location_id: uuid.UUID | None = None


class GenerateOut(Schema):
    queued: int
    location_ids: list[uuid.UUID]


class GoodsReceiptLineRow(Schema):
    id: uuid.UUID
    purchase_order_line_id: uuid.UUID | None
    item_id: uuid.UUID
    item_name: str
    presentation: str
    tracks_lots: bool
    tracks_expiry: bool
    ordered_quantity: int | None
    quantity: int
    lot_id: uuid.UUID | None
    lot_code: str
    expires_at: date | None
    unit_cost: Decimal | None


class GoodsReceiptRow(Schema):
    id: uuid.UUID
    purchase_order_id: uuid.UUID | None
    order_number: int | None
    location_id: uuid.UUID
    location_name: str
    supplier_id: uuid.UUID
    supplier_name: str
    type: ReceiptTypeValue
    status: ReceiptStatusValue
    received_at: datetime
    received_by_name: str
    supplier_document_number: str
    notes: str
    confirmed_at: datetime | None
    line_count: int


class GoodsReceiptDetail(GoodsReceiptRow):
    lines: list[GoodsReceiptLineRow]


class GoodsReceiptLineIn(Schema):
    id: uuid.UUID | None = None
    item_id: uuid.UUID | None = None
    purchase_order_line_id: uuid.UUID | None = None
    quantity: int
    lot_code: str | None = None
    expires_at: date | None = None
    unit_cost: Decimal | None = None


class GoodsReceiptIn(Schema):
    purchase_order_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    #: Nullable rather than defaulted, so the generated client type makes it
    #: optional: a field with a default and no null is *required* in the typed
    #: body, and every caller would have to say `receipt`.
    type: ReceiptTypeValue | None = None
    received_at: datetime | None = None
    supplier_document_number: str | None = None
    notes: str | None = None
    lines: list[GoodsReceiptLineIn] | None = None


class GoodsReceiptPatchIn(Schema):
    received_at: datetime | None = None
    supplier_document_number: str | None = None
    notes: str | None = None
    lines: list[GoodsReceiptLineIn] | None = None


class ForecastRow(Schema):
    item_id: uuid.UUID
    item_name: str
    location_id: uuid.UUID
    basis: BasisValue
    confidence: Decimal
    band: BandValue
    weekly_sales: Decimal | None
    trend: Decimal | None
    coverage_days: Decimal | None
    reorder_point: int
    safety_stock: int
    usable_weeks: int | None
    variation: Decimal | None
    imported_share: Decimal | None
    computed_at: datetime
    model_version: str


class PurchasingSettingsOut(Schema):
    default_lead_time_days: int
    target_coverage_days: int
    order_cap_value: int
    order_cap_weeks_per_line: int
    refresh_hour: int
    service_level: float
    learned_min_weeks: int
    learned_max_rse: float
    learned_demote_rse: float
    category_default_min_items: int
    seasonal_multiplier_enabled: bool
    reason_text_enabled: bool
    write_model_stock_policies: bool


class PurchasingSettingsIn(Schema):
    default_lead_time_days: int | None = None
    target_coverage_days: int | None = None
    order_cap_value: int | None = None
    order_cap_weeks_per_line: int | None = None
    refresh_hour: int | None = None
    service_level: float | None = None
    learned_min_weeks: int | None = None
    learned_max_rse: float | None = None
    learned_demote_rse: float | None = None
    category_default_min_items: int | None = None
    seasonal_multiplier_enabled: bool | None = None
    reason_text_enabled: bool | None = None
    write_model_stock_policies: bool | None = None


# ---------------------------------------------------------------------------
# The orders grid
# ---------------------------------------------------------------------------


def _order_row(order, *, line_count=None) -> dict:
    """One row of `Órdenes de compra`.

    `line_count` is passed rather than read off the instance: the grid annotates
    it in one subquery for a whole page, and a mutation endpoint counts the one
    order it just changed. An attribute set on the model instance would be a
    third way of saying the same thing that a reader has to check for.
    """
    return {
        "id": order.id,
        "number": order.number,
        "supplier_id": order.supplier_id,
        "supplier_name": order.supplier.name,
        "location_id": order.location_id,
        "location_name": order.location.name,
        "status": order.status,
        "source": order.source,
        "line_count": (
            line_count if line_count is not None else getattr(order, "line_count", 0)
        ),
        "total": order.total,
        "created_at": order.created_at,
        "approved_at": order.approved_at,
        "sent_at": order.sent_at,
        "approved_by_name": order.approved_by_name,
        "dispatch_attempts": order.dispatch_attempts,
        "last_dispatch_error": order.last_dispatch_error,
    }


@router.get("/purchase-orders", response=Page[OrderRow], auth=owner_or_admin)
def list_purchase_orders(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
    status: list[StatusValue] | None = Query(None),
    supplier_id: uuid.UUID | None = Query(None),
    location_id: list[uuid.UUID] | None = Query(None),
    source: SourceValue | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
):
    """`Órdenes de compra`, server-paginated per the grid contract (§9)."""
    queryset = (
        PurchaseOrder.objects.select_related("supplier", "location")
        .filter(tenant_id=request.tenant_id)
        .filter(location_id__in=_readable(request, location_id))
        .annotate(line_count=_line_count())
    )
    if status:
        queryset = queryset.filter(status__in=list(status))
    if supplier_id:
        queryset = queryset.filter(supplier_id=supplier_id)
    if source:
        queryset = queryset.filter(source=source)
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
        sortable=ORDER_SORTABLE,
    )
    return {
        "rows": [_order_row(one) for one in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _count_lines(order) -> int:
    return PurchaseOrderLine.objects.filter(purchase_order=order).count()


def _line_count():
    """`count(*)` over the order's lines, as a subquery rather than a join.

    A `Count` annotation beside another aggregate would multiply rows the moment
    a second aggregate joined -- the classic fan-out, which shows up as a line
    count that quietly doubles.
    """
    return Coalesce(
        Subquery(
            PurchaseOrderLine.objects.filter(purchase_order=OuterRef("pk"))
            .order_by()
            .values("purchase_order")
            .annotate(total=Count("id"))
            .values("total")[:1]
        ),
        0,
        output_field=IntegerField(),
    )


@router.post("/purchase-orders", response=OrderRow, auth=owner_or_admin)
def create_purchase_order(request, payload: ManualOrderIn):
    """A manual order: a supplier, a sede and typed lines.

    **`suggested_quantity` is null on every line, not zero.** Nobody proposed
    anything, and a zero there would enter the deviation measurement as a
    proposal of nothing -- which is the one number this stage exists to keep
    honest.
    """
    _readable(request, [payload.location_id])
    location = get_object_or_404(
        Location, id=payload.location_id, tenant_id=request.tenant_id
    )
    supplier = get_object_or_404(
        Supplier, id=payload.supplier_id, tenant_id=request.tenant_id
    )
    if not payload.lines:
        raise HttpError(422, "Una orden sin líneas no se puede enviar a un proveedor.")

    items = {
        item.id: item
        for item in Item.objects.filter(
            tenant_id=request.tenant_id,
            id__in=[line.item_id for line in payload.lines],
        )
    }
    order_service.lock_location(request.tenant_id, location.id)
    order = PurchaseOrder.objects.create(
        tenant_id=request.tenant_id,
        location=location,
        supplier=supplier,
        number=order_service.next_number(request.tenant_id, location.id),
        status=PurchaseOrderStatus.SUGGESTED,
        source=PurchaseOrderSource.MANUAL,
    )
    seen: set = set()
    fresh = []
    for line in payload.lines:
        item = items.get(line.item_id)
        if item is None:
            raise HttpError(
                422, "Una línea de la orden nombra un producto que no existe."
            )
        if line.item_id in seen:
            raise HttpError(
                422,
                f"«{item.name}» aparece dos veces en la orden. Sume las "
                "cantidades en una sola línea.",
            )
        if line.quantity <= 0:
            raise HttpError(
                422, f"La cantidad de «{item.name}» tiene que ser mayor que cero."
            )
        seen.add(line.item_id)
        fresh.append(
            PurchaseOrderLine(
                tenant_id=request.tenant_id,
                purchase_order=order,
                item=item,
                suggested_quantity=None,
                approved_quantity=line.quantity,
                unit_cost=line.unit_cost,
            )
        )
    PurchaseOrderLine.objects.bulk_create(fresh, batch_size=500)
    order_service.recompute_total(order)
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="purchase_orders",
        entity_id=order.id,
        after={
            "number": order.number,
            "supplier": supplier.name,
            "location": location.code,
            "lines": len(fresh),
            "source": PurchaseOrderSource.MANUAL,
        },
        request_id=request_id.get(),
    )
    return _order_row(order, line_count=len(fresh))


# ---------------------------------------------------------------------------
# Generation, and it is declared **before** `/purchase-orders/{order_id}`.
#
# django-ninja matches paths in registration order and a `uuid.UUID` path
# parameter compiles to a bare string converter, so `{order_id}` would swallow
# the literal `generate` and answer a 405 on the one button the screen has for
# it. The order of these two declarations is load-bearing.
# ---------------------------------------------------------------------------


@router.post("/purchase-orders/generate", response=GenerateOut, auth=owner_or_admin)
def generate_purchase_orders(request, payload: GenerateIn):
    """Run generation now, for one sede or for every readable one.

    **Returns the job handle, not the orders**: generation reads a sede's whole
    forecast and a request that waited for twenty of them would time out on the
    one screen that has a button for it.
    """
    allowed = _readable(request, [payload.location_id] if payload.location_id else None)
    queued = 0
    for location_id in allowed:
        queued += 1 if jobs.enqueue_generate(request.tenant_id, location_id) else 0
    return {"queued": queued, "location_ids": list(allowed)}


# ---------------------------------------------------------------------------
# One order, its lines, its four tiles and its provenance line
# ---------------------------------------------------------------------------


def _live(order):
    """The three columns the screen reads now rather than as stamped."""
    stock = (
        StockOnHand.objects.filter(
            tenant_id=OuterRef("tenant_id"),
            location_id=order.location_id,
            item_id=OuterRef("item_id"),
        )
        .order_by()
        .values("item_id")
        .annotate(total=Sum("quantity"))
        .values("total")[:1]
    )
    weekly = DemandForecast.objects.filter(
        tenant_id=OuterRef("tenant_id"),
        location_id=order.location_id,
        item_id=OuterRef("item_id"),
    ).values("weekly_sales")[:1]
    coverage = DemandForecast.objects.filter(
        tenant_id=OuterRef("tenant_id"),
        location_id=order.location_id,
        item_id=OuterRef("item_id"),
    ).values("coverage_days")[:1]
    return {
        "live_stock": Subquery(stock),
        "live_weekly": Subquery(weekly),
        "live_coverage": Subquery(coverage),
    }


def _line_row(line) -> dict:
    values: dict = {}
    if line.reason_code == "stockout_available_elsewhere":
        values = getattr(line, "reason_values", {}) or {}
    fallback = reasons.render(line.reason_code, values) if line.reason_code else ""
    return {
        "id": line.id,
        "item_id": line.item_id,
        "item_name": line.item.name,
        "presentation": line.item.presentation,
        "manufacturer_name": (
            line.item.manufacturer.name if line.item.manufacturer_id else None
        ),
        "category_id": line.item.category_id,
        "stock": int(line.live_stock or 0),
        "weekly_sales": line.live_weekly,
        "coverage_days": line.live_coverage,
        "suggested_quantity": line.suggested_quantity,
        "approved_quantity": line.approved_quantity,
        "received_quantity": line.received_quantity,
        "unit_cost": line.unit_cost,
        "basis": line.basis,
        "confidence": line.confidence,
        "band": forecast.band(line.confidence) if line.confidence is not None else None,
        "reason": line.reason,
        "reason_code": line.reason_code,
        "reason_fallback": fallback,
        "stamped_coverage_days": line.coverage_days,
    }


def _band_filter(bands):
    """The `Confianza del modelo` chip's second group, as one predicate.

    The bands are ranges over a stored 0–1 rather than a column, because a
    stored band would be a second encoding of `confidence` that could drift from
    it -- and the chip's two groups intersect, so both halves have to be
    expressible against the same rows.
    """
    query = Q(pk__in=[])
    for value in bands:
        if value == "alta":
            query |= Q(confidence__gte=forecast.BAND_HIGH)
        elif value == "media":
            query |= Q(
                confidence__gte=forecast.BAND_MEDIUM,
                confidence__lt=forecast.BAND_HIGH,
            )
        else:
            query |= Q(confidence__lt=forecast.BAND_MEDIUM)
    return query


@router.get("/purchase-orders/{order_id}", response=OrderDetail, auth=owner_or_admin)
def read_purchase_order(
    request,
    order_id: uuid.UUID,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
    basis: list[BasisValue] | None = Query(None),
    band: list[BandValue] | None = Query(None),
    category_id: uuid.UUID | None = Query(None),
):
    """One order with its lines, its four tiles, its total and its provenance.

    **The tiles are computed in this request** (acceptance 2). A second round
    trip for four numbers is a second round trip on the screen an administrator
    opens first thing on a Monday, and it is what makes the 400 ms budget a
    budget for the page rather than for one of its halves.

    **The tiles describe the whole order and the footer describes the filter.**
    Narrowing to `Paramétrica` narrows the rows; it does not narrow what the
    order is worth.
    """
    row = _order(request, order_id)
    tenant = _tenant(request)
    options = purchasing_settings.read(tenant)

    lines = (
        PurchaseOrderLine.objects.filter(purchase_order=row)
        .select_related("item", "item__manufacturer")
        .annotate(**_live(row))
    )
    filtered = lines
    if basis:
        filtered = filtered.filter(basis__in=list(basis))
    if band:
        filtered = filtered.filter(_band_filter(band))
    if category_id:
        filtered = filtered.filter(item__category_id=category_id)

    page_rows, row_count, page, page_size = paginate(
        filtered,
        page=page,
        page_size=page_size,
        sort=sort or "item",
        order=order,
        sortable=LINE_SORTABLE,
    )
    _attach_reason_values(request.tenant_id, row, page_rows)

    counts = _counts(lines)
    kpis, suggested, active = _kpis(request.tenant_id, row, lines, options=options)
    return {
        "order": _order_row(
            PurchaseOrder.objects.select_related("supplier", "location")
            .annotate(line_count=_line_count())
            .get(id=row.id)
        ),
        "lines": [_line_row(one) for one in page_rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
        "suggested_reference_count": suggested,
        "active_reference_count": active,
        "kpis": kpis,
        "provenance": _provenance(request.tenant_id, row, lines),
        "basis_counts": counts["basis"],
        "band_counts": counts["band"],
    }


def _attach_reason_values(tenant_id, order, rows):
    """Fill the one fallback string that carries a live figure.

    `En quiebre, hay 96 en Suba` reads another sede's stock **in the same
    request that renders the row**, and it carries no staleness marker: §B.9.2's
    marker belongs to a figure a till read from its local store, and every
    surface in this stage is server-authoritative and online-only.
    """
    wanted = [
        line.item_id
        for line in rows
        if line.reason_code == "stockout_available_elsewhere"
    ]
    if not wanted:
        return
    held = order_service.elsewhere(tenant_id, order.location_id, wanted)
    for line in rows:
        if line.reason_code != "stockout_available_elsewhere":
            continue
        found = held.get(line.item_id)
        line.reason_values = (
            {"elsewhere": found[0], "sede": found[1]}
            if found
            else {"elsewhere": 0, "sede": "otra sede"}
        )


def _counts(lines) -> dict:
    """The counts the `Confianza del modelo` menu shows beside every value."""
    basis_counts = {value: 0 for value in ForecastBasis.values}
    band_counts = {"alta": 0, "media": 0, "baja": 0}
    for basis, confidence in lines.values_list("basis", "confidence"):
        if basis:
            basis_counts[basis] = basis_counts.get(basis, 0) + 1
        if confidence is not None:
            band_counts[forecast.band(confidence)] += 1
    return {"basis": basis_counts, "band": band_counts}


def _provenance(tenant_id, order, lines) -> dict:
    """Three forms, chosen by the basis of the majority of the order's lines.

    The figure is **computed from the window actually used**, never transcribed
    from a drawing: the span of `sales` the refresh really read for that sede,
    rendered in whole months above eight weeks and in whole weeks below. A
    screen carrying `18 meses` so the pixels matched would be the first place in
    this product where a screenshot was worth more than a true number.
    """
    tally: dict = {}
    for basis in lines.values_list("basis", flat=True):
        if basis:
            tally[basis] = tally.get(basis, 0) + 1
    majority = (
        max(tally.items(), key=lambda one: (one[1], one[0]))[0]
        if tally
        else ForecastBasis.PARAMETRIC
    )
    window = forecast.training_window(tenant_id, order.location_id)
    computed_at = (
        DemandForecast.objects.filter(
            tenant_id=tenant_id, location_id=order.location_id
        )
        .order_by("-computed_at")
        .values_list("computed_at", flat=True)
        .first()
    )
    prose = lines.filter(basis=ForecastBasis.LEARNED).exclude(reason="").exists()
    learned_any = lines.filter(basis=ForecastBasis.LEARNED).exists()
    return {
        "basis": majority,
        "window": window["label"],
        "computed_at": computed_at,
        # **Absent prose is only worth saying where prose was possible.** An
        # order with no `learned` line was never going to carry any, and
        # appending `· sin redacción del modelo` to it would report a
        # degradation that did not happen.
        "model_prose": prose or not learned_any,
    }


def _kpis(tenant_id, order, lines, *, options):
    """The four drawn tiles, and what each degrades to (*UI*).

    Tile 1 counts lines and is unaffected by the regime. Tiles 2, 3 and 4 rest
    on a demand estimate, and where there is none each renders `—` with its
    reason rather than a zero -- three tiles reading `0` on the first screen of
    every demo is precisely the empty state nobody designed.
    """
    rows = list(
        lines.values(
            "approved_quantity", "unit_cost", "basis", "live_coverage", "live_weekly"
        )
    )
    suggested = sum(1 for one in rows if one["approved_quantity"] > 0)
    active = (
        Item.objects.filter(
            tenant_id=tenant_id,
            active=True,
            tracks_stock=True,
            id__in=StockOnHand.objects.filter(
                tenant_id=tenant_id, location_id=order.location_id
            ).values("item_id"),
        )
        .distinct()
        .count()
    )
    total = sum(
        (one["unit_cost"] or Decimal("0")) * one["approved_quantity"] for one in rows
    )

    measured = [one for one in rows if one["live_weekly"] is not None]
    daily_cost = order_service.projected_daily_cost(tenant_id, order.location_id)
    covers_days = (
        (total / daily_cost).quantize(Decimal("1"))
        if measured and daily_cost > 0
        else None
    )

    urgent = [
        one
        for one in rows
        if one["live_coverage"] is not None
        and one["live_coverage"] <= 7
        and one["approved_quantity"] > 0
    ]
    saving = order_service.counterfactual(
        tenant_id, order.location_id, order, options=options
    )

    return (
        [
            {
                "key": "suggested_references",
                "label": "Referencias sugeridas",
                "figure": Decimal(suggested),
                # **The denominator travels as `active_reference_count`, not in
                # this string.** §A.11's thousands dot is the client's and a
                # figure formatted on the server would render `1184` beside a
                # column of `1.184`s.
                "reading": "activas en la sede",
            },
            {
                "key": "order_value",
                "label": "Valor de la orden",
                "figure": total,
                "reading": (
                    f"cubre {covers_days} días de venta proyectada"
                    if covers_days is not None
                    else "sin proyección de venta"
                ),
            },
            {
                "key": "stockouts_avoided",
                "label": "Quiebres que evita",
                "figure": Decimal(len(urgent)) if measured else None,
                "reading": (
                    "referencias que se agotan en 7 días"
                    if measured
                    else "sin proyección de venta"
                ),
            },
            {
                "key": "manual_order_saving",
                "label": "Recortes vs. pedido manual",
                # **Reported only when negative** (*UI*): the tile says what the
                # model cut, and a positive difference is the model asking for
                # more than a flat rule would -- which is a legitimate answer and
                # not a saving.
                "figure": saving if saving is not None and saving < 0 else None,
                "reading": (
                    "en referencias de rotación lenta"
                    if saving is not None and saving < 0
                    else "sin comparación posible"
                ),
            },
        ],
        suggested,
        active,
    )


# ---------------------------------------------------------------------------
# The edit, the approval, the discard and the dispatch
# ---------------------------------------------------------------------------


@router.patch(
    "/purchase-orders/{order_id}/lines/{line_id}",
    response=LineRow,
    auth=owner_or_admin,
)
def patch_line(request, order_id: uuid.UUID, line_id: uuid.UUID, payload: LinePatchIn):
    """Write `approved_quantity`. **Never writes `suggested_quantity`.**

    Refused on any order past `suggested`: an approved order's quantities are
    what the supplier was sent, and a screen that let them drift from it would
    be a screen that disagrees with the carton on the loading bay.
    """
    order = _order(request, order_id)
    line = get_object_or_404(
        PurchaseOrderLine.objects.select_related("item", "item__manufacturer"),
        id=line_id,
        purchase_order=order,
        tenant_id=request.tenant_id,
    )
    before = line.approved_quantity
    try:
        order_service.set_line_quantity(order, line, payload.approved_quantity)
    except order_service.Refused as refusal:
        raise HttpError(409, str(refusal)) from refusal
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="purchase_order_lines",
        entity_id=line.id,
        before={"approved_quantity": before},
        after={
            "approved_quantity": line.approved_quantity,
            "suggested_quantity": line.suggested_quantity,
            "order": order.number,
            "item": line.item.name,
        },
        request_id=request_id.get(),
    )
    fresh = (
        PurchaseOrderLine.objects.filter(id=line.id)
        .select_related("item", "item__manufacturer")
        .annotate(**_live(order))
        .get()
    )
    _attach_reason_values(request.tenant_id, order, [fresh])
    return _line_row(fresh)


@router.post(
    "/purchase-orders/{order_id}/approve", response=OrderRow, auth=owner_or_admin
)
def approve_purchase_order(request, order_id: uuid.UUID):
    """Freeze the quantities, stamp the approver, enqueue the dispatch.

    **Idempotent**: a second call on an order past `suggested` returns it
    unchanged and enqueues nothing, because a double-pressed button must not
    send a supplier the same order twice.
    """
    order = _order(request, order_id)
    if order.status == PurchaseOrderStatus.APPROVED:
        # **`Reintentar ahora`.** The order is approved and the dispatch failed;
        # pressing approve again is what the screen offers, so it has to queue
        # the send rather than answer with the row unchanged. The attempt count
        # is part of the queueing lock, so a retry is a new job and a
        # double-clicked retry is still one.
        jobs.enqueue_dispatch(
            request.tenant_id, order.id, attempt=order.dispatch_attempts
        )
        return _order_row(order, line_count=_count_lines(order))
    if order.status != PurchaseOrderStatus.SUGGESTED:
        return _order_row(order, line_count=_count_lines(order))

    before = order.status
    order_service.approve(order, actor=request.user)
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.APPROVE,
        entity_type="purchase_orders",
        entity_id=order.id,
        before={"status": before},
        after={
            "status": order.status,
            "number": order.number,
            "total": str(order.total),
            "supplier": order.supplier.name,
        },
        request_id=request_id.get(),
    )
    jobs.enqueue_dispatch(request.tenant_id, order.id)
    return _order_row(order, line_count=_count_lines(order))


@router.post(
    "/purchase-orders/{order_id}/discard", response=OrderRow, auth=owner_or_admin
)
def discard_purchase_order(request, order_id: uuid.UUID):
    """Terminal, and **not a failure**: the badge is neutral (§B.7.4)."""
    order = _order(request, order_id)
    before = order.status
    try:
        order_service.discard(order)
    except order_service.Refused as refusal:
        raise HttpError(409, str(refusal)) from refusal
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.REJECT,
        entity_type="purchase_orders",
        entity_id=order.id,
        before={"status": before},
        after={"status": order.status, "number": order.number},
        request_id=request_id.get(),
    )
    return _order_row(order, line_count=_count_lines(order))


@router.post(
    "/purchase-orders/{order_id}/mark-sent", response=OrderRow, auth=owner_or_admin
)
def mark_purchase_order_sent(request, order_id: uuid.UUID):
    """Record a dispatch Botica did not make.

    The supplier has no address on file, or the buyer phoned it in. Either is
    ordinary, and an order stuck at **Aprobada** because we could not send it is
    an order nobody can receive against.
    """
    order = _order(request, order_id)
    if order.status not in (
        PurchaseOrderStatus.APPROVED,
        PurchaseOrderStatus.SENT,
    ):
        raise HttpError(
            409,
            "Solo se marca como enviada una orden aprobada que todavía no salió.",
        )
    before = order.status
    order_service.mark_sent(order)
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.SEND,
        entity_type="purchase_orders",
        entity_id=order.id,
        before={"status": before},
        after={"status": order.status, "number": order.number, "by": "hand"},
        request_id=request_id.get(),
    )
    return _order_row(order, line_count=_count_lines(order))


# ---------------------------------------------------------------------------
# Receiving and supplier returns
# ---------------------------------------------------------------------------


def _receipt_row(receipt, *, lines=None) -> dict:
    body = {
        "id": receipt.id,
        "purchase_order_id": receipt.purchase_order_id,
        "order_number": (
            receipt.purchase_order.number if receipt.purchase_order_id else None
        ),
        "location_id": receipt.location_id,
        "location_name": receipt.location.name,
        "supplier_id": receipt.supplier_id,
        "supplier_name": receipt.supplier.name,
        "type": receipt.type,
        "status": receipt.status,
        "received_at": receipt.received_at,
        "received_by_name": receipt.received_by_name,
        "supplier_document_number": receipt.supplier_document_number,
        "notes": receipt.notes,
        "confirmed_at": receipt.confirmed_at,
        "line_count": len(lines) if lines is not None else receipt.lines.count(),
    }
    if lines is not None:
        body["lines"] = [_receipt_line_row(one) for one in lines]
    return body


def _receipt_line_row(line) -> dict:
    ordered = (
        line.purchase_order_line.approved_quantity
        if line.purchase_order_line_id
        else None
    )
    return {
        "id": line.id,
        "purchase_order_line_id": line.purchase_order_line_id,
        "item_id": line.item_id,
        "item_name": line.item.name,
        "presentation": line.item.presentation,
        "tracks_lots": line.item.tracks_lots,
        "tracks_expiry": line.item.tracks_expiry,
        "ordered_quantity": ordered,
        "quantity": line.quantity,
        "lot_id": line.lot_id,
        "lot_code": line.lot_code,
        "expires_at": line.expires_at,
        "unit_cost": line.unit_cost,
    }


def _receipt_lines(receipt):
    return list(
        GoodsReceiptLine.objects.filter(goods_receipt=receipt)
        .select_related("item", "purchase_order_line")
        .order_by("item__name")
    )


@router.get("/goods-receipts", response=Page[GoodsReceiptRow], auth=owner_or_admin)
def list_goods_receipts(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    purchase_order_id: uuid.UUID | None = Query(None),
    supplier_id: uuid.UUID | None = Query(None),
    location_id: list[uuid.UUID] | None = Query(None),
    type: ReceiptTypeValue | None = Query(None),
    status: ReceiptStatusValue | None = Query(None),
):
    queryset = (
        GoodsReceipt.objects.select_related("location", "supplier", "purchase_order")
        .filter(tenant_id=request.tenant_id)
        .filter(location_id__in=_readable(request, location_id))
        .order_by("-received_at")
    )
    if purchase_order_id:
        queryset = queryset.filter(purchase_order_id=purchase_order_id)
    if supplier_id:
        queryset = queryset.filter(supplier_id=supplier_id)
    if type:
        queryset = queryset.filter(type=type)
    if status:
        queryset = queryset.filter(status=status)
    rows, row_count, page, page_size = paginate(
        queryset, page=page, page_size=page_size, sort=None, order="desc", sortable={}
    )
    return {
        "rows": [_receipt_row(one) for one in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/goods-receipts/{receipt_id}", response=GoodsReceiptDetail, auth=owner_or_admin
)
def read_goods_receipt(request, receipt_id: uuid.UUID):
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related("location", "supplier", "purchase_order"),
        id=receipt_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, [receipt.location_id])
    return _receipt_row(receipt, lines=_receipt_lines(receipt))


@router.post("/goods-receipts", response=GoodsReceiptDetail, auth=owner_or_admin)
def create_goods_receipt(request, payload: GoodsReceiptIn):
    """Open a `draft` receipt against an order, or a standalone supplier return.

    Nothing else. A receipt with neither an order nor a return behind it would
    be merchandise arriving from nowhere, and S3's `Cargar mercancía` is the
    screen that records that with the reason it requires.
    """
    if payload.purchase_order_id:
        order = _order(request, payload.purchase_order_id)
        try:
            receipt = receiving.open_against(
                order,
                actor=request.user,
                received_at=payload.received_at,
                supplier_document_number=payload.supplier_document_number or "",
            )
        except receiving.Refused as refusal:
            raise HttpError(409, str(refusal)) from refusal
    else:
        if (payload.type or GoodsReceiptType.RECEIPT) != (
            GoodsReceiptType.SUPPLIER_RETURN
        ):
            raise HttpError(
                422,
                "Una recepción se abre contra una orden. Para mercancía que "
                "llegó sin orden use Inventario · Cargar mercancía.",
            )
        if not payload.location_id or not payload.supplier_id:
            raise HttpError(
                422, "Una devolución al proveedor necesita sede y proveedor."
            )
        _readable(request, [payload.location_id])
        receipt = GoodsReceipt.objects.create(
            tenant_id=request.tenant_id,
            location_id=payload.location_id,
            supplier_id=payload.supplier_id,
            type=GoodsReceiptType.SUPPLIER_RETURN,
            status=GoodsReceiptStatus.DRAFT,
            received_at=payload.received_at or timezone.now(),
            received_by=request.user,
            received_by_name=request.user.name,
            supplier_document_number=payload.supplier_document_number or "",
            notes=payload.notes or "",
        )
        _write_lines(request, receipt, payload.lines or [])

    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="goods_receipts",
        entity_id=receipt.id,
        after={
            "type": receipt.type,
            "order": payload.purchase_order_id and str(payload.purchase_order_id),
        },
        request_id=request_id.get(),
    )
    receipt = GoodsReceipt.objects.select_related(
        "location", "supplier", "purchase_order"
    ).get(id=receipt.id)
    return _receipt_row(receipt, lines=_receipt_lines(receipt))


def _write_lines(request, receipt, payload_lines):
    """Replace a draft's lines with what the screen holds.

    Replace rather than merge: the receiving screen is a form over a document
    somebody is typing, and a merge would leave a line nobody can see and nobody
    deleted -- which is stock arriving that no person on the screen agreed to.
    """
    items = {
        item.id: item
        for item in Item.objects.filter(
            tenant_id=request.tenant_id,
            id__in=[line.item_id for line in payload_lines if line.item_id],
        )
    }
    order_lines = {
        line.id: line
        for line in PurchaseOrderLine.objects.filter(
            tenant_id=request.tenant_id,
            id__in=[
                line.purchase_order_line_id
                for line in payload_lines
                if line.purchase_order_line_id
            ],
        )
    }
    fresh = []
    for position, line in enumerate(payload_lines, start=1):
        order_line = order_lines.get(line.purchase_order_line_id)
        if order_line is not None and (
            order_line.purchase_order_id != receipt.purchase_order_id
        ):
            # A line of one order cannot be received against another: the
            # shortfall would settle the wrong document, and the two would then
            # disagree about what the supplier still owes.
            raise HttpError(
                422,
                "Una línea de la recepción nombra una línea de otra orden.",
            )
        item = items.get(line.item_id) or (order_line.item if order_line else None)
        if item is None:
            raise HttpError(422, "Una línea de la recepción no nombra un producto.")
        if line.quantity <= 0:
            # A line of nothing is a line the operator meant to delete, and the
            # screen deletes it rather than sending a zero.
            continue
        fresh.append(
            GoodsReceiptLine(
                tenant_id=request.tenant_id,
                goods_receipt=receipt,
                purchase_order_line=order_line,
                item=item,
                quantity=line.quantity,
                lot_code=line.lot_code or "",
                expires_at=line.expires_at,
                unit_cost=line.unit_cost,
            )
        )
        del position
    GoodsReceiptLine.objects.filter(goods_receipt=receipt).delete()
    if fresh:
        GoodsReceiptLine.objects.bulk_create(fresh, batch_size=500)


@router.patch(
    "/goods-receipts/{receipt_id}", response=GoodsReceiptDetail, auth=owner_or_admin
)
def patch_goods_receipt(request, receipt_id: uuid.UUID, payload: GoodsReceiptPatchIn):
    """Edit a draft's lines -- quantities, lot codes, expiries, unit costs."""
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related("location", "supplier", "purchase_order"),
        id=receipt_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, [receipt.location_id])
    if receipt.status == GoodsReceiptStatus.CONFIRMED:
        raise HttpError(
            409,
            "Esta recepción ya fue confirmada y movió existencias. Corrija con "
            "un ajuste en Inventario o con una devolución al proveedor.",
        )
    fields = payload.dict(exclude_unset=True)
    changed = {}
    for key in ("received_at", "supplier_document_number", "notes"):
        if fields.get(key) is not None:
            changed[key] = fields[key]
    if changed:
        GoodsReceipt.objects.filter(id=receipt.id).update(**changed)
        for key, value in changed.items():
            setattr(receipt, key, value)
    if payload.lines is not None:
        _write_lines(request, receipt, payload.lines)
    return _receipt_row(receipt, lines=_receipt_lines(receipt))


@router.post(
    "/goods-receipts/{receipt_id}/confirm",
    response=GoodsReceiptDetail,
    auth=owner_or_admin,
)
def confirm_goods_receipt(request, receipt_id: uuid.UUID):
    """The atomic act: lots, one ledger call per line, cost, lead time, status.

    **Idempotent on the receipt** -- a retried confirmation moves no stock twice,
    and the guarantee is doubled: the status check returns early on the ordinary
    double click, and every move's `client_uuid` is derived from
    `(receipt, line)` so two requests that raced past it collide in the ledger's
    own unique index.
    """
    receipt = get_object_or_404(
        GoodsReceipt.objects.select_related("location", "supplier", "purchase_order"),
        id=receipt_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, [receipt.location_id])
    already = receipt.status == GoodsReceiptStatus.CONFIRMED
    try:
        receiving.confirm(receipt, actor=request.user, request_id=request_id.get())
    except receiving.Refused as refusal:
        raise LineRefused(str(refusal), line=refusal.line, field=refusal.field)
    if not already:
        jobs.enqueue_lead_time(request.tenant_id, receipt.id, receipt.supplier_id)
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.UPDATE,
            entity_type="goods_receipts",
            entity_id=receipt.id,
            before={"status": GoodsReceiptStatus.DRAFT},
            after={
                "status": GoodsReceiptStatus.CONFIRMED,
                "type": receipt.type,
                "order": (
                    receipt.purchase_order.number if receipt.purchase_order else None
                ),
            },
            request_id=request_id.get(),
        )
    receipt = GoodsReceipt.objects.select_related(
        "location", "supplier", "purchase_order"
    ).get(id=receipt.id)
    return _receipt_row(receipt, lines=_receipt_lines(receipt))


# ---------------------------------------------------------------------------
# The forecast, read
# ---------------------------------------------------------------------------


@router.get("/demand-forecasts", response=Page[ForecastRow], auth=owner_or_admin)
def list_demand_forecasts(
    request,
    location_id: uuid.UUID = Query(...),
    item_id: uuid.UUID | None = Query(None),
    category_id: uuid.UUID | None = Query(None),
    basis: list[BasisValue] | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
):
    """The current forecast for one sede.

    **One row per item per sede and no history**: what the model said on a given
    day survives on `purchase_order_lines`, stamped at generation.
    """
    _readable(request, [location_id])
    queryset = (
        DemandForecast.objects.select_related("item")
        .filter(tenant_id=request.tenant_id, location_id=location_id)
        .order_by("item__name")
    )
    if item_id:
        queryset = queryset.filter(item_id=item_id)
    if category_id:
        queryset = queryset.filter(item__category_id=category_id)
    if basis:
        queryset = queryset.filter(basis__in=list(basis))
    rows, row_count, page, page_size = paginate(
        queryset, page=page, page_size=page_size, sort=None, order="asc", sortable={}
    )
    return {
        "rows": [
            {
                "item_id": row.item_id,
                "item_name": row.item.name,
                "location_id": row.location_id,
                "basis": row.basis,
                "confidence": row.confidence,
                "band": forecast.band(row.confidence),
                "weekly_sales": row.weekly_sales,
                "trend": row.trend,
                "coverage_days": row.coverage_days,
                "reorder_point": row.reorder_point,
                "safety_stock": row.safety_stock,
                "usable_weeks": row.usable_weeks,
                "variation": row.variation,
                "imported_share": row.imported_share,
                "computed_at": row.computed_at,
                "model_version": row.model_version,
            }
            for row in rows
        ],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


# ---------------------------------------------------------------------------
# The settings group (rule 5)
# ---------------------------------------------------------------------------


@router.get("/settings/purchasing", response=PurchasingSettingsOut, auth=owner_or_admin)
def read_purchasing_settings(request):
    return purchasing_settings.read(_tenant(request))


@router.patch(
    "/settings/purchasing", response=PurchasingSettingsOut, auth=owner_or_admin
)
def write_purchasing_settings(request, payload: PurchasingSettingsIn):
    """One `jsonb_set`, every other group untouched (rule 5)."""
    tenant = _tenant(request)
    before = purchasing_settings.read(tenant)
    values = {
        key: value
        for key, value in payload.dict(exclude_unset=True).items()
        if value is not None
    }
    try:
        after = purchasing_settings.write(tenant, values)
    except purchasing_settings.Invalid as refusal:
        raise HttpError(422, str(refusal)) from refusal
    audit.record(
        actor=request.user,
        tenant_id=tenant.id,
        action=AuditAction.UPDATE,
        entity_type="settings.purchasing",
        entity_id=tenant.id,
        before=before,
        after=after,
        request_id=request_id.get(),
    )
    return after


def suggested_order_count(tenant_id, location_ids) -> int:
    """The `Compras` nav counter: orders at `suggested`, which is work waiting.

    **Never a total**, and zero renders nothing at all (§B.8.2).
    """
    if not location_ids:
        return 0
    return PurchaseOrder.objects.filter(
        tenant_id=tenant_id,
        location_id__in=location_ids,
        status=PurchaseOrderStatus.SUGGESTED,
    ).count()
