"""S4's endpoints, on the router `core.api` mounts.

Every path carries the `/api/` prefix and is English (§3), runs behind S0's
single permission dependency (§2) inside the pinned transaction (A1), applies
S0's location-scoping helper rather than its own filter (A2), and appends to
`audit_log` through S0's path on every elevated-role mutation (ledger).

**No endpoint here creates a counter sale.** Every sale, line, payment, shift
and return originates on a device and arrives through S2's
`POST /api/sync/push` (A5, §5 rule 5). A second write path would be a second way
to allocate `sales.number` and a second way to move stock, and rule 7 has
exactly one of the latter. What is here instead is the office's *read* of the
counter and its two corrections -- a void and a forced close -- each of which
moves a row a till already wrote.

**The counter never calls any of these.** It reads the same rows from the local
store at zero latency, which is the whole of §4's two-read-models boundary and
the reason none of §4's counter budgets contains a request.
"""

import uuid
from datetime import date
from typing import Literal

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit, scoping
from core.counter import sales as sale_service
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.middleware import request_id
from core.models import (
    AuditAction,
    Payment,
    Role,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    Shift,
    ShiftStatus,
)
from core.permissions import any_member, owner_or_admin

router = Router()

SortOrder = Literal["asc", "desc"]
SaleStatusValue = Literal["open", "closed", "voided"]
SaleSourceValue = Literal["counter", "imported"]
ShiftStatusValue = Literal["open", "closed"]
PaymentMethodValue = Literal["cash", "debit_card", "credit_card", "transfer", "other"]
VatClassValue = Literal["excluded", "exempt", "rate_5", "rate_19"]


def _readable(request, requested=None):
    return scoping.readable_locations(
        request.user, request.tenant_id, requested=requested
    )


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class SaleRow(Schema):
    id: uuid.UUID
    number: str
    location_id: uuid.UUID
    location_name: str
    shift_id: uuid.UUID | None
    status: SaleStatusValue
    source: SaleSourceValue
    occurred_at: object
    recorded_at: object
    item_count: int
    total: object
    #: Every method applied to the ticket, in the order they are rendered. The
    #: `Medio` column reads this; a split payment is two words and not a
    #: truncation of one.
    methods: list[PaymentMethodValue]
    customer_id: uuid.UUID | None
    customer_name: str | None
    sold_by_name: str
    device_id: uuid.UUID | None
    device_label: str | None
    returned: bool


class SaleLineOut(Schema):
    id: uuid.UUID
    position: int
    item_id: uuid.UUID
    item_name: str
    presentation: str
    lot_id: uuid.UUID | None
    lot_code: str | None
    quantity: int
    unit_price: object
    discount: object
    vat_class: VatClassValue
    tax_amount: object
    unit_cost: object
    from_suggestion: bool
    returned_quantity: int


class PaymentOut(Schema):
    id: uuid.UUID
    method: PaymentMethodValue
    amount: object
    reference: str


class ReturnRow(Schema):
    id: uuid.UUID
    number: str
    sale_id: uuid.UUID
    sale_number: str
    location_id: uuid.UUID
    location_name: str
    occurred_at: object
    recorded_at: object
    item_count: int
    total: object
    tax: object
    reason: str
    refund_method: PaymentMethodValue
    returned_by_name: str


class ReturnLineOut(Schema):
    id: uuid.UUID
    sale_line_id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    lot_id: uuid.UUID | None
    lot_code: str | None
    quantity: int
    unit_price: object
    discount: object
    vat_class: VatClassValue
    tax_amount: object


class ReturnDetail(ReturnRow):
    lines: list[ReturnLineOut]


class SaleDetail(SaleRow):
    subtotal: object
    discount: object
    tax: object
    void_reason: str
    lines: list[SaleLineOut]
    payments: list[PaymentOut]
    returns: list[ReturnRow]


class ShiftRow(Schema):
    id: uuid.UUID
    location_id: uuid.UUID
    location_name: str
    device_id: uuid.UUID | None
    device_label: str | None
    user_id: uuid.UUID | None
    user_name: str
    opened_at: object
    closed_at: object
    status: ShiftStatusValue
    opening_float: object
    cash_sales: object
    cash_returns: object
    expected_total: object
    declared_total: object
    variance: object
    #: A forced close is not a count and is never rendered as one (§6). The
    #: reason travels with the row so the list can say so without reading
    #: `audit_log`.
    forced_close_reason: str
    sale_count: int


class ShiftPaymentOut(Schema):
    method: PaymentMethodValue
    amount: object


class ShiftDetail(ShiftRow):
    payments: list[ShiftPaymentOut]
    sales: list[SaleRow]


class VoidIn(Schema):
    reason: str = ""


class ForceCloseIn(Schema):
    reason: str


SALE_SORTS = {
    "number": ["number"],
    "location": ["location__name"],
    "occurred_at": ["occurred_at"],
    "recorded_at": ["recorded_at"],
    "total": ["total"],
    "status": ["status"],
}

RETURN_SORTS = {
    "number": ["number"],
    "location": ["location__name"],
    "occurred_at": ["occurred_at"],
    "recorded_at": ["recorded_at"],
    "total": ["total"],
}

SHIFT_SORTS = {
    "location": ["location__name"],
    "opened_at": ["opened_at"],
    "closed_at": ["closed_at"],
    "variance": ["variance"],
    "status": ["status"],
}


# ---------------------------------------------------------------------------
# Ventas
# ---------------------------------------------------------------------------


def _sale_queryset(
    request, *, location_id, shift_id, status, source, since, until, q, sold_by
):
    rows = Sale.objects.filter(
        tenant_id=request.tenant_id,
        location_id__in=_readable(request, requested=location_id),
    ).select_related("location", "device", "customer")
    if shift_id:
        rows = rows.filter(shift_id=shift_id)
    if status:
        rows = rows.filter(status=status)
    if source:
        rows = rows.filter(source=source)
    if sold_by:
        rows = rows.filter(sold_by_user_id=sold_by)
    if since:
        # **`recorded_at` and not `occurred_at`.** The device's clock is
        # displayed to the operator and never used for accounting (rule 8): a
        # period filter is an accounting question, and a till three hours behind
        # would otherwise move its own sales into yesterday.
        rows = rows.filter(recorded_at__date__gte=since)
    if until:
        rows = rows.filter(recorded_at__date__lte=until)
    if q and q.strip():
        rows = rows.filter(number__icontains=q.strip())
    return rows


def _sale_extras(rows):
    """Per-sale item counts, payment methods and whether anything came back.

    Three aggregates in three queries over the page rather than three per row:
    the `Ítems`, `Medio` and `Estado` columns are on every line of the list, and
    a per-row query is what makes a twenty-five-row grid a seventy-five-query
    page.
    """
    ids = [row.id for row in rows]
    if not ids:
        return {}, {}, set()
    counts = {
        entry["sale_id"]: entry["units"] or 0
        for entry in SaleLine.objects.filter(sale_id__in=ids)
        .values("sale_id")
        .annotate(units=Sum("quantity"))
    }
    methods: dict = {}
    for entry in (
        Payment.objects.filter(sale_id__in=ids)
        .values("sale_id", "method")
        .annotate(total=Count("id"))
        .order_by("method")
    ):
        methods.setdefault(entry["sale_id"], []).append(entry["method"])
    returned = set(
        SaleReturn.objects.filter(sale_id__in=ids).values_list("sale_id", flat=True)
    )
    return counts, methods, returned


def _sale_row(row, counts, methods, returned):
    return {
        "id": row.id,
        "number": row.number,
        "location_id": row.location_id,
        "location_name": row.location.name,
        "shift_id": row.shift_id,
        "status": row.status,
        "source": row.source,
        "occurred_at": row.occurred_at,
        "recorded_at": row.recorded_at,
        "item_count": counts.get(row.id, 0),
        "total": row.total,
        "methods": methods.get(row.id, []),
        "customer_id": row.customer_id,
        "customer_name": (
            (row.customer.name or "Cliente eliminado") if row.customer_id else None
        ),
        "sold_by_name": row.sold_by_name,
        "device_id": row.device_id,
        "device_label": row.device.label if row.device else None,
        "returned": row.id in returned,
    }


@router.get("/sales", response=Page[SaleRow], auth=any_member)
def list_sales(
    request,
    location_id: list[uuid.UUID] = Query(None),
    shift_id: uuid.UUID | None = Query(None),
    status: SaleStatusValue | None = Query(None),
    source: SaleSourceValue | None = Query(None),
    sold_by_user_id: uuid.UUID | None = Query(None),
    since: date | None = Query(None),
    until: date | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
):
    """The sale list the office reads.

    A `cashier` reads their own home sede and no other, which is the scoped mode
    of S0's helper rather than a filter written here (A2) -- and a cashier at a
    till does not read this at all: the counter answers the same question from
    its local store at zero latency (§4).
    """
    queryset = _sale_queryset(
        request,
        location_id=location_id,
        shift_id=shift_id,
        status=status,
        source=source,
        since=since,
        until=until,
        q=q,
        sold_by=sold_by_user_id,
    )
    rows, row_count, page, page_size = paginate(
        queryset.order_by("-recorded_at", "-id"),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=SALE_SORTS,
    )
    counts, methods, returned = _sale_extras(rows)
    return {
        "rows": [_sale_row(row, counts, methods, returned) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _sale(request, sale_id):
    sale = get_object_or_404(
        Sale.objects.select_related("location", "device", "customer"),
        id=sale_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, requested=[sale.location_id])
    return sale


@router.get("/sales/{sale_id}", response=SaleDetail, auth=any_member)
def read_sale(request, sale_id: uuid.UUID):
    """One sale with its lines, payments and returns. What the record panel
    renders, and what a devolución is started from."""
    sale = _sale(request, sale_id)
    elevated = request.user.role in (Role.OWNER, Role.ADMIN, Role.PLATFORM_ADMIN)
    counts, methods, returned = _sale_extras([sale])
    remaining = sale_service.returnable(sale)
    lines = sale.lines.select_related("item", "lot").order_by("position")
    return {
        **_sale_row(sale, counts, methods, returned),
        "subtotal": sale.subtotal,
        "discount": sale.discount,
        "tax": sale.tax,
        "void_reason": sale.void_reason,
        "lines": [
            {
                "id": line.id,
                "position": line.position,
                "item_id": line.item_id,
                "item_name": line.item.name,
                "presentation": line.item.presentation,
                "lot_id": line.lot_id,
                "lot_code": line.lot.lot_code if line.lot else None,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "discount": line.discount,
                "vat_class": line.vat_class,
                "tax_amount": line.tax_amount,
                # **A cashier never receives a cost figure from the API**, which
                # is the rule S1 fixed on the catalog and which this record
                # panel would otherwise walk around: a ticket's lines carry the
                # lot's acquisition cost, stamped.
                "unit_cost": line.unit_cost if elevated else None,
                "from_suggestion": line.from_suggestion,
                "returned_quantity": line.quantity - remaining.get(line.id, 0),
            }
            for line in lines
        ],
        "payments": [
            {
                "id": payment.id,
                "method": payment.method,
                "amount": payment.amount,
                "reference": payment.reference,
            }
            for payment in sale.payments.all()
        ],
        "returns": _return_rows(
            list(sale.returns.select_related("location", "sale").all())
        ),
    }


@router.post("/sales/{sale_id}/void", response=SaleDetail, auth=owner_or_admin)
def void_sale(request, sale_id: uuid.UUID, payload: VoidIn):
    """Void a sale already received by the server.

    **The cashier's own same-shift void is a client write and does not call
    this** -- a mis-keyed ticket at 10:14 is corrected at 10:15 by the person who
    made it, and once the turno closes only an `owner` or an `admin` may void. A
    permissive void is how a till is robbed; a strictly-office void means a
    mis-key at 20:00 waits for Monday.

    No row is deleted: the reversing moves go through S3's service and
    `status` becomes `voided` (ledger, rule 7).
    """
    sale = _sale(request, sale_id)
    if sale.status == SaleStatus.VOIDED:
        raise HttpError(409, f"La venta {sale.number} ya está anulada.")
    if sale.returns.exists():
        raise HttpError(
            409,
            f"La venta {sale.number} tiene devoluciones registradas. Anúlelas "
            "primero o corrija por devolución.",
        )
    before = {"status": sale.status, "total": str(sale.total)}
    sale_service.void(
        sale,
        actor=request.user,
        device=sale.device,
        request_id=request_id.get(),
        reason=payload.reason,
    )
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="sales",
        entity_id=sale.id,
        before=before,
        after={
            "status": sale.status,
            "total": str(sale.total),
            "reason": sale.void_reason,
        },
        request_id=request_id.get(),
    )
    return read_sale(request, sale.id)


# ---------------------------------------------------------------------------
# Devoluciones
# ---------------------------------------------------------------------------


def _return_row(row, units):
    return {
        "id": row.id,
        "number": row.number,
        "sale_id": row.sale_id,
        "sale_number": row.sale.number,
        "location_id": row.location_id,
        "location_name": row.location.name,
        "occurred_at": row.occurred_at,
        "recorded_at": row.recorded_at,
        "item_count": units.get(row.id, 0),
        "total": row.total,
        "tax": row.tax,
        "reason": row.reason,
        "refund_method": row.refund_method,
        "returned_by_name": row.returned_by_name,
    }


@router.get("/sale-returns", response=Page[ReturnRow], auth=any_member)
def list_returns(
    request,
    location_id: list[uuid.UUID] = Query(None),
    sale_id: uuid.UUID | None = Query(None),
    since: date | None = Query(None),
    until: date | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
):
    """The return list, same contract and same filters as the sale list."""
    rows = SaleReturn.objects.filter(
        tenant_id=request.tenant_id,
        location_id__in=_readable(request, requested=location_id),
    ).select_related("location", "sale")
    if sale_id:
        rows = rows.filter(sale_id=sale_id)
    if since:
        rows = rows.filter(recorded_at__date__gte=since)
    if until:
        rows = rows.filter(recorded_at__date__lte=until)
    if q and q.strip():
        term = q.strip()
        rows = rows.filter(number__icontains=term) | rows.filter(
            sale__number__icontains=term
        )
    page_rows, row_count, page, page_size = paginate(
        rows.order_by("-recorded_at", "-id"),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=RETURN_SORTS,
    )
    return {
        "rows": _return_rows(page_rows),
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _return_rows(rows):
    """The list's rows, with the unit count aggregated once over the page."""
    ids = [row.id for row in rows]
    units = (
        {
            entry["sale_return_id"]: entry["units"] or 0
            for entry in SaleReturnLine.objects.filter(sale_return_id__in=ids)
            .values("sale_return_id")
            .annotate(units=Sum("quantity"))
        }
        if ids
        else {}
    )
    return [_return_row(row, units) for row in rows]


@router.get("/sale-returns/{return_id}", response=ReturnDetail, auth=any_member)
def read_return(request, return_id: uuid.UUID):
    """One return with its lines and the sale it reverses.

    **S5 reads this to build the return's credit note inside the canonical
    document** (ledger, cross-stage services). It carries the money as it was
    originally charged, so nobody re-derives a peso from a price list.
    """
    row = get_object_or_404(
        SaleReturn.objects.select_related("location", "sale"),
        id=return_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, requested=[row.location_id])
    return {
        **_return_rows([row])[0],
        "lines": [
            {
                "id": line.id,
                "sale_line_id": line.sale_line_id,
                "item_id": line.item_id,
                "item_name": line.item.name,
                "lot_id": line.lot_id,
                "lot_code": line.lot.lot_code if line.lot else None,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
                "discount": line.discount,
                "vat_class": line.vat_class,
                "tax_amount": line.tax_amount,
            }
            for line in row.lines.select_related("item", "lot").all()
        ],
    }


# ---------------------------------------------------------------------------
# Turnos
# ---------------------------------------------------------------------------


def _shift_row(row, counts):
    report = sale_service.shift_report(row)
    return {
        "id": row.id,
        "location_id": row.location_id,
        "location_name": row.location.name,
        "device_id": row.device_id,
        "device_label": row.device.label if row.device else None,
        "user_id": row.user_id,
        "user_name": row.user_name,
        "opened_at": row.opened_at,
        "closed_at": row.closed_at,
        "status": row.status,
        "forced_close_reason": row.forced_close_reason,
        "sale_count": counts.get(row.id, 0),
        **report,
    }


def _shift_sale_counts(rows):
    ids = [row.id for row in rows]
    if not ids:
        return {}
    return {
        entry["shift_id"]: entry["total"]
        for entry in Sale.objects.filter(shift_id__in=ids)
        .values("shift_id")
        .annotate(total=Count("id"))
    }


@router.get("/shifts", response=Page[ShiftRow], auth=any_member)
def list_shifts(
    request,
    location_id: list[uuid.UUID] = Query(None),
    device_id: uuid.UUID | None = Query(None),
    status: ShiftStatusValue | None = Query(None),
    since: date | None = Query(None),
    until: date | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
):
    """The turno list: who opened, when, the float, the count, the recomputed
    expectation and the difference between the last two.

    `Diferencia` is **recomputed on every read** rather than trusted from the
    stored figure, because the stored one was computed when the shift closed and
    an offline sale attributed to that turno may have arrived since (§5). The
    two agree on a drained till, which is the point of showing both.
    """
    rows = Shift.objects.filter(
        tenant_id=request.tenant_id,
        location_id__in=_readable(request, requested=location_id),
    ).select_related("location", "device")
    if device_id:
        rows = rows.filter(device_id=device_id)
    if status:
        rows = rows.filter(status=status)
    if since:
        rows = rows.filter(opened_at__date__gte=since)
    if until:
        rows = rows.filter(opened_at__date__lte=until)
    page_rows, row_count, page, page_size = paginate(
        rows.order_by("-opened_at", "-id"),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=SHIFT_SORTS,
    )
    counts = _shift_sale_counts(page_rows)
    return {
        "rows": [_shift_row(row, counts) for row in page_rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _shift(request, shift_id):
    shift = get_object_or_404(
        Shift.objects.select_related("location", "device"),
        id=shift_id,
        tenant_id=request.tenant_id,
    )
    _readable(request, requested=[shift.location_id])
    return shift


@router.get("/shifts/{shift_id}", response=ShiftDetail, auth=any_member)
def read_shift(request, shift_id: uuid.UUID):
    """One turno: its sales, its takings by method, and the close report."""
    shift = _shift(request, shift_id)
    counts = _shift_sale_counts([shift])
    sales = list(
        Sale.objects.filter(tenant_id=request.tenant_id, shift_id=shift.id)
        .select_related("location", "device", "customer")
        .order_by("-recorded_at")
    )
    line_counts, methods, returned = _sale_extras(sales)
    return {
        **_shift_row(shift, counts),
        "payments": sale_service.payment_breakdown(shift),
        "sales": [_sale_row(row, line_counts, methods, returned) for row in sales],
    }


@router.post(
    "/shifts/{shift_id}/force-close", response=ShiftDetail, auth=owner_or_admin
)
def force_close_shift(request, shift_id: uuid.UUID, payload: ForceCloseIn):
    """Close a turno whose device is gone or whose cashier left without counting.

    **It leaves `declared_total` and `variance` null.** A forced close is not a
    count and is never rendered as one; a zero here would claim an empty drawer
    was counted, which is a different and much worse thing to record.

    The daily `stale_shift_notice` job does not do this and never will: closing a
    cash session without a count destroys the count, which is the one number the
    session exists to produce.
    """
    shift = _shift(request, shift_id)
    if shift.status != ShiftStatus.OPEN:
        raise HttpError(409, "Ese turno ya está cerrado.")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HttpError(422, "Un cierre forzado lleva el motivo por escrito.")
    before = {"status": shift.status, "opened_at": shift.opened_at.isoformat()}
    sale_service.force_close(shift, reason=reason, at=timezone.now())
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="shifts",
        entity_id=shift.id,
        before=before,
        after={
            "status": shift.status,
            "closed_at": shift.closed_at.isoformat() if shift.closed_at else None,
            "reason": shift.forced_close_reason,
            "declared_total": None,
        },
        request_id=request_id.get(),
    )
    return read_shift(request, shift.id)
