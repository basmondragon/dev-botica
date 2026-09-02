"""S3's endpoints, on the router `core.api` mounts.

Every path carries the `/api/` prefix and is English (§3), runs behind S0's
single permission dependency (§2) inside the pinned transaction (A1), applies
S0's location-scoping helper rather than its own filter (A2), and appends to
`audit_log` through S0's path on every elevated-role mutation (ledger).

**Two shape rules bind this stage in particular.**

There is **no endpoint that writes `stock_on_hand`**, at any path, for any role.
The projection has no API (rule 7); it is maintained by `core.inventory.ledger`
inside the same transaction as the moves that change it, and the rebuild is a
management command rather than a route for the same reason.

And **`POST /api/stock-moves` accepts three types and no others**, because every
other type in the enum is the consequence of a document that has its own
endpoint. A general "write me a move" endpoint would be the exact hole through
which a future stage bypasses the service.
"""

import csv
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from django.db import connection, transaction
from django.db.models import Count, DecimalField, F, Max, Q, Sum, Value
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit, scoping
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.inventory import ledger, settings as inventory_settings, states
from core.middleware import request_id
from core.models import (
    DIRECT_MOVE_TYPES,
    AuditAction,
    Category,
    CountScope,
    CountStatus,
    Item,
    Location,
    Lot,
    PolicySource,
    Role,
    StockCount,
    StockCountLine,
    StockMove,
    StockMoveType,
    StockOnHand,
    StockPolicy,
    SyncConflict,
    SyncConflictStatus,
    SyncConflictType,
    Tenant,
    Transfer,
    TransferLine,
    TransferResolution,
    TransferStatus,
)
from core.permissions import any_member, owner_or_admin

router = Router()

SortOrder = Literal["asc", "desc"]
StateValue = Literal[
    "expired",
    "stockout",
    "expiring_urgent",
    "expiring",
    "reorder_point",
    "overstock",
    "sufficient",
]
ExpiryFilter = Literal["expired", "valuation", "alert", "notice", "none"]
DirectMoveType = Literal["adjustment", "shrinkage", "expiry"]
MoveTypeValue = Literal[
    "receipt",
    "sale",
    "customer_return",
    "supplier_return",
    "transfer_out",
    "transfer_in",
    "adjustment",
    "shrinkage",
    "expiry",
    "count",
]
ReasonValue = Literal[
    "opening_stock",
    "standalone_receipt",
    "correction",
    "damage",
    "theft",
    "loss",
    "expired",
    "count_adjustment",
    "negative_resolution",
]
TransferStatusValue = Literal["draft", "dispatched", "received", "partial"]
ResolutionValue = Literal["received_late", "lost_in_transit"]
CountScopeValue = Literal["full", "category", "item_list"]
CountStatusValue = Literal["draft", "counting", "closed"]
PolicySourceValue = Literal["manual", "model"]

#: The reasons a `cashier` may write, and the types they may write them on. A
#: negative movement a cashier can point at on a shelf -- a broken bottle, an
#: expired blister -- is something the person who found it should record while it
#: is in their hand, and requiring a regente for it means it never gets
#: recorded. **A positive adjustment is the one movement in the product that
#: creates value out of nothing**, and it is the exact shape of a loss being
#: covered, so it stays with an elevated role (design-system §B.17·3, answered).
CASHIER_MOVE_TYPES = (StockMoveType.SHRINKAGE, StockMoveType.EXPIRY)


def _tenant(request):
    tenant = Tenant.objects.filter(id=request.tenant_id).first()
    if tenant is None:
        raise HttpError(403, "No hay una droguería seleccionada en esta sesión.")
    return tenant


def _options(request):
    return inventory_settings.read(_tenant(request))


def _elevated(user):
    return user.role in (Role.OWNER, Role.ADMIN, Role.PLATFORM_ADMIN)


def _require_elevated(user, what):
    if not _elevated(user):
        raise HttpError(403, f"{what} requiere el perfil Propietaria o Administradora.")


def _readable(request, requested=None, network_read=False):
    return scoping.readable_locations(
        request.user,
        request.tenant_id,
        requested=requested,
        network_read=network_read,
    )


def _writable_location(request, location_id):
    """The one location a write may name, checked through S0's helper (A2).

    A `cashier` reaches exactly their home sede; the office reaches every active
    one. An explicit location outside the identity's set is **rejected, not
    intersected away** -- a silently emptied write is indistinguishable from one
    that had nothing to do.
    """
    _readable(request, requested=[location_id])
    return location_id


def _today():
    """The pharmacy's calendar day.

    `localdate()` under `TIME_ZONE = America/Bogota`, so a lot expiring today is
    expiring today in the shop and not in UTC -- which at 19:00 Bogotá is
    already tomorrow, and would move every expiry badge in the network for five
    hours a day.
    """
    return timezone.localdate()


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class AvailabilityOut(Schema):
    location_id: uuid.UUID
    location_name: str
    quantity: int


class TransitAvailabilityOut(AvailabilityOut):
    """One sede's shelf **and its road**, for the item lookup only.

    §1 deliverable 11 · units dispatched here and not yet received are on no
    shelf, so they are never in `quantity` and never a state in the `Estado`
    column -- the record panel renders the figure beside the shelf one so the
    office can tell an empty sede from one whose box is in a van. Kept off the
    plain `AvailabilityOut` because the `Quiebre` clause's `hay 96 en Suba`
    counts what somebody can put in a customer's hand today, and a row in
    transit is exactly what they cannot.
    """

    in_transit: int = 0


class StockRow(Schema):
    """One row of Existencias, at `(item, location, lot)` grain -- which is
    `stock_on_hand`'s own key, exactly."""

    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    presentation: str
    unit: str
    tracks_lots: bool
    invima_status: str
    manufacturer_name: str | None
    location_id: uuid.UUID
    location_name: str
    lot_id: uuid.UUID | None
    lot_code: str | None
    expires_at: date | None
    quantity: int
    #: The in-cell bar's fill against `stock_policies.max_quantity`. **Null where
    #: no policy exists**: a bar with no capacity behind it is a bar measuring
    #: nothing, so the figure stands alone (§A.18.1, §B.12.3).
    bar_percentage: int | None
    state: StateValue
    state_ordinal: int
    reorder_point: int | None
    max_quantity: int | None
    min_quantity: int | None
    target_coverage_days: int | None
    #: `manual` or `model`, or null where the row carries no policy at all --
    #: the one column that tells a regente which numbers are theirs and which
    #: the model wrote (S6).
    policy_source: PolicySourceValue | None
    #: The `hay 96 en Suba` clause: **the single sede holding the most units**,
    #: ties broken by name so the string does not change between page loads. A
    #: badge listing four sedes is a badge nobody reads.
    elsewhere: AvailabilityOut | None


class StockPage(Page[StockRow]):
    #: The footer's `312 requieren acción`, counted over the same filters as the
    #: page and **before** pagination.
    action_required: int


class StateCountOut(Schema):
    state: StateValue
    ordinal: int
    count: int


class LocationSummaryOut(Schema):
    location_id: uuid.UUID
    location_name: str
    rows: int
    action_required: int
    states: list[StateCountOut]


class StockSummaryOut(Schema):
    rows: int
    action_required: int
    states: list[StateCountOut]
    locations: list[LocationSummaryOut]


class ItemAvailabilityOut(Schema):
    item_id: uuid.UUID
    item_name: str
    unit: str
    total: int
    #: The network's units on the road, summed over the same sedes.
    in_transit: int = 0
    by_location: list[TransitAvailabilityOut]
    lots: list["LotAvailabilityOut"]


class LotAvailabilityOut(Schema):
    lot_id: uuid.UUID | None
    lot_code: str | None
    expires_at: date | None
    location_id: uuid.UUID
    location_name: str
    quantity: int


class HorizonOut(Schema):
    key: Literal["valuation", "alert", "notice"]
    days: int
    value: Decimal
    lots: int


class ExpiringLocationOut(Schema):
    location_id: uuid.UUID
    location_name: str
    horizons: list[HorizonOut]
    #: The denominator. S9's `4,6% del inventario valorizado` tile is a ratio
    #: with no meaning without it, and returning it here is what stops S9
    #: computing a second valuation on a different basis.
    total_value: Decimal


class ExpiringOut(Schema):
    #: Every figure is `Σ quantity × lots.unit_cost`, **never sale price**: an
    #: inventory-at-risk figure priced at retail overstates the loss by the
    #: whole margin, and it is the number an owner would repeat to their
    #: accountant.
    horizons: list[HorizonOut]
    total_value: Decimal
    locations: list[ExpiringLocationOut]


class MoveOut(Schema):
    id: uuid.UUID
    location_id: uuid.UUID
    location_name: str
    item_id: uuid.UUID
    item_name: str
    lot_id: uuid.UUID | None
    lot_code: str | None
    quantity: int
    type: MoveTypeValue
    reason: str
    note: str
    document_type: str
    document_id: uuid.UUID | None
    unit_cost: Decimal | None
    occurred_at: datetime
    recorded_at: datetime
    device_id: uuid.UUID | None
    device_label: str | None
    user_id: uuid.UUID | None
    user_name: str
    fefo_override: bool


class MoveIn(Schema):
    """The only direct write. Three types and nothing else."""

    location_id: uuid.UUID
    item_id: uuid.UUID
    lot_id: uuid.UUID | None = None
    #: Always **positive**, in base units. The sign comes from the type: a
    #: `shrinkage` of 12 removes twelve, and asking a person to type a minus
    #: sign in a dialog whose button already reads `Restar 12 unidades` is how a
    #: double negative reaches the ledger.
    quantity: int
    type: DirectMoveType
    reason: ReasonValue
    note: str = ""
    #: A5 · a till's own key. Absent from the office, where the endpoint derives
    #: one, so a double-clicked confirm appends once.
    client_uuid: uuid.UUID | None = None
    occurred_at: datetime | None = None


class LotOut(Schema):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    lot_code: str
    expires_at: date | None
    supplier_id: uuid.UUID | None
    supplier_name: str | None
    unit_cost: Decimal | None
    invima_registration: str
    total: int
    by_location: list[AvailabilityOut]


class TraceRowOut(MoveOut):
    #: The running balance after this move, over the whole network. A final
    #: value that disagrees with the projection is a trace nobody can hand to an
    #: inspector.
    balance: int


class TraceOut(Schema):
    lot: LotOut
    moves: list[TraceRowOut]


class ReceiptLineIn(Schema):
    item_id: uuid.UUID
    lot_code: str = ""
    expires_at: date | None = None
    #: **Base units**, already converted from packs by the surface that took the
    #: entry. S1 fixes `unit` and `units_per_pack` per item and the ledger counts
    #: the unit that gets sold; `Cargar mercancía` asks in packs and shows both
    #: figures on the line before it is confirmed -- `12 cajas · 360 unidades`.
    quantity: int
    unit_cost: Decimal | None = None
    supplier_id: uuid.UUID | None = None
    client_uuid: uuid.UUID | None = None


class ReceiptIn(Schema):
    location_id: uuid.UUID
    #: A5 · **the entry's own key, minted by the surface and kept across a
    #: retry.** Without it the server derives each line's key from a
    #: `document_id` it mints per request, so a `Confirmar entrada` pressed
    #: twice -- or retried after a timeout that committed -- books the whole
    #: receipt again. The one failure A5 exists for is the one that actually
    #: happens.
    document_id: uuid.UUID | None = None
    lines: list[ReceiptLineIn]
    #: `opening_stock` for the first load of a sede, `standalone_receipt` for
    #: merchandise that arrived with no order behind it. **Neither is a
    #: `receipt` move**: the ledger fixes that type as caused by S6, so cost of
    #: goods must never be computed by filtering `type = 'receipt'` -- it reads
    #: `unit_cost` across every positive type.
    reason: Literal["opening_stock", "standalone_receipt"] = "standalone_receipt"
    note: str = ""
    occurred_at: datetime | None = None


class ReceiptOut(Schema):
    location_id: uuid.UUID
    document_id: uuid.UUID
    lines_written: int
    lines_duplicate: int
    lines_skipped: int
    moves: list[MoveOut]


class TransferLineOut(Schema):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    lot_id: uuid.UUID | None
    lot_code: str | None
    quantity_requested: int
    quantity_dispatched: int
    quantity_received: int
    in_transit: int
    resolution: str


class TransferOut(Schema):
    id: uuid.UUID
    number: int
    origin_location_id: uuid.UUID
    origin_location_name: str
    destination_location_id: uuid.UUID
    destination_location_name: str
    status: TransferStatusValue
    dispatched_at: datetime | None
    dispatched_by_name: str
    received_at: datetime | None
    received_by_name: str
    note: str
    references: int
    in_transit: int
    lines: list[TransferLineOut]


class TransferLineIn(Schema):
    item_id: uuid.UUID
    lot_id: uuid.UUID | None = None
    quantity_requested: int


class TransferIn(Schema):
    origin_location_id: uuid.UUID
    destination_location_id: uuid.UUID
    note: str = ""
    lines: list[TransferLineIn]


class TransferPatchIn(Schema):
    note: str | None = None
    lines: list[TransferLineIn] | None = None


class MovementLineIn(Schema):
    line_id: uuid.UUID
    quantity: int


class DispatchIn(Schema):
    #: Absent means every line at its requested quantity, which is the ordinary
    #: case. A picker who found fewer states what they actually put in the box.
    lines: list[MovementLineIn] | None = None


class ResolveIn(Schema):
    line_id: uuid.UUID
    resolution: ResolutionValue


class CountLineOut(Schema):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    lot_id: uuid.UUID | None
    lot_code: str | None
    expected_quantity: int
    counted_quantity: int
    difference: int
    unit_cost: Decimal | None
    entered_at: datetime
    #: Whether this line covers an open negative-stock exception. **This screen
    #: is where §5 says an oversell is resolved**, so the exception is shown on
    #: the line that resolves it rather than on a queue somewhere else.
    resolves_negative: bool


class NegativeOut(Schema):
    """One open negative-stock exception at this count's sede.

    §5 rule 2 raises the oversell to the office and **this screen is where it is
    resolved** -- so a count carries the sede's open exceptions whether or not
    anybody has counted that reference yet. Reading them off the count is what
    makes them reachable: a negative raised by a direct movement, a transfer
    receipt or the opening-stock command has no device behind it, so the
    device-scoped arrival queue on S2's own screen never shows it.
    """

    conflict_id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    lot_id: uuid.UUID | None
    lot_code: str | None
    quantity: int
    recorded_at: datetime
    #: Whether a line of this count already covers it.
    counted: bool


class CountOut(Schema):
    id: uuid.UUID
    location_id: uuid.UUID
    location_name: str
    scope: CountScopeValue
    category_id: uuid.UUID | None
    category_name: str | None
    status: CountStatusValue
    counted_by_name: str
    closed_by_name: str
    closed_at: datetime | None
    recorded_at: datetime
    lines_count: int
    differences: int
    difference_value: Decimal
    lines: list[CountLineOut]
    negatives: list[NegativeOut]


class CountIn(Schema):
    location_id: uuid.UUID
    scope: CountScopeValue = "full"
    category_id: uuid.UUID | None = None
    client_uuid: uuid.UUID | None = None
    occurred_at: datetime | None = None


class CountLineIn(Schema):
    item_id: uuid.UUID
    lot_id: uuid.UUID | None = None
    counted_quantity: int
    client_uuid: uuid.UUID | None = None
    occurred_at: datetime | None = None


class CountLinesIn(Schema):
    lines: list[CountLineIn]


class CountDueOut(Schema):
    location_id: uuid.UUID
    location_name: str
    last_closed_at: datetime | None
    due: bool


class CountPage(Page[CountOut]):
    """The list, **with the locations whose `count_cadence_days` has elapsed
    flagged as due** -- which is what the API surface asks this path for.

    It rides on the list's own envelope rather than on a second path, for the
    same reason `action_required` rides on Existencias': the due set is what the
    list is *for*, and a screen that had to ask twice would render its own
    heading before it knew what to put in it.
    """

    due_locations: list[CountDueOut]


class PolicyOut(Schema):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    location_id: uuid.UUID | None
    location_name: str | None
    min_quantity: int | None
    max_quantity: int | None
    reorder_point: int | None
    target_coverage_days: int | None
    source: PolicySourceValue


class PolicyIn(Schema):
    item_id: uuid.UUID
    location_id: uuid.UUID | None = None
    min_quantity: int | None = None
    max_quantity: int | None = None
    reorder_point: int | None = None
    target_coverage_days: int | None = None


class PolicyWriteIn(Schema):
    """One item or many, **always at `source = manual`**.

    A write over a `model` row is accepted and flips `source` back -- that is the
    point of the column, and it is what stops S6 quietly overwriting a threshold
    somebody set on purpose.
    """

    policies: list[PolicyIn]


class InventorySettingsOut(Schema):
    expiry_valuation_days: int
    expiry_alert_days: int
    expiry_notice_days: int
    fefo_override_policy: str
    negative_stock_block_outbound: bool
    count_cadence_days: int
    expiry_digest_recipients: list[str]


class InventorySettingsIn(Schema):
    expiry_valuation_days: int | None = None
    expiry_alert_days: int | None = None
    expiry_notice_days: int | None = None
    negative_stock_block_outbound: bool | None = None
    count_cadence_days: int | None = None
    expiry_digest_recipients: list[str] | None = None


# ---------------------------------------------------------------------------
# Existencias
# ---------------------------------------------------------------------------

#: Every column the grid offers, and the tie-break each carries. A sort with no
#: tie-break is a sort whose page boundaries move between requests, which the
#: `Estado` check reads back page by page and would catch as a false failure.
STOCK_SORTS = {
    "item": ["item__name", "location__name", "lot__lot_code"],
    "manufacturer": ["item__manufacturer__name", "item__name", "location__name"],
    "location": ["location__name", "item__name", "lot__lot_code"],
    "lot": ["lot__lot_code", "item__name", "location__name"],
    "expires_at": ["lot__expires_at", "item__name", "location__name"],
    "quantity": ["quantity", "item__name", "location__name"],
    # **`asc` is most urgent first**, because ordinal 1 is `Vencido`. The column
    # has one ordering and it is urgency; the SQL direction is how urgency is
    # spelled, not a second opinion about it.
    "state": ["state_ordinal", "item__name", "location__name"],
}

DEFAULT_STOCK_ORDER = ["item__name", "location__name", "lot__lot_code"]


def _stock_queryset(request, *, options, today, filters):
    """The Existencias queryset, filtered and annotated but not yet paged."""
    queryset = StockOnHand.objects.filter(tenant_id=request.tenant_id)
    # A2 · the chip's selection is **intersected** with the helper's set and
    # never replaces it. A grid that filtered by its own chip and forgot the
    # helper looks correct to every reviewer holding an owner account.
    queryset = queryset.filter(
        location_id__in=_readable(
            request, requested=filters.get("location_ids"), network_read=True
        )
    )
    term = (filters.get("q") or "").strip()
    if term:
        from core.catalog.search import fold

        folded = fold(term)
        queryset = queryset.filter(
            Q(item__search_name__contains=folded)
            | Q(item__manufacturer__search_name__contains=folded)
            | Q(lot__lot_code__istartswith=term)
        )
    if filters.get("category_id"):
        queryset = queryset.filter(
            Q(item__category_id=filters["category_id"])
            | Q(item__category__parent_id=filters["category_id"])
        )
    if filters.get("expiry"):
        queryset = queryset.filter(
            states.expiry_window(filters["expiry"], today=today, options=options)
        )
    queryset = states.annotate(
        queryset.select_related("item", "item__manufacturer", "location", "lot"),
        today=today,
        alert_days=options["expiry_alert_days"],
        notice_days=options["expiry_notice_days"],
    )
    if filters.get("state"):
        queryset = queryset.filter(state_ordinal=states.ORDINALS[filters["state"]])
    elif filters.get("action_required"):
        queryset = queryset.filter(
            state_ordinal__in=[states.ORDINALS[one] for one in states.ACTION_STATES]
        )
    return queryset


@router.get("/stock", response=StockPage, auth=any_member)
def list_stock(
    request,
    q: str | None = Query(None),
    location_id: list[uuid.UUID] = Query(None),
    category_id: uuid.UUID | None = Query(None),
    state: StateValue | None = Query(None),
    action_required: bool = Query(False),
    expiry: ExpiryFilter | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
):
    """The Existencias grid: one row per `(item, location, lot)`.

    A `cashier` reads it **network-wide and read-only**: §2 grants exactly that,
    so `network_read` is the mode here and the interface pre-selects their home
    sede in the `Sede` chip rather than confining the query.
    """
    options = _options(request)
    today = _today()
    filters = {
        "q": q,
        "location_ids": location_id,
        "category_id": category_id,
        "state": state,
        "action_required": action_required,
        "expiry": expiry,
    }
    queryset = _stock_queryset(request, options=options, today=today, filters=filters)

    action_count = queryset.filter(
        state_ordinal__in=[states.ORDINALS[one] for one in states.ACTION_STATES]
    ).count()

    rows, row_count, page, page_size = paginate(
        queryset.order_by(*DEFAULT_STOCK_ORDER),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=STOCK_SORTS,
    )
    elsewhere = _elsewhere(request.tenant_id, rows)
    return {
        "rows": [_stock_row(row, elsewhere) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
        "action_required": action_count,
    }


def _elsewhere(tenant_id, rows):
    """`hay 96 en Suba`, resolved for the page in one query.

    Only the rows that are actually in quiebre ask for it, and the answer is the
    **single** location holding the most units of that item, ties broken by name
    so the string does not change between page loads.
    """
    wanted = {
        row.item_id
        for row in rows
        if row.state_ordinal == states.ORDINALS[states.STOCKOUT]
    }
    if not wanted:
        return {}
    totals = (
        StockOnHand.objects.filter(tenant_id=tenant_id, item_id__in=wanted)
        .values("item_id", "location_id", "location__name")
        .annotate(total=Sum("quantity"))
        .filter(total__gt=0)
        .order_by("item_id", "-total", "location__name")
    )
    best: dict = {}
    for row in totals:
        best.setdefault(row["item_id"], []).append(row)
    return best


def _stock_row(row, elsewhere):
    clause = None
    if row.state_ordinal == states.ORDINALS[states.STOCKOUT]:
        for candidate in elsewhere.get(row.item_id, []):
            # Never the row's own sede: a lot in quiebre while another lot on
            # the same shelf is full is not a reason to send anybody anywhere.
            if candidate["location_id"] != row.location_id:
                clause = {
                    "location_id": candidate["location_id"],
                    "location_name": candidate["location__name"],
                    "quantity": candidate["total"],
                }
                break
    return {
        "id": row.id,
        "item_id": row.item_id,
        "item_name": row.item.name,
        "presentation": row.item.presentation,
        "unit": row.item.unit,
        "tracks_lots": row.item.tracks_lots,
        "invima_status": row.item.invima_status,
        "manufacturer_name": (
            row.item.manufacturer.name if row.item.manufacturer_id else None
        ),
        "location_id": row.location_id,
        "location_name": row.location.name,
        "lot_id": row.lot_id,
        "lot_code": row.lot.lot_code if row.lot else None,
        "expires_at": row.lot.expires_at if row.lot else None,
        "quantity": row.quantity,
        "bar_percentage": states.bar_percentage(row.quantity, row.policy_max_quantity),
        "state": states.name_of(row.state_ordinal),
        "state_ordinal": row.state_ordinal,
        "reorder_point": row.policy_reorder_point,
        "max_quantity": row.policy_max_quantity,
        "min_quantity": row.policy_min_quantity,
        "target_coverage_days": row.policy_coverage_days,
        "policy_source": row.policy_source,
        "elsewhere": clause,
    }


@router.get("/stock/summary", response=StockSummaryOut, auth=any_member)
def stock_summary(
    request,
    q: str | None = Query(None),
    location_id: list[uuid.UUID] = Query(None),
    category_id: uuid.UUID | None = Query(None),
    expiry: ExpiryFilter | None = Query(None),
):
    """Per-state counts, network-wide and per sede.

    It takes the same filters as the grid minus the state itself, so the chip's
    own counts describe the set the chip is about to narrow. **A check asserts
    against these figures rather than against a literal**: they are whatever the
    fixtures built, and a check that hard-codes them is red the first time a
    fixture changes.
    """
    options = _options(request)
    today = _today()
    queryset = _stock_queryset(
        request,
        options=options,
        today=today,
        filters={
            "q": q,
            "location_ids": location_id,
            "category_id": category_id,
            "expiry": expiry,
        },
    )
    grouped = (
        queryset.values("location_id", "location__name", "state_ordinal")
        .annotate(total=Count("id"))
        .order_by("location__name", "state_ordinal")
    )
    action_ordinals = {states.ORDINALS[one] for one in states.ACTION_STATES}
    network: dict[int, int] = {}
    per_location: dict = {}
    for row in grouped:
        ordinal = row["state_ordinal"]
        network[ordinal] = network.get(ordinal, 0) + row["total"]
        bucket = per_location.setdefault(
            row["location_id"],
            {
                "location_id": row["location_id"],
                "location_name": row["location__name"],
                "rows": 0,
                "action_required": 0,
                "counts": {},
            },
        )
        bucket["rows"] += row["total"]
        bucket["counts"][ordinal] = bucket["counts"].get(ordinal, 0) + row["total"]
        if ordinal in action_ordinals:
            bucket["action_required"] += row["total"]

    return {
        "rows": sum(network.values()),
        "action_required": sum(
            count for ordinal, count in network.items() if ordinal in action_ordinals
        ),
        "states": _state_counts(network),
        "locations": [
            {
                "location_id": bucket["location_id"],
                "location_name": bucket["location_name"],
                "rows": bucket["rows"],
                "action_required": bucket["action_required"],
                "states": _state_counts(bucket["counts"]),
            }
            for bucket in per_location.values()
        ],
    }


def _state_counts(counts):
    """Every one of the seven, including the ones at zero.

    A chip that hid a state with no rows would be a chip whose options moved as
    the data did, and a reviewer checking that no badge in §B.7.4 is one nobody
    has ever seen needs the zero to be visible rather than absent.
    """
    return [
        {
            "state": name,
            "ordinal": ordinal,
            "count": counts.get(ordinal, 0),
        }
        for name, ordinal in states.ORDINALS.items()
    ]


@router.get("/stock/availability", response=ItemAvailabilityOut, auth=any_member)
def stock_availability(request, item_id: uuid.UUID):
    """One item, the quantity at every location, lot by lot.

    §2 grants a `cashier` a network-wide stock lookup, and this is it: the
    source of `hay 96 en Suba` and of S4's counter lookup.
    """
    item = get_object_or_404(Item, id=item_id, tenant_id=request.tenant_id)
    allowed = _readable(request, network_read=True)
    rows = (
        StockOnHand.objects.filter(
            tenant_id=request.tenant_id, item_id=item_id, location_id__in=allowed
        )
        .select_related("location", "lot")
        .order_by("location__name", "lot__expires_at", "lot__lot_code")
    )
    transit = _incoming_transit(request.tenant_id, item_id, allowed)
    names = dict(Location.objects.filter(id__in=allowed).values_list("id", "name"))
    by_location: dict = {}
    lots = []
    # **A sede with a box on the road and nothing on the shelf is the reading
    # that matters**, and it has no `stock_on_hand` row to hang off -- so the
    # buckets are opened from the transit set first and the shelf fills them in.
    for location_id, units in transit.items():
        by_location[location_id] = {
            "location_id": location_id,
            "location_name": names.get(location_id, ""),
            "quantity": 0,
            "in_transit": units,
        }
    for row in rows:
        bucket = by_location.setdefault(
            row.location_id,
            {
                "location_id": row.location_id,
                "location_name": row.location.name,
                "quantity": 0,
                "in_transit": 0,
            },
        )
        bucket["quantity"] += row.quantity
        lots.append(
            {
                "lot_id": row.lot_id,
                "lot_code": row.lot.lot_code if row.lot else None,
                "expires_at": row.lot.expires_at if row.lot else None,
                "location_id": row.location_id,
                "location_name": row.location.name,
                "quantity": row.quantity,
            }
        )
    return {
        "item_id": item.id,
        "item_name": item.name,
        "unit": item.unit,
        "total": sum(bucket["quantity"] for bucket in by_location.values()),
        "in_transit": sum(transit.values()),
        "by_location": sorted(
            by_location.values(), key=lambda one: one["location_name"]
        ),
        "lots": lots,
    }


def _incoming_transit(tenant_id, item_id, allowed) -> dict:
    """Units of one item dispatched to each sede and not yet received.

    Open transfers only -- `dispatched` and `partial`, which are exactly the two
    states with a `transfer_out` written and no matching `transfer_in`. A
    `received` transfer has both legs and nothing on the road; a `draft` has
    neither, because nothing in v1 reserves stock.

    **This is not read from the projection**, and cannot be: the units are on no
    shelf, which is the whole point of the figure. It is the transfer document's
    own arithmetic, `dispatched - received`, over its open lines.
    """
    rows = (
        TransferLine.objects.filter(
            tenant_id=tenant_id,
            item_id=item_id,
            transfer__status__in=[TransferStatus.DISPATCHED, TransferStatus.PARTIAL],
            transfer__destination_location_id__in=allowed,
            # A resolved line is settled -- `No llegó` wrote the shrinkage at
            # the origin, `Llegó después` wrote the receipt -- so its units are
            # off the road either way. The same rule `_in_transit` applies per
            # line, applied to the sum.
            resolution="",
        )
        .values("transfer__destination_location_id")
        .annotate(units=Sum(F("quantity_dispatched") - F("quantity_received")))
    )
    return {
        row["transfer__destination_location_id"]: row["units"]
        for row in rows
        if (row["units"] or 0) > 0
    }


@router.get("/stock/expiring", response=ExpiringOut, auth=owner_or_admin)
def stock_expiring(request, location_id: list[uuid.UUID] = Query(None)):
    """Value and lot count by horizon, per sede and network-wide, **plus the
    total inventory value at the same cost basis**.

    The total is the denominator: S9's `4,6% del inventario valorizado` tile is
    a ratio with no meaning without it, and returning it here is what stops S9
    computing a second valuation on a different basis. A percentage whose
    numerator and denominator came from two cost bases is a number nobody can
    defend to an accountant.
    """
    options = _options(request)
    today = _today()
    allowed = _readable(request, requested=location_id)
    # **`!= 0`, not `> 0`.** Acceptance 23 reconciles the total to
    # `Σ quantity × lots.unit_cost` over *every* `stock_on_hand` row in scope,
    # and §5 rule 2 makes a negative row a designed-for state rather than an
    # anomaly: an oversold lot is merchandise the network owes, its value is
    # real and negative, and dropping it makes the denominator disagree with the
    # sum anybody would compute by hand.
    #
    # A row at zero is excluded because it contributes nothing to either figure
    # and would inflate the lot count -- `142 lotes` beside `$18,9 M` is a count
    # of lots at risk, and a lot with nothing on the shelf is not one. Excluding
    # it changes no value, so all three figures still come off one query.
    base = StockOnHand.objects.filter(
        tenant_id=request.tenant_id, location_id__in=allowed
    ).exclude(quantity=0)
    horizons = [
        ("valuation", int(options["expiry_valuation_days"])),
        ("alert", int(options["expiry_alert_days"])),
        ("notice", int(options["expiry_notice_days"])),
    ]

    names = dict(Location.objects.filter(id__in=allowed).values_list("id", "name"))
    totals = _valuation(base)
    per_location = {
        location: {
            "location_id": location,
            "location_name": names.get(location, ""),
            "horizons": [],
            "total_value": Decimal("0"),
        }
        for location in allowed
    }
    for location, value in _valuation_by_location(base).items():
        if location in per_location:
            per_location[location]["total_value"] = value

    network = []
    for key, days in horizons:
        # **`>= today`, not just `<= horizon`.** A lot past its date is
        # `Vencido`, which is a different and worse state than `por vencer`;
        # counting it inside `Inventario por vencer · 90 días` would put
        # merchandise that is already a legal problem into a tile about a supply
        # one, and the derivation's own precedence keeps the two apart.
        window = base.filter(
            lot__expires_at__gte=today,
            lot__expires_at__lte=today + timedelta(days=days),
        )
        value, lot_count = _valuation(window), _lot_count(window)
        network.append({"key": key, "days": days, "value": value, "lots": lot_count})
        by_location = _valuation_by_location(window)
        lots_by_location = _lot_count_by_location(window)
        for location, bucket in per_location.items():
            bucket["horizons"].append(
                {
                    "key": key,
                    "days": days,
                    "value": by_location.get(location, Decimal("0")),
                    "lots": lots_by_location.get(location, 0),
                }
            )

    return {
        "horizons": network,
        "total_value": totals,
        "locations": sorted(
            per_location.values(), key=lambda one: one["location_name"]
        ),
    }


def _valuation(queryset) -> Decimal:
    """`Σ quantity × lots.unit_cost`, and never a sale price.

    A lot with no cost recorded contributes nothing rather than a zero that
    would read as free merchandise -- the join drops it, and the lot count says
    how many rows the value is over.
    """
    total = queryset.aggregate(total=Sum(_value_expression()))["total"]
    return Decimal(total or 0).quantize(Decimal("0.01"))


def _value_expression():
    """`quantity × unit_cost`, told what it is.

    The product of an integer column and a numeric one has no output field
    Django can infer, and guessing wrong here would round a network's inventory
    value to whole pesos.
    """
    return (
        F("quantity")
        * F("lot__unit_cost")
        * Value(
            Decimal("1"), output_field=DecimalField(max_digits=18, decimal_places=2)
        )
    )


def _valuation_by_location(queryset):
    return {
        row["location_id"]: Decimal(row["total"] or 0).quantize(Decimal("0.01"))
        for row in queryset.values("location_id").annotate(
            total=Sum(_value_expression())
        )
    }


def _lot_count(queryset) -> int:
    return queryset.exclude(lot_id=None).values("lot_id").distinct().count()


def _lot_count_by_location(queryset):
    return {
        row["location_id"]: row["total"]
        for row in queryset.exclude(lot_id=None)
        .values("location_id")
        .annotate(total=Count("lot_id", distinct=True))
    }


# ---------------------------------------------------------------------------
# The ledger, read and written
# ---------------------------------------------------------------------------

MOVE_SORTS = {
    "recorded_at": ["recorded_at"],
    "occurred_at": ["occurred_at"],
    "quantity": ["quantity", "recorded_at"],
    "item": ["item__name", "recorded_at"],
    "type": ["type", "recorded_at"],
}


@router.get("/stock-moves", response=Page[MoveOut], auth=any_member)
def list_moves(
    request,
    location_id: list[uuid.UUID] = Query(None),
    item_id: uuid.UUID | None = Query(None),
    lot_id: uuid.UUID | None = Query(None),
    type: MoveTypeValue | None = Query(None),
    document_id: uuid.UUID | None = Query(None),
    since: date | None = Query(None),
    until: date | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
):
    """The ledger, filterable. **Read-only, and there is no `PATCH` or `DELETE`
    on this resource at any path** -- nor a grant that would let one exist.

    A `cashier` reads their own home sede and no other, which is the scoped mode
    of S0's helper rather than a filter written here.
    """
    queryset = StockMove.objects.filter(
        tenant_id=request.tenant_id,
        location_id__in=_readable(request, requested=location_id),
    ).select_related("location", "item", "lot", "device")
    if item_id:
        queryset = queryset.filter(item_id=item_id)
    if lot_id:
        queryset = queryset.filter(lot_id=lot_id)
    if type:
        queryset = queryset.filter(type=type)
    if document_id:
        queryset = queryset.filter(document_id=document_id)
    if since:
        queryset = queryset.filter(recorded_at__date__gte=since)
    if until:
        queryset = queryset.filter(recorded_at__date__lte=until)

    rows, row_count, page, page_size = paginate(
        queryset.order_by("-recorded_at", "-id"),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=MOVE_SORTS,
    )
    return {
        "rows": [_move_out(row) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _move_out(row):
    return {
        "id": row.id,
        "location_id": row.location_id,
        "location_name": row.location.name,
        "item_id": row.item_id,
        "item_name": row.item.name,
        "lot_id": row.lot_id,
        "lot_code": row.lot.lot_code if row.lot else None,
        "quantity": row.quantity,
        "type": row.type,
        "reason": row.reason,
        "note": row.note,
        "document_type": row.document_type,
        "document_id": row.document_id,
        "unit_cost": row.unit_cost,
        "occurred_at": row.occurred_at,
        "recorded_at": row.recorded_at,
        "device_id": row.device_id,
        "device_label": row.device.label if row.device_id else None,
        "user_id": row.user_id,
        "user_name": row.user_name,
        "fefo_override": row.fefo_override,
    }


@router.post("/stock-moves", response=list[MoveOut], auth=any_member)
def create_move(request, payload: MoveIn):
    """The only direct write, and it accepts three types and no others.

    A body naming any other type is rejected, because every other type is a
    consequence of a document that has its own endpoint -- and a general "write
    me a move" endpoint would be the hole through which a future stage bypasses
    the ledger service.
    """
    if payload.type not in [one.value for one in DIRECT_MOVE_TYPES]:
        raise HttpError(
            422,
            f"«{payload.type}» no se escribe a mano: es la consecuencia de un "
            "documento y se registra desde su propia pantalla.",
        )
    if not _elevated(request.user) and payload.type not in [
        one.value for one in CASHIER_MOVE_TYPES
    ]:
        raise HttpError(
            403,
            "Un ajuste lo registra la administradora. Desde el mostrador se "
            "pueden registrar una merma y un vencimiento.",
        )
    _writable_location(request, payload.location_id)

    # **`shrinkage` and `expiry` always subtract, so their quantity is a count
    # and the sign is the type's.** `adjustment` is the one direct type that
    # takes either sign, and the caller states which -- the dialog's confirm
    # button already reads `Restar 12 unidades del lote A-2291 en Chapinero`, so
    # the direction is a thing the person saw before they pressed it.
    if payload.type in [one.value for one in CASHIER_MOVE_TYPES]:
        if payload.quantity <= 0:
            raise HttpError(
                422,
                "Una merma o un vencimiento se registra como un número de "
                "unidades mayor que cero.",
            )
        quantity = -payload.quantity
    else:
        if payload.quantity == 0:
            raise HttpError(422, "Un ajuste de cero unidades no es un ajuste.")
        quantity = payload.quantity

    document_id = uuid.uuid4()
    try:
        result = ledger.append(
            [
                ledger.Move(
                    location_id=payload.location_id,
                    item_id=payload.item_id,
                    lot_id=payload.lot_id,
                    quantity=quantity,
                    type=payload.type,
                    reason=payload.reason,
                    note=payload.note,
                    occurred_at=payload.occurred_at,
                    client_uuid=payload.client_uuid,
                    key=f"stock-move:{document_id}",
                )
            ],
            tenant_id=request.tenant_id,
            actor=request.user,
            request_id=request_id.get(),
        )
    except ledger.Refused as refusal:
        raise HttpError(422, str(refusal)) from refusal

    if result.skipped:
        raise HttpError(
            422,
            "Este producto no maneja existencias, así que no tiene movimientos "
            "que registrar.",
        )
    written = result.written or result.duplicates
    for row in written:
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.CREATE,
            entity_type="stock_moves",
            entity_id=row.id,
            after={
                "type": row.type,
                "quantity": row.quantity,
                "reason": row.reason,
                "location_id": str(row.location_id),
                "item_id": str(row.item_id),
                "lot_id": str(row.lot_id) if row.lot_id else None,
            },
            request_id=request_id.get(),
        )
    return [_move_out(_reload(row)) for row in written]


def _reload(move):
    return StockMove.objects.select_related("location", "item", "lot", "device").get(
        id=move.id
    )


# ---------------------------------------------------------------------------
# Lots and the recall answer
# ---------------------------------------------------------------------------


@router.get("/lots", response=Page[LotOut], auth=any_member)
def list_lots(
    request,
    item_id: uuid.UUID | None = Query(None),
    code: str | None = Query(None),
    expiring_within_days: int | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
):
    """Lots by item, by code, by expiry window, with the locations holding each.

    The code lookup is the **reverse half of the recall answer**: a lot code in,
    every sede holding it out.
    """
    queryset = Lot.objects.filter(tenant_id=request.tenant_id).select_related(
        "item", "supplier"
    )
    if item_id:
        queryset = queryset.filter(item_id=item_id)
    if code:
        queryset = queryset.filter(lot_code__iexact=code.strip())
    if expiring_within_days is not None:
        queryset = queryset.filter(
            expires_at__isnull=False,
            expires_at__lte=_today() + timedelta(days=int(expiring_within_days)),
        )
    rows, row_count, page, page_size = paginate(
        queryset.order_by("expires_at", "lot_code"),
        page=page,
        page_size=page_size,
        sort=None,
        order="asc",
        sortable={},
    )
    holdings = _holdings(request, [row.id for row in rows])
    return {
        "rows": [_lot_out(row, holdings) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _holdings(request, lot_ids):
    if not lot_ids:
        return {}
    rows = (
        StockOnHand.objects.filter(
            tenant_id=request.tenant_id,
            lot_id__in=lot_ids,
            location_id__in=_readable(request, network_read=True),
        )
        .select_related("location")
        .order_by("location__name")
    )
    held: dict = {}
    for row in rows:
        held.setdefault(row.lot_id, []).append(
            {
                "location_id": row.location_id,
                "location_name": row.location.name,
                "quantity": row.quantity,
            }
        )
    return held


def _lot_out(lot, holdings):
    by_location = holdings.get(lot.id, [])
    return {
        "id": lot.id,
        "item_id": lot.item_id,
        "item_name": lot.item.name,
        "lot_code": lot.lot_code,
        "expires_at": lot.expires_at,
        "supplier_id": lot.supplier_id,
        "supplier_name": lot.supplier.name if lot.supplier_id else None,
        "unit_cost": lot.unit_cost,
        "invima_registration": lot.invima_registration,
        "total": sum(one["quantity"] for one in by_location),
        "by_location": by_location,
    }


@router.get("/lots/{lot_id}", response=LotOut, auth=any_member)
def read_lot(request, lot_id: uuid.UUID):
    lot = get_object_or_404(
        Lot.objects.select_related("item", "supplier"),
        id=lot_id,
        tenant_id=request.tenant_id,
    )
    return _lot_out(lot, _holdings(request, [lot.id]))


def _trace(request, lot):
    """Every move on the lot in `recorded_at` order, with a running balance.

    The balance runs over the **whole network**, because a withdrawal is about
    the lot and not about a sede, and its final value equals `SUM(quantity)`
    from `stock_on_hand` over that lot across every location. A final balance
    that disagrees with the projection is a trace nobody can hand to an
    inspector.
    """
    rows = (
        StockMove.objects.filter(tenant_id=request.tenant_id, lot_id=lot.id)
        .select_related("location", "item", "lot", "device")
        .order_by("recorded_at", "id")
    )
    balance = 0
    answer = []
    for row in rows:
        balance += row.quantity
        answer.append({**_move_out(row), "balance": balance})
    return answer


@router.get("/lots/{lot_id}/trace", response=TraceOut, auth=owner_or_admin)
def lot_trace(request, lot_id: uuid.UUID):
    """**The recall answer.** Given a lot: who has it, who bought it, on which
    device, in which shift and at what moment."""
    lot = get_object_or_404(
        Lot.objects.select_related("item", "supplier"),
        id=lot_id,
        tenant_id=request.tenant_id,
    )
    return {
        "lot": _lot_out(lot, _holdings(request, [lot.id])),
        "moves": _trace(request, lot),
    }


@router.get("/lots/{lot_id}/trace.csv", auth=owner_or_admin, response=None)
def lot_trace_csv(request, lot_id: uuid.UUID):
    """The same rows in the same order, as a file somebody hands to an inspector.

    CSV rather than a PDF of a table: the receiving end is a spreadsheet or a
    regulator's own system, and a rendered table is the one format neither can
    read back.
    """
    lot = get_object_or_404(
        Lot.objects.select_related("item", "supplier"),
        id=lot_id,
        tenant_id=request.tenant_id,
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="trazabilidad-{lot.lot_code}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            "registrado",
            "ocurrido",
            "sede",
            "producto",
            "lote",
            "vence",
            "tipo",
            "motivo",
            "cantidad",
            "saldo",
            "documento",
            "documento_id",
            "equipo",
            "usuario",
        ]
    )
    for row in _trace(request, lot):
        writer.writerow(
            [
                row["recorded_at"].isoformat(),
                row["occurred_at"].isoformat(),
                row["location_name"],
                row["item_name"],
                row["lot_code"] or "",
                lot.expires_at.isoformat() if lot.expires_at else "",
                row["type"],
                row["reason"],
                row["quantity"],
                row["balance"],
                row["document_type"],
                str(row["document_id"] or ""),
                row["device_label"] or "",
                row["user_name"],
            ]
        )
    return response


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


class LineRefused(Exception):
    """One line of a multi-line entry, refused, **with the line and the box.**

    §B.10.3 · a refusal a person can act on names where it happened. `Cargar
    mercancía` takes twenty lines at a time, and a region-scope block at the
    foot of the page saying a lot code disagrees leaves them reading twenty
    lines to find which one -- so the line index and the control travel with
    the message and the surface marks that box invalid.
    """

    def __init__(self, detail, *, line, field=""):
        super().__init__(detail)
        self.detail = detail
        self.line = line
        self.field = field


def resolve_lot(*, tenant_id, item, lot_code, expires_at, unit_cost, supplier_id):
    """Create or match one lot, refusing a stored expiry that disagrees.

    An expiry mismatch on the same lot code is a typo the person holding the box
    fixes in three seconds, and silently keeping either value puts a wrong date
    at the head of the FEFO queue -- so the line is refused rather than accepted
    with one of them.
    """
    if not item.tracks_lots:
        return None
    code = (lot_code or "").strip()
    if not code:
        raise ledger.Refused(
            f"«{item.name}» se maneja por lote, así que la línea necesita el "
            "código del lote.",
            field="lot_code",
        )
    if item.tracks_expiry and expires_at is None:
        raise ledger.Refused(
            f"«{item.name}» lleva fecha de vencimiento y esta línea no la trae.",
            field="expires_at",
        )
    lot = Lot.objects.filter(
        tenant_id=tenant_id, item_id=item.id, lot_code=code
    ).first()
    if lot is None:
        return Lot.objects.create(
            tenant_id=tenant_id,
            item=item,
            lot_code=code,
            expires_at=expires_at,
            unit_cost=unit_cost,
            supplier_id=supplier_id,
            invima_registration="",
        )
    if expires_at is not None and lot.expires_at != expires_at:
        held = lot.expires_at.strftime("%m/%Y") if lot.expires_at else "sin fecha"
        raise ledger.Refused(
            f"El lote {lot.lot_code} ya existe con vencimiento {held}.",
            field="expires_at",
        )
    fields = []
    # S3 writes `unit_cost` for opening stock and standalone receipts; S6 writes
    # what a purchase-order receipt actually paid (ledger). Neither overwrites a
    # cost with nothing.
    if unit_cost is not None and lot.unit_cost != unit_cost:
        lot.unit_cost = unit_cost
        fields.append("unit_cost")
    if supplier_id and lot.supplier_id != supplier_id:
        lot.supplier_id = supplier_id
        fields.append("supplier_id")
    if fields:
        lot.save(update_fields=[*fields, "updated_at"])
    return lot


@router.post("/receipts", response=ReceiptOut, auth=owner_or_admin)
def create_receipt(request, payload: ReceiptIn):
    """A standalone receipt or opening stock: **`adjustment` rows, not
    `receipt` rows.**

    The ledger fixes `receipt` as caused by S6, when a goods receipt against a
    purchase order is confirmed, and the ledger wins on ownership. The
    consequence to hold onto: cost of goods must never be computed by filtering
    `type = 'receipt'` -- it reads `stock_moves.unit_cost` and the move's
    `lot_id` across every positive type.
    """
    _writable_location(request, payload.location_id)
    if not payload.lines:
        raise HttpError(422, "Una entrada sin líneas no es una entrada.")

    document_id = payload.document_id or uuid.uuid4()
    items = {
        item.id: item
        for item in Item.objects.filter(
            tenant_id=request.tenant_id,
            id__in=[line.item_id for line in payload.lines],
        )
    }
    moves = []
    with transaction.atomic():
        for index, line in enumerate(payload.lines):
            item = items.get(line.item_id)
            if item is None:
                raise LineRefused(
                    "Esta línea nombra un producto que no existe.",
                    line=index,
                    field="item_id",
                )
            if line.quantity <= 0:
                raise LineRefused(
                    f"«{item.name}» entra con una cantidad mayor que cero, en "
                    "unidades base.",
                    line=index,
                    field="quantity",
                )
            try:
                lot = resolve_lot(
                    tenant_id=request.tenant_id,
                    item=item,
                    lot_code=line.lot_code,
                    expires_at=line.expires_at,
                    unit_cost=line.unit_cost,
                    supplier_id=line.supplier_id,
                )
            except ledger.Refused as refusal:
                raise LineRefused(
                    str(refusal), line=index, field=refusal.field
                ) from refusal
            moves.append(
                ledger.Move(
                    location_id=payload.location_id,
                    item_id=item.id,
                    lot_id=lot.id if lot else None,
                    quantity=line.quantity,
                    type=StockMoveType.ADJUSTMENT,
                    reason=payload.reason,
                    note=payload.note,
                    unit_cost=line.unit_cost,
                    document_type="receipts",
                    document_id=document_id,
                    occurred_at=payload.occurred_at,
                    client_uuid=line.client_uuid,
                    key=f"receipt:{document_id}:{index}",
                )
            )
        try:
            result = ledger.append(
                moves,
                tenant_id=request.tenant_id,
                actor=request.user,
                request_id=request_id.get(),
            )
        except ledger.Refused as refusal:
            raise HttpError(422, str(refusal)) from refusal

        # **One audit row for the whole entry, not one per line.** A forty-line
        # receipt is one thing a person did.
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.CREATE,
            entity_type="receipts",
            entity_id=document_id,
            after={
                "location_id": str(payload.location_id),
                "reason": payload.reason,
                "lines": len(payload.lines),
                "units": sum(line.quantity for line in payload.lines),
            },
            request_id=request_id.get(),
        )
    return {
        "location_id": payload.location_id,
        "document_id": document_id,
        "lines_written": len(result.written),
        "lines_duplicate": len(result.duplicates),
        "lines_skipped": len(result.skipped),
        "moves": [_move_out(_reload(row)) for row in result.written],
    }


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------


def _transfer_out(transfer):
    lines = list(transfer.lines.select_related("item", "lot").all())
    return {
        "id": transfer.id,
        "number": transfer.number,
        "origin_location_id": transfer.origin_location_id,
        "origin_location_name": transfer.origin_location.name,
        "destination_location_id": transfer.destination_location_id,
        "destination_location_name": transfer.destination_location.name,
        "status": transfer.status,
        "dispatched_at": transfer.dispatched_at,
        "dispatched_by_name": transfer.dispatched_by_name,
        "received_at": transfer.received_at,
        "received_by_name": transfer.received_by_name,
        "note": transfer.note,
        "references": len(lines),
        "in_transit": sum(_in_transit(line) for line in lines),
        "lines": [
            {
                "id": line.id,
                "item_id": line.item_id,
                "item_name": line.item.name,
                "lot_id": line.lot_id,
                "lot_code": line.lot.lot_code if line.lot else None,
                "quantity_requested": line.quantity_requested,
                "quantity_dispatched": line.quantity_dispatched,
                "quantity_received": line.quantity_received,
                "in_transit": _in_transit(line),
                "resolution": line.resolution,
            }
            for line in lines
        ],
    }


def _in_transit(line):
    """Units on no shelf: dispatched, not yet received, not yet resolved.

    **A figure on the transfer, never a state in the `Estado` column.** The box
    that left Chapinero on Tuesday and has not reached Suba by Thursday is
    visible as exactly that rather than as a discrepancy nobody can name.
    """
    if line.resolution:
        return 0
    return max(0, line.quantity_dispatched - line.quantity_received)


def _transfers_for(request):
    """Every transfer touching a location this identity reaches.

    A `cashier` sees transfers at their home sede in **either** direction --
    they are the person opening the box, and an inbound transfer they could not
    see is a box nobody can book in.
    """
    allowed = _readable(request)
    return (
        Transfer.objects.filter(tenant_id=request.tenant_id)
        .filter(
            Q(origin_location_id__in=allowed) | Q(destination_location_id__in=allowed)
        )
        .select_related("origin_location", "destination_location")
    )


@router.get("/transfers", response=Page[TransferOut], auth=any_member)
def list_transfers(
    request,
    status: TransferStatusValue | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
):
    """The transfer list, **with `partial` first -- that is the work list.**

    A shortfall stays on it until a person closes it by choosing one of two
    answers, each of which writes a move.
    """
    queryset = _transfers_for(request)
    if status:
        queryset = queryset.filter(status=status)
    ordered = queryset.annotate(unresolved=Q(status=TransferStatus.PARTIAL)).order_by(
        "-unresolved", "-number"
    )
    rows, row_count, page, page_size = paginate(
        ordered, page=page, page_size=page_size, sort=None, order="asc", sortable={}
    )
    return {
        "rows": [_transfer_out(row) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _next_number(tenant_id):
    """One sequential number per tenant, allocated under a lock.

    A `max(number) + 1` with no lock is two transfers with the same number the
    first time two administrators press the button in the same second, and the
    unique index would then refuse one of them with a message nobody can act on.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [int.from_bytes(uuid.UUID(str(tenant_id)).bytes[:8], "big", signed=True)],
        )
    highest = Transfer.objects.filter(tenant_id=tenant_id).aggregate(
        highest=Max("number")
    )["highest"]
    return (highest or 0) + 1


@router.post("/transfers", response=TransferOut, auth=owner_or_admin)
def create_transfer(request, payload: TransferIn):
    """Create a `draft` with its lines. **A draft moves no units** -- its lines
    are a request, not a hold: nothing in v1 reserves stock."""
    _writable_location(request, payload.origin_location_id)
    _readable(request, requested=[payload.destination_location_id])
    if payload.origin_location_id == payload.destination_location_id:
        raise HttpError(422, "Un traslado va de una sede a otra distinta.")
    if not payload.lines:
        raise HttpError(422, "Un traslado sin líneas no es un traslado.")

    with transaction.atomic():
        transfer = Transfer.objects.create(
            tenant_id=request.tenant_id,
            number=_next_number(request.tenant_id),
            origin_location_id=payload.origin_location_id,
            destination_location_id=payload.destination_location_id,
            note=payload.note,
        )
        _write_lines(request, transfer, payload.lines)
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.CREATE,
            entity_type="transfers",
            entity_id=transfer.id,
            after={"number": transfer.number, "lines": len(payload.lines)},
            request_id=request_id.get(),
        )
    return _transfer_out(_transfer(request, transfer.id))


def _write_lines(request, transfer, lines):
    TransferLine.objects.filter(tenant_id=request.tenant_id, transfer=transfer).delete()
    items = {
        item.id: item
        for item in Item.objects.filter(
            tenant_id=request.tenant_id, id__in=[line.item_id for line in lines]
        )
    }
    rows = []
    for line in lines:
        item = items.get(line.item_id)
        if item is None:
            raise HttpError(422, "Una línea nombra un producto que no existe.")
        if line.quantity_requested <= 0:
            raise HttpError(
                422, f"«{item.name}» se traslada en una cantidad mayor que cero."
            )
        if item.tracks_lots and line.lot_id is None:
            raise HttpError(
                422, f"«{item.name}» se maneja por lote, así que la línea necesita uno."
            )
        rows.append(
            TransferLine(
                tenant_id=request.tenant_id,
                transfer=transfer,
                item_id=line.item_id,
                lot_id=line.lot_id,
                quantity_requested=line.quantity_requested,
            )
        )
    TransferLine.objects.bulk_create(rows)


def _transfer(request, transfer_id):
    return get_object_or_404(
        _transfers_for(request), id=transfer_id, tenant_id=request.tenant_id
    )


@router.patch("/transfers/{transfer_id}", response=TransferOut, auth=owner_or_admin)
def patch_transfer(request, transfer_id: uuid.UUID, payload: TransferPatchIn):
    """Edit lines. **`draft` only** -- a dispatched transfer is a fact, not a
    form, and editing one would silently disagree with the moves it already
    wrote."""
    transfer = _transfer(request, transfer_id)
    if transfer.status != TransferStatus.DRAFT:
        raise HttpError(
            409,
            "Este traslado ya salió de la sede de origen, así que sus líneas son "
            "un hecho y no un formulario.",
        )
    _writable_location(request, transfer.origin_location_id)
    before = {"note": transfer.note, "lines": transfer.lines.count()}
    with transaction.atomic():
        if payload.note is not None:
            transfer.note = payload.note
            transfer.save(update_fields=["note", "updated_at"])
        if payload.lines is not None:
            _write_lines(request, transfer, payload.lines)
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.UPDATE,
            entity_type="transfers",
            entity_id=transfer.id,
            before=before,
            after={"note": transfer.note, "lines": transfer.lines.count()},
            request_id=request_id.get(),
        )
    return _transfer_out(_transfer(request, transfer_id))


@router.delete("/transfers/{transfer_id}", auth=owner_or_admin)
def delete_transfer(request, transfer_id: uuid.UUID):
    """`draft` only. Nothing that has moved stock is ever deleted."""
    transfer = _transfer(request, transfer_id)
    if transfer.status != TransferStatus.DRAFT:
        raise HttpError(
            409, "Un traslado que ya movió existencias no se elimina: se resuelve."
        )
    _writable_location(request, transfer.origin_location_id)
    before = {"number": transfer.number, "status": transfer.status}
    transfer.delete()
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.DELETE,
        entity_type="transfers",
        entity_id=transfer_id,
        before=before,
        request_id=request_id.get(),
    )
    return {"deleted": True}


@router.post(
    "/transfers/{transfer_id}/dispatch", response=TransferOut, auth=owner_or_admin
)
def dispatch_transfer(request, transfer_id: uuid.UUID, payload: DispatchIn):
    """`draft → dispatched`. Writes one `transfer_out` per line at the origin.

    **This is where `negative_stock_block_outbound` is read**, and the only kind
    of place it ever is: refusal is a policy at the endpoint and never in the
    ledger service, because the sale path must never ask this question (§5
    rule 2).
    """
    transfer = _transfer(request, transfer_id)
    if transfer.status != TransferStatus.DRAFT:
        raise HttpError(409, "Este traslado ya fue despachado.")
    _writable_location(request, transfer.origin_location_id)
    options = _options(request)

    lines = list(transfer.lines.select_related("item").all())
    asked = {line.line_id: line.quantity for line in (payload.lines or [])}
    moves = []
    with transaction.atomic():
        for line in lines:
            quantity = asked.get(line.id, line.quantity_requested)
            if quantity <= 0:
                continue
            if options["negative_stock_block_outbound"]:
                _refuse_overdraft(request, transfer.origin_location_id, line, quantity)
            line.quantity_dispatched = quantity
            line.save(update_fields=["quantity_dispatched", "updated_at"])
            moves.append(
                ledger.Move(
                    location_id=transfer.origin_location_id,
                    item_id=line.item_id,
                    lot_id=line.lot_id,
                    quantity=-quantity,
                    type=StockMoveType.TRANSFER_OUT,
                    document_type="transfers",
                    document_id=transfer.id,
                    key=f"transfer-out:{transfer.id}:{line.id}",
                )
            )
        if not moves:
            raise HttpError(422, "Este traslado no despacha ninguna unidad.")
        try:
            ledger.append(
                moves,
                tenant_id=request.tenant_id,
                actor=request.user,
                request_id=request_id.get(),
            )
        except ledger.Refused as refusal:
            raise HttpError(422, str(refusal)) from refusal

        transfer.status = TransferStatus.DISPATCHED
        transfer.dispatched_at = timezone.now()
        transfer.dispatched_by = request.user
        transfer.dispatched_by_name = request.user.name
        transfer.save(
            update_fields=[
                "status",
                "dispatched_at",
                "dispatched_by",
                "dispatched_by_name",
                "updated_at",
            ]
        )
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.UPDATE,
            entity_type="transfers",
            entity_id=transfer.id,
            before={"status": TransferStatus.DRAFT},
            after={
                "status": transfer.status,
                "units": sum(-move.quantity for move in moves),
            },
            request_id=request_id.get(),
        )
    return _transfer_out(_transfer(request, transfer_id))


def _refuse_overdraft(request, location_id, line, quantity):
    held = (
        StockOnHand.objects.filter(
            tenant_id=request.tenant_id,
            location_id=location_id,
            item_id=line.item_id,
            lot_id=line.lot_id,
        )
        .values_list("quantity", flat=True)
        .first()
        or 0
    )
    if quantity > held:
        raise HttpError(
            422,
            f"«{line.item.name}» tiene {held} unidades en la sede de origen y "
            f"el traslado despacha {quantity}. Ajuste la cantidad o registre "
            "primero la entrada que falta.",
        )


@router.post("/transfers/{transfer_id}/receive", response=TransferOut, auth=any_member)
def receive_transfer(request, transfer_id: uuid.UUID, payload: DispatchIn):
    """`dispatched → received` or `partial`. Writes one `transfer_in` per
    received line at the destination.

    A `cashier` at the destination may press this: they are the person opening
    the box. They may not create, dispatch or resolve.

    **It is refused on a `partial`, and that is not a restriction for its own
    sake.** A shortfall is closed through `/resolve`, which writes the move that
    accounts for the missing units; receiving again would set
    `quantity_received` to the full dispatched figure while the ledger appended
    nothing -- the second `transfer_in` carries the same document key as the
    first and is deduplicated -- and the line would then claim units the
    destination never got. Two doors to one outcome, one of which quietly lies
    about the shelf, is worse than one door.
    """
    transfer = _transfer(request, transfer_id)
    if transfer.status != TransferStatus.DISPATCHED:
        raise HttpError(
            409,
            "Este traslado no está en camino."
            if transfer.status != TransferStatus.PARTIAL
            else "Este traslado tiene un faltante: ciérrelo indicando si llegó "
            "después o si no llegó.",
        )
    _writable_location(request, transfer.destination_location_id)

    lines = list(transfer.lines.select_related("item").all())
    asked = {line.line_id: line.quantity for line in (payload.lines or [])}
    moves = []
    with transaction.atomic():
        for line in lines:
            quantity = asked.get(line.id, line.quantity_dispatched)
            quantity = max(0, min(quantity, line.quantity_dispatched))
            if quantity <= 0:
                continue
            line.quantity_received = quantity
            line.save(update_fields=["quantity_received", "updated_at"])
            moves.append(
                ledger.Move(
                    location_id=transfer.destination_location_id,
                    item_id=line.item_id,
                    lot_id=line.lot_id,
                    quantity=quantity,
                    type=StockMoveType.TRANSFER_IN,
                    document_type="transfers",
                    document_id=transfer.id,
                    key=f"transfer-in:{transfer.id}:{line.id}",
                )
            )
        if moves:
            try:
                ledger.append(
                    moves,
                    tenant_id=request.tenant_id,
                    actor=request.user,
                    request_id=request_id.get(),
                )
            except ledger.Refused as refusal:
                raise HttpError(422, str(refusal)) from refusal

        transfer.refresh_from_db()
        short = any(_in_transit(line) > 0 for line in transfer.lines.all())
        transfer.status = TransferStatus.PARTIAL if short else TransferStatus.RECEIVED
        transfer.received_at = timezone.now()
        transfer.received_by = request.user
        transfer.received_by_name = request.user.name
        transfer.save(
            update_fields=[
                "status",
                "received_at",
                "received_by",
                "received_by_name",
                "updated_at",
            ]
        )
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.UPDATE,
            entity_type="transfers",
            entity_id=transfer.id,
            before={"status": TransferStatus.DISPATCHED},
            after={
                "status": transfer.status,
                "units": sum(move.quantity for move in moves),
            },
            request_id=request_id.get(),
        )
    return _transfer_out(_transfer(request, transfer_id))


@router.post(
    "/transfers/{transfer_id}/resolve", response=TransferOut, auth=owner_or_admin
)
def resolve_transfer(request, transfer_id: uuid.UUID, payload: ResolveIn):
    """Close a shortfall by choosing one of two answers, each of which writes a
    move.

    **`received_late`** writes a further `transfer_in` at the destination: the
    remainder arrived, and the network total returns to where it started.

    **`lost_in_transit`** writes a **pair** at the origin -- a `transfer_in` of
    the shortfall, then a `shrinkage` of the same size with reason `loss` -- and
    the pair is the whole point. The units left the origin's shelf at dispatch,
    so the network total already fell by the shortfall; a bare `shrinkage` here
    would take it down a second time and the total would end at `N − 24` on a
    twelve-unit loss, with both moves looking individually correct. The
    `transfer_in` returns the un-received units to the origin's books, which is
    exactly the inverse of the `transfer_out` that put them in a van, and the
    `shrinkage` is then the row that carries the reason. **A shortfall that
    quietly nets out of the ledger is merchandise nobody ever has to explain**,
    and this is what stops it.
    """
    transfer = _transfer(request, transfer_id)
    if transfer.status != TransferStatus.PARTIAL:
        raise HttpError(409, "Este traslado no tiene un faltante por resolver.")
    _writable_location(request, transfer.origin_location_id)
    line = get_object_or_404(
        TransferLine.objects.select_related("item"),
        id=payload.line_id,
        transfer=transfer,
        tenant_id=request.tenant_id,
    )
    short = _in_transit(line)
    if short <= 0:
        raise HttpError(409, "Esta línea no tiene faltante.")

    moves = []
    if payload.resolution == TransferResolution.RECEIVED_LATE:
        moves.append(
            ledger.Move(
                location_id=transfer.destination_location_id,
                item_id=line.item_id,
                lot_id=line.lot_id,
                quantity=short,
                type=StockMoveType.TRANSFER_IN,
                document_type="transfers",
                document_id=transfer.id,
                key=f"transfer-late:{transfer.id}:{line.id}",
            )
        )
    else:
        moves.extend(
            [
                ledger.Move(
                    location_id=transfer.origin_location_id,
                    item_id=line.item_id,
                    lot_id=line.lot_id,
                    quantity=short,
                    type=StockMoveType.TRANSFER_IN,
                    document_type="transfers",
                    document_id=transfer.id,
                    key=f"transfer-return:{transfer.id}:{line.id}",
                ),
                ledger.Move(
                    location_id=transfer.origin_location_id,
                    item_id=line.item_id,
                    lot_id=line.lot_id,
                    quantity=-short,
                    type=StockMoveType.SHRINKAGE,
                    reason="loss",
                    note=f"Faltante del traslado {transfer.number}.",
                    document_type="transfers",
                    document_id=transfer.id,
                    key=f"transfer-loss:{transfer.id}:{line.id}",
                ),
            ]
        )

    with transaction.atomic():
        try:
            ledger.append(
                moves,
                tenant_id=request.tenant_id,
                actor=request.user,
                request_id=request_id.get(),
            )
        except ledger.Refused as refusal:
            raise HttpError(422, str(refusal)) from refusal
        if payload.resolution == TransferResolution.RECEIVED_LATE:
            line.quantity_received = line.quantity_dispatched
        line.resolution = payload.resolution
        line.save(update_fields=["quantity_received", "resolution", "updated_at"])

        transfer.refresh_from_db()
        if not any(_in_transit(one) > 0 for one in transfer.lines.all()):
            transfer.status = TransferStatus.RECEIVED
            transfer.save(update_fields=["status", "updated_at"])
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.UPDATE,
            entity_type="transfer_lines",
            entity_id=line.id,
            before={"resolution": ""},
            after={
                "resolution": payload.resolution,
                "units": short,
                "transfer": transfer.number,
            },
            request_id=request_id.get(),
        )
    return _transfer_out(_transfer(request, transfer_id))


# ---------------------------------------------------------------------------
# Cycle counts
# ---------------------------------------------------------------------------


def _counts_for(request):
    return (
        StockCount.objects.filter(
            tenant_id=request.tenant_id, location_id__in=_readable(request)
        )
        .select_related("location", "category")
        .prefetch_related("lines__item", "lines__lot")
    )


def _count_out(count, *, negative_keys=frozenset(), negatives=()):
    lines = list(count.lines.all())
    costs = {line.lot_id: line.lot.unit_cost if line.lot_id else None for line in lines}
    differences = [
        line for line in lines if line.counted_quantity != line.expected_quantity
    ]
    value = sum(
        abs(line.counted_quantity - line.expected_quantity)
        * (costs.get(line.lot_id) or Decimal("0"))
        for line in differences
    )
    return {
        "id": count.id,
        "location_id": count.location_id,
        "location_name": count.location.name,
        "scope": count.scope,
        "category_id": count.category_id,
        "category_name": count.category.name if count.category_id else None,
        "status": count.status,
        "counted_by_name": count.counted_by_name,
        "closed_by_name": count.closed_by_name,
        "closed_at": count.closed_at,
        "recorded_at": count.recorded_at,
        "lines_count": len(lines),
        "differences": len(differences),
        "difference_value": Decimal(value).quantize(Decimal("0.01")),
        "lines": [
            {
                "id": line.id,
                "item_id": line.item_id,
                "item_name": line.item.name,
                "lot_id": line.lot_id,
                "lot_code": line.lot.lot_code if line.lot else None,
                "expected_quantity": line.expected_quantity,
                "counted_quantity": line.counted_quantity,
                "difference": line.counted_quantity - line.expected_quantity,
                "unit_cost": costs.get(line.lot_id),
                "entered_at": line.entered_at,
                "resolves_negative": (
                    count.location_id,
                    line.item_id,
                    line.lot_id,
                )
                in negative_keys,
            }
            for line in lines
        ],
        "negatives": list(negatives),
    }


@router.get("/stock-counts", response=CountPage, auth=any_member)
def list_counts(
    request,
    location_id: list[uuid.UUID] = Query(None),
    status: CountStatusValue | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
):
    queryset = _counts_for(request)
    if location_id:
        queryset = queryset.filter(
            location_id__in=_readable(request, requested=location_id)
        )
    if status:
        queryset = queryset.filter(status=status)
    rows, row_count, page, page_size = paginate(
        queryset.order_by("-recorded_at"),
        page=page,
        page_size=page_size,
        sort=None,
        order="asc",
        sortable={},
    )
    rows = list(rows)
    negatives = _open_negatives(request, rows)
    return {
        "rows": [
            _count_out(
                row,
                negative_keys=_negative_keys(request, row),
                negatives=negatives.get(row.location_id, ()),
            )
            for row in rows
        ],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
        "due_locations": _due_locations(request),
    }


def _open_negatives(request, counts) -> dict:
    """The open negative-stock exceptions at each of these counts' sedes.

    One query over `sync_conflicts` for the whole page, and one each over
    `items` and `lots` to name them -- **the exception is useless as a row of
    identifiers**, and a person walking a back room needs the reference and the
    lot code they will find on the box.

    Read from the conflict rows rather than recomputed from the projection,
    because an oversell an administrator has already resolved is closed there
    and would otherwise come back on every load.
    """
    locations = {count.location_id for count in counts}
    if not locations:
        return {}
    rows = list(
        SyncConflict.objects.filter(
            tenant_id=request.tenant_id,
            location_id__in=locations,
            type=SyncConflictType.NEGATIVE_STOCK,
            status=SyncConflictStatus.OPEN,
        ).order_by("-recorded_at", "-id")
    )
    if not rows:
        return {}
    item_ids, lot_ids = set(), set()
    for row in rows:
        detail = row.detail or {}
        if detail.get("item_id"):
            item_ids.add(detail["item_id"])
        if detail.get("lot_id"):
            lot_ids.add(detail["lot_id"])
    names = dict(
        Item.objects.filter(tenant_id=request.tenant_id, id__in=item_ids).values_list(
            "id", "name"
        )
    )
    codes = dict(
        Lot.objects.filter(tenant_id=request.tenant_id, id__in=lot_ids).values_list(
            "id", "lot_code"
        )
    )
    counted = {
        (line.count.location_id, str(line.item_id), str(line.lot_id or ""))
        for count in counts
        for line in count.lines.all()
    }
    answer: dict = {}
    for row in rows:
        detail = row.detail or {}
        item_id = detail.get("item_id")
        if not item_id:
            continue
        lot_id = detail.get("lot_id")
        answer.setdefault(row.location_id, []).append(
            {
                "conflict_id": row.id,
                "item_id": item_id,
                "item_name": names.get(uuid.UUID(item_id), ""),
                "lot_id": lot_id,
                "lot_code": codes.get(uuid.UUID(lot_id)) if lot_id else None,
                "quantity": int(detail.get("quantity") or 0),
                "recorded_at": row.recorded_at,
                "counted": (row.location_id, item_id, lot_id or "") in counted,
            }
        )
    return answer


def _due_locations(request):
    """Which sedes are past `count_cadence_days` since their last closed count.

    **A location that has never been counted is due, not silent**: a sede with no
    count at all is exactly the one somebody should walk.
    """
    options = _options(request)
    cadence = timedelta(days=int(options["count_cadence_days"]))
    allowed = _readable(request)
    last = {
        row["location_id"]: row["last"]
        for row in StockCount.objects.filter(
            tenant_id=request.tenant_id,
            location_id__in=allowed,
            status=CountStatus.CLOSED,
        )
        .values("location_id")
        .annotate(last=Max("closed_at"))
    }
    now = timezone.now()
    return [
        {
            "location_id": location.id,
            "location_name": location.name,
            "last_closed_at": last.get(location.id),
            "due": last.get(location.id) is None or last[location.id] < now - cadence,
        }
        for location in Location.objects.filter(id__in=allowed).order_by("name")
    ]


@router.post("/stock-counts", response=CountOut, auth=owner_or_admin)
def create_count(request, payload: CountIn):
    _writable_location(request, payload.location_id)
    if payload.scope == CountScope.CATEGORY and payload.category_id is None:
        raise HttpError(422, "Un conteo por categoría necesita una categoría.")
    if payload.category_id:
        get_object_or_404(Category, id=payload.category_id, tenant_id=request.tenant_id)
    client_uuid = payload.client_uuid or uuid.uuid4()
    existing = StockCount.objects.filter(
        tenant_id=request.tenant_id, client_uuid=client_uuid
    ).first()
    if existing is not None:
        return _count_out(_count(request, existing.id))

    count = StockCount.objects.create(
        tenant_id=request.tenant_id,
        location_id=payload.location_id,
        scope=payload.scope,
        category_id=payload.category_id,
        status=CountStatus.COUNTING,
        counted_by_user=request.user,
        counted_by_name=request.user.name,
        client_uuid=client_uuid,
        occurred_at=payload.occurred_at or timezone.now(),
        recorded_at=timezone.now(),
    )
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="stock_counts",
        entity_id=count.id,
        after={"location_id": str(count.location_id), "scope": count.scope},
        request_id=request_id.get(),
    )
    return _count_out(_count(request, count.id))


def _count(request, count_id):
    return get_object_or_404(_counts_for(request), id=count_id)


@router.post("/stock-counts/{count_id}/lines", response=CountOut, auth=any_member)
def enter_count_lines(request, count_id: uuid.UUID, payload: CountLinesIn):
    """Enter or update counted lines.

    **`expected_quantity` is stamped when the line is entered**, not at close --
    which is the whole arithmetic of a count. The adjusting move is the
    discrepancy measured at entry, and every sale made while the count is open
    applies on top of it. Stamping at close instead double-counts them.

    Idempotent on `client_uuid` per line, so an offline device replaying a batch
    does not double-count (A5). A second entry of the same `(item, lot)` under a
    **new** key is a person recounting: the counted figure moves and the stamp
    stays, because "entered" happened once.
    """
    count = _count(request, count_id)
    if count.status == CountStatus.CLOSED:
        raise HttpError(409, "Este conteo ya está cerrado.")
    _writable_location(request, count.location_id)

    with transaction.atomic():
        for line in payload.lines:
            _enter_line(request, count, line)
    # Re-read: the count above carries a prefetched line set from before this
    # request wrote any, and a screen rendered off it would show a person the
    # lines they entered a moment ago as absent.
    fresh = _count(request, count_id)
    return _count_out(
        fresh,
        negative_keys=_negative_keys(request, fresh),
        negatives=_open_negatives(request, [fresh]).get(fresh.location_id, ()),
    )


def _enter_line(request, count, line):
    item = get_object_or_404(Item, id=line.item_id, tenant_id=request.tenant_id)
    if item.tracks_lots and line.lot_id is None:
        raise HttpError(
            422, f"«{item.name}» se maneja por lote, así que la línea necesita uno."
        )
    if not item.tracks_stock:
        # A7 · a service on a count line is not an error and is not a line: it
        # has no stock to count.
        return
    client_uuid = line.client_uuid or uuid.uuid4()
    if StockCountLine.objects.filter(
        tenant_id=request.tenant_id, client_uuid=client_uuid
    ).exists():
        return

    existing = StockCountLine.objects.filter(
        tenant_id=request.tenant_id,
        count=count,
        item_id=line.item_id,
        lot_id=line.lot_id,
    ).first()
    if existing is not None:
        existing.counted_quantity = line.counted_quantity
        existing.save(update_fields=["counted_quantity", "updated_at"])
        return

    expected = (
        StockOnHand.objects.filter(
            tenant_id=request.tenant_id,
            location_id=count.location_id,
            item_id=line.item_id,
            lot_id=line.lot_id,
        )
        .values_list("quantity", flat=True)
        .first()
        or 0
    )
    StockCountLine.objects.create(
        tenant_id=request.tenant_id,
        count=count,
        item_id=line.item_id,
        lot_id=line.lot_id,
        expected_quantity=expected,
        counted_quantity=line.counted_quantity,
        entered_at=timezone.now(),
        client_uuid=client_uuid,
        occurred_at=line.occurred_at or timezone.now(),
        recorded_at=timezone.now(),
    )


def _negative_keys(request, count):
    """The `(location, item, lot)` keys this count covers that are below zero.

    Shown inline on the counting screen, because §5 fixes this as the place an
    oversell is resolved and a person walking a shelf should see the exception
    they are about to close.
    """
    keys = {
        (count.location_id, line.item_id, line.lot_id) for line in count.lines.all()
    }
    if not keys:
        return frozenset()
    negative = list(
        StockOnHand.objects.filter(
            tenant_id=request.tenant_id,
            location_id=count.location_id,
            quantity__lt=0,
        ).values_list("item_id", "lot_id")
    )
    return {
        (count.location_id, item_id, lot_id)
        for item_id, lot_id in negative
        if (count.location_id, item_id, lot_id) in keys
    }


@router.post("/stock-counts/{count_id}/close", response=CountOut, auth=owner_or_admin)
def close_count(request, count_id: uuid.UUID):
    """`counting → closed`. Writes one `count` move per differing line and
    resolves any open negative-stock exception it covers, **in the same
    transaction**.

    A count that wrote the move and left the exception open would leave the
    office chasing a discrepancy somebody had already fixed.
    """
    count = _count(request, count_id)
    if count.status == CountStatus.CLOSED:
        raise HttpError(409, "Este conteo ya está cerrado.")
    _writable_location(request, count.location_id)

    negative = _negative_keys(request, count)
    moves = []
    for line in count.lines.all():
        difference = line.counted_quantity - line.expected_quantity
        if difference == 0:
            continue
        key = (count.location_id, line.item_id, line.lot_id)
        moves.append(
            ledger.Move(
                location_id=count.location_id,
                item_id=line.item_id,
                lot_id=line.lot_id,
                quantity=difference,
                type=StockMoveType.COUNT,
                reason=(
                    "negative_resolution" if key in negative else "count_adjustment"
                ),
                document_type="stock_counts",
                document_id=count.id,
                key=f"count:{count.id}:{line.id}",
            )
        )

    with transaction.atomic():
        if moves:
            try:
                ledger.append(
                    moves,
                    tenant_id=request.tenant_id,
                    actor=request.user,
                    request_id=request_id.get(),
                )
            except ledger.Refused as refusal:
                raise HttpError(422, str(refusal)) from refusal
        ledger.resolve_negative(
            tenant_id=request.tenant_id,
            keys=[
                (count.location_id, line.item_id, line.lot_id)
                for line in count.lines.all()
            ],
            actor=request.user,
        )
        count.status = CountStatus.CLOSED
        count.closed_at = timezone.now()
        count.closed_by_user = request.user
        count.closed_by_name = request.user.name
        count.save(
            update_fields=[
                "status",
                "closed_at",
                "closed_by_user",
                "closed_by_name",
                "updated_at",
            ]
        )
        audit.record(
            actor=request.user,
            tenant_id=request.tenant_id,
            action=AuditAction.UPDATE,
            entity_type="stock_counts",
            entity_id=count.id,
            before={"status": CountStatus.COUNTING},
            after={"status": count.status, "moves": len(moves)},
            request_id=request_id.get(),
        )
    fresh = _count(request, count_id)
    return _count_out(
        fresh,
        negatives=_open_negatives(request, [fresh]).get(fresh.location_id, ()),
    )


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@router.get("/stock-policies", response=Page[PolicyOut], auth=any_member)
def list_policies(
    request,
    item_id: uuid.UUID | None = Query(None),
    location_id: list[uuid.UUID] = Query(None),
    source: PolicySourceValue | None = Query(None),
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
):
    """Thresholds by item and location, **with `source` on every row** -- the
    one column that tells a regente which numbers are theirs."""
    allowed = _readable(request, requested=location_id)
    queryset = (
        StockPolicy.objects.filter(tenant_id=request.tenant_id)
        .filter(Q(location_id__in=allowed) | Q(location_id__isnull=True))
        .select_related("item", "location")
    )
    if item_id:
        queryset = queryset.filter(item_id=item_id)
    if source:
        queryset = queryset.filter(source=source)
    rows, row_count, page, page_size = paginate(
        queryset.order_by("item__name", "location__name"),
        page=page,
        page_size=page_size,
        sort=None,
        order="asc",
        sortable={},
    )
    return {
        "rows": [_policy_out(row) for row in rows],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _policy_audit(row):
    """The JSON-safe half of a policy row, for the trail.

    `audit_log.before`/`after` are `jsonb`, and a uuid is not JSON -- so the ids
    are stringified here rather than at the call site, where the next stage
    writing a threshold would have to remember.
    """
    if row is None:
        return None
    written = _policy_out(row)
    return {
        **written,
        "id": str(written["id"]),
        "item_id": str(written["item_id"]),
        "location_id": (
            str(written["location_id"]) if written["location_id"] else None
        ),
    }


def _policy_out(row):
    return {
        "id": row.id,
        "item_id": row.item_id,
        "item_name": row.item.name,
        "location_id": row.location_id,
        "location_name": row.location.name if row.location_id else None,
        "min_quantity": row.min_quantity,
        "max_quantity": row.max_quantity,
        "reorder_point": row.reorder_point,
        "target_coverage_days": row.target_coverage_days,
        "source": row.source,
    }


@router.put("/stock-policies", response=list[PolicyOut], auth=owner_or_admin)
def write_policies(request, payload: PolicyWriteIn):
    """Write thresholds, one item or many, **always at `source = manual`**."""
    if not payload.policies:
        raise HttpError(422, "No hay umbrales que guardar.")
    written = []
    with transaction.atomic():
        for entry in payload.policies:
            if entry.location_id is not None:
                _writable_location(request, entry.location_id)
            get_object_or_404(Item, id=entry.item_id, tenant_id=request.tenant_id)
            scope = (
                Q(location_id=entry.location_id)
                if entry.location_id
                else Q(location_id__isnull=True)
            )
            row = StockPolicy.objects.filter(
                scope, tenant_id=request.tenant_id, item_id=entry.item_id
            ).first()
            before = _policy_audit(row)
            if row is None:
                row = StockPolicy(
                    tenant_id=request.tenant_id,
                    item_id=entry.item_id,
                    location_id=entry.location_id,
                )
            row.min_quantity = entry.min_quantity
            row.max_quantity = entry.max_quantity
            row.reorder_point = entry.reorder_point
            row.target_coverage_days = entry.target_coverage_days
            # **A write over a `model` row flips `source` back** -- that is the
            # point of the column, and it is what S6 has to preserve.
            row.source = PolicySource.MANUAL
            row.save()
            written.append(row)
            audit.record(
                actor=request.user,
                tenant_id=request.tenant_id,
                action=AuditAction.UPDATE if before else AuditAction.CREATE,
                entity_type="stock_policies",
                entity_id=row.id,
                before=before,
                after=_policy_audit(
                    StockPolicy.objects.select_related("item", "location").get(
                        id=row.id
                    )
                ),
                request_id=request_id.get(),
            )
    return [
        _policy_out(row)
        for row in StockPolicy.objects.select_related("item", "location").filter(
            id__in=[one.id for one in written]
        )
    ]


# ---------------------------------------------------------------------------
# The `inventory` settings group (ledger rule 5)
# ---------------------------------------------------------------------------


@router.get("/settings/inventory", response=InventorySettingsOut, auth=owner_or_admin)
def read_inventory_settings(request):
    return inventory_settings.read(_tenant(request))


@router.patch("/settings/inventory", response=InventorySettingsOut, auth=owner_or_admin)
def write_inventory_settings(request, payload: InventorySettingsIn):
    """Write the group through S0's helper, which issues one `jsonb_set` and
    leaves every other group as it stands (ledger rule 5)."""
    tenant = _tenant(request)
    before = inventory_settings.read(tenant)
    values = {
        key: value
        for key, value in payload.dict(exclude_unset=True).items()
        if value is not None
    }
    try:
        after = inventory_settings.write(tenant, values)
    except inventory_settings.Invalid as refusal:
        raise HttpError(422, str(refusal)) from refusal
    audit.record(
        actor=request.user,
        tenant_id=tenant.id,
        action=AuditAction.UPDATE,
        entity_type="settings.inventory",
        entity_id=tenant.id,
        before=before,
        after=after,
        request_id=request_id.get(),
    )
    return after
