"""What a sale, a shift and a return *do* once they land, in one module.

**Nothing in this stage creates a counter sale over HTTP.** Every sale, line,
payment, shift and return originates on a device and arrives through S2's
`POST /api/sync/push` (A5, §5 rule 5); this module is what the push writers and
the office's two mutations both call, so a void reverses stock the same way
whichever door it came through.

**Every stock movement here is an append through S3's ledger service** (rule 7,
A3). Nothing in this file writes `stock_moves` or `stock_on_hand`, and every
move carries a deterministic natural key -- `sale_line:{id}`,
`return_line:{id}`, `void:{id}` -- so a replayed push, a double-pressed button
and a re-run seed are all no-ops at the ledger, exactly as a duplicated batch is
at the push.

**Two of §5's three reconciliations are raised here**, as an offline sale
arrives, one row per offending line and never as a refusal: `stale_price` where
the line's stamped `unit_price` differs from the `item_prices` row effective on
arrival, and `catalog_divergence` where the line names an item deactivated while
the till was offline. In both cases the sale stands at what was actually
charged -- a cashier who sold a box that was on the shelf is right about the
world and the catalog is late. The third, negative stock, is S3's and is raised
by the ledger service in the same transaction.
"""

import re
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from core.counter import money
from core.inventory import ledger
from core.models import (
    Item,
    ItemPrice,
    Payment,
    PaymentMethod,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    Shift,
    ShiftStatus,
    StockMoveType,
    SyncConflictType,
)
from core.sync import conflicts as conflict_service

#: `{device code}-{per-device sequence}`, and the composition is the point.
#:
#: The number must be allocated locally, offline, and must never collide across
#: two tills in one sede; the only mechanism that gives a bare integer that
#: property is a central allocator or a lease, neither of which exists at v1
#: (A6, deferred). `devices.code` is unique network-wide, so the pair is unique
#: network-wide by construction -- and a composed number can never be read as
#: one some other system issued, which is the failure the ledger singles out as
#: the most likely modelling error in the product (§8).
NUMBER = re.compile(r"^[A-Z0-9]{1,16}-[0-9]{1,12}$")


def compose_number(device_code: str, sequence: int) -> str:
    return f"{device_code.upper()}-{int(sequence)}"


def valid_number(value: str) -> bool:
    return bool(value) and bool(NUMBER.match(value))


# ---------------------------------------------------------------------------
# The two reconciliations S4 owns (§5)
# ---------------------------------------------------------------------------


def effective_price(*, tenant_id, item_id, location_id, on):
    """This sede's price for one item on one day, resolved the way the till
    resolves it: the sede's own row beats the network-wide one, the window is
    `[effective_from, effective_to)` and the later `effective_from` wins.

    Returns `None` where the item has no price in force, which is not the same
    as zero and is why `stale_price` is not raised for it -- an item with no
    price row is a catalog gap, and reporting it as a price *change* would send
    an administrator looking for an edit nobody made.
    """
    # **`Q(location_id__isnull=True)` and not `location_id__in=[id, None]`.**
    # SQL's `IN` never matches NULL, so the `__in` form silently drops every
    # network-wide price -- which is most of them, and which reads as a catalog
    # with no prices at all rather than as a bug.
    rows = ItemPrice.objects.filter(
        Q(tenant_id=tenant_id, item_id=item_id, effective_from__lte=on)
        & (Q(location_id=location_id) | Q(location_id__isnull=True))
        & (Q(effective_to__isnull=True) | Q(effective_to__gt=on))
    )
    best = None
    for row in rows:
        if best is None or _price_wins(row, best):
            best = row
    return best.price if best is not None else None


def _price_wins(row, held) -> bool:
    scoped = row.location_id is not None
    held_scoped = held.location_id is not None
    if scoped != held_scoped:
        return scoped
    return row.effective_from > held.effective_from


def reconcile_line(line, *, item, device, request_id=""):
    """Raise what an offline sale brought with it, for one line.

    Both are reported and neither corrects anything: the sale stands at the
    price actually charged, and `sale_lines.unit_price` is the record (§5).
    `detail` carries the collection, the sale, the line, the item and the two
    figures or states -- **never the payload verbatim**, which is S2's rule and
    applies to S4's rows too.
    """
    if not item.active:
        conflict_service.raise_conflict(
            device=device,
            type=SyncConflictType.CATALOG_DIVERGENCE,
            collection="sale_lines",
            client_uuid=line.client_uuid,
            occurred_at=line.occurred_at,
            detail={
                "reason": "item_inactive",
                "sale_id": line.sale_id,
                "line_id": line.id,
                "item_id": line.item_id,
                "charged": str(money.cents(line.unit_price)),
                "state": "inactive",
                "request_id": request_id,
            },
        )

    current = effective_price(
        tenant_id=line.tenant_id,
        item_id=line.item_id,
        location_id=line.location_id,
        on=timezone.localdate(line.recorded_at),
    )
    if current is None:
        return
    if money.cents(current) == money.cents(line.unit_price):
        return
    conflict_service.raise_conflict(
        device=device,
        type=SyncConflictType.STALE_PRICE,
        collection="sale_lines",
        client_uuid=line.client_uuid,
        occurred_at=line.occurred_at,
        detail={
            "reason": "price_moved_while_offline",
            "sale_id": line.sale_id,
            "line_id": line.id,
            "item_id": line.item_id,
            "charged": str(money.cents(line.unit_price)),
            "effective": str(money.cents(current)),
            "request_id": request_id,
        },
    )


# ---------------------------------------------------------------------------
# Stock, always through S3's service (rule 7)
# ---------------------------------------------------------------------------


def sell_line(
    line, *, item, device=None, actor=None, request_id="", fefo_override=None
):
    """Take one line's units off the shelf.

    **The till's own observation of the FEFO deviation is passed through and
    believed** (§6). The counter showed the lot queue and watched a cashier pick
    past its head; the server, applying a sale that happened three hours ago,
    would recompute the head against a projection that has moved since -- and a
    stamp derived from the wrong instant is worse than no stamp. Where the till
    says nothing, the ledger derives it, which is its documented default.

    An item whose `tracks_stock` is false writes nothing at all and is not a
    failure of the line (A7) -- a service on a ticket has no stock, and a row
    saying it has none would be a row somebody later sums.
    """
    if not item.tracks_stock:
        return None
    return ledger.append(
        [
            ledger.Move(
                location_id=line.location_id,
                item_id=line.item_id,
                lot_id=line.lot_id,
                quantity=-int(line.quantity),
                type=StockMoveType.SALE,
                document_type="sales",
                document_id=line.sale_id,
                unit_cost=line.unit_cost,
                # **Both clocks, and the till owns the first one** (§5 rule 4).
                # The box left the shelf when the customer took it, not when the
                # link came back.
                occurred_at=line.occurred_at,
                fefo_override=fefo_override,
                key=f"sale_line:{line.id}",
            )
        ],
        tenant_id=line.tenant_id,
        actor=actor,
        device=device,
        request_id=request_id,
    )


def return_line_stock(row, *, item, device=None, actor=None, request_id=""):
    """Put one returned line's units back **on the lot they left on**, or a
    recall becomes unanswerable (§6)."""
    if not item.tracks_stock:
        return None
    return ledger.append(
        [
            ledger.Move(
                location_id=row.location_id,
                item_id=row.item_id,
                lot_id=row.lot_id,
                quantity=int(row.quantity),
                type=StockMoveType.CUSTOMER_RETURN,
                document_type="sale_returns",
                document_id=row.sale_return_id,
                unit_cost=row.unit_cost,
                occurred_at=row.occurred_at,
                key=f"return_line:{row.id}",
            )
        ],
        tenant_id=row.tenant_id,
        actor=actor,
        device=device,
        request_id=request_id,
    )


# ---------------------------------------------------------------------------
# The sale's own state machine
# ---------------------------------------------------------------------------


def restate_totals(sale) -> money.Totals:
    """Recompute a ticket's four figures from its own lines and store them.

    **The server recomputes rather than trusts.** A till's totals are a
    browser's arithmetic; the prices it stamped are taken exactly as sent,
    because `sale_lines.unit_price` records what was actually charged and no
    later price list may restate it (§5). The two are different questions and
    only the first is the server's.
    """
    figures = money.totals(sale.lines.all())
    for name, value in figures.as_fields().items():
        setattr(sale, name, value)
    sale.save(update_fields=["subtotal", "discount", "tax", "total", "updated_at"])
    return figures


def close(sale, *, closed_at=None, request_id=""):
    """Flip a ticket to `closed` and restate its totals.

    **The stock has already moved.** Each line's `sale` move is appended as the
    line lands, because a line only ever reaches the server inside the batch
    that closes its ticket -- an open ticket pushes its header on the ordinary
    delta cadence and never its lines (§5) -- and the line's arrival is
    therefore the sale happening. Doing it here instead would mean holding the
    till's own FEFO observation somewhere between the two, which would be a
    second stamp beside the ledger's and a second truth to keep in step (§6).

    **This is where S5 attaches.** From S5 onward, and only where an invoicing
    target is configured, this is the point that calls the sale handoff service,
    which builds the canonical document and enqueues its delivery inside this
    same pinned transaction (ledger, cross-stage services). S4 assembles no
    payload, knows no target's field names and waits for no target; with no
    target configured the call is not made and nothing is enqueued, which is the
    default and the state every demo runs in (§8).
    """
    del request_id
    if sale.status != SaleStatus.OPEN:
        return sale
    sale.status = SaleStatus.CLOSED
    sale.closed_at = closed_at or timezone.now()
    sale.save(update_fields=["status", "closed_at", "updated_at"])
    restate_totals(sale)
    return sale


def void(sale, *, actor=None, device=None, request_id="", reason="", at=None):
    """Reverse a sale's stock and mark it `voided`. **No row is ever deleted.**

    The reversing moves are `customer_return`, which is the one positive type
    this stage causes (ledger, `stock_moves.type`). It is not a customer
    returning anything and does not pretend to be: the move carries
    `document_type = 'sales'` and the voided sale's own id, so the lot trace and
    the record panel both read it as the void it is. Inventing an eleventh enum
    value for it would put a value in the type nobody outside this one path ever
    writes.

    Once S5 exists, a sale whose canonical document already reached the client's
    invoicing system is corrected **in that system**, by whatever instrument it
    issues -- Botica records the void and issues no fiscal correction of its own
    (ledger, §8).
    """
    if sale.status == SaleStatus.VOIDED:
        return sale
    items = _items(sale.tenant_id, sale.lines.all())
    # **Only what is still out the door.** A unit a devolución already put back
    # would otherwise be credited to the shelf twice, and the second credit is
    # merchandise that never existed. Both callers refuse a void once anything
    # has been returned; the arithmetic is here as well because a reversal that
    # depended on its caller checking first is a reversal that will one day be
    # called by a third one.
    outstanding = returnable(sale)
    moves = []
    for line in sale.lines.all():
        item = items.get(line.item_id)
        quantity = outstanding.get(line.id, int(line.quantity))
        if item is None or not item.tracks_stock or quantity <= 0:
            continue
        moves.append(
            ledger.Move(
                location_id=line.location_id,
                item_id=line.item_id,
                lot_id=line.lot_id,
                quantity=quantity,
                type=StockMoveType.CUSTOMER_RETURN,
                document_type="sales",
                document_id=sale.id,
                unit_cost=line.unit_cost,
                key=f"void:{line.id}",
            )
        )
    if moves:
        ledger.append(
            moves,
            tenant_id=sale.tenant_id,
            actor=actor,
            device=device,
            request_id=request_id,
        )
    sale.status = SaleStatus.VOIDED
    sale.voided_at = at or timezone.now()
    sale.void_reason = (reason or "")[:500]
    sale.save(update_fields=["status", "voided_at", "void_reason", "updated_at"])
    return sale


def _items(tenant_id, lines):
    ids = {line.item_id for line in lines}
    return {
        item.id: item
        for item in Item.objects.filter(tenant_id=tenant_id, id__in=list(ids))
    }


# ---------------------------------------------------------------------------
# The turno's arithmetic
# ---------------------------------------------------------------------------


def cash_taken(shift) -> Decimal:
    """Cash applied to this turno's closed sales.

    Voided sales are excluded: the money went back over the counter, so it is
    not in the drawer at close and counting it would manufacture a shortfall out
    of a correction.
    """
    total = Payment.objects.filter(
        tenant_id=shift.tenant_id,
        sale__shift_id=shift.id,
        sale__status=SaleStatus.CLOSED,
        method=PaymentMethod.CASH,
    ).aggregate(total=Sum("amount"))["total"]
    return money.cents(total)


def cash_refunded(shift) -> Decimal:
    """Cash refunded out of this turno's drawer, whichever turno the sale was
    rung in: money leaves the drawer that is open now."""
    total = SaleReturn.objects.filter(
        tenant_id=shift.tenant_id,
        shift_id=shift.id,
        refund_method=PaymentMethod.CASH,
    ).aggregate(total=Sum("total"))["total"]
    return money.cents(total)


def expected_cash(shift) -> Decimal:
    """`Efectivo esperado` -- what the drawer should hold, stated before the
    cashier is asked for anything."""
    return money.cents(
        money.cents(shift.opening_float) + cash_taken(shift) - cash_refunded(shift)
    )


def close_shift(shift, *, declared_total, closed_at=None):
    """Close a turno with a count, and **store the difference whatever it is**.

    `variance = declared_total − expected`. Positive, negative or zero, it is
    stored: a till that quietly reconciles its own shortfalls is a till nobody
    can audit (ledger, disputed columns).
    """
    declared = money.cents(declared_total)
    shift.declared_total = declared
    shift.variance = money.cents(declared - expected_cash(shift))
    shift.closed_at = closed_at or timezone.now()
    shift.status = ShiftStatus.CLOSED
    shift.save(
        update_fields=[
            "declared_total",
            "variance",
            "closed_at",
            "status",
            "updated_at",
        ]
    )
    return shift


def force_close(shift, *, reason, at=None):
    """Close a turno whose device is gone or whose cashier left without counting.

    **`declared_total` and `variance` stay null.** A forced close is not a count
    and is never rendered as one; a zero here would be a count of an empty
    drawer, which is a different and much worse claim.
    """
    shift.closed_at = at or timezone.now()
    shift.status = ShiftStatus.CLOSED
    shift.declared_total = None
    shift.variance = None
    shift.forced_close_reason = (reason or "")[:500]
    shift.save(
        update_fields=[
            "closed_at",
            "status",
            "declared_total",
            "variance",
            "forced_close_reason",
            "updated_at",
        ]
    )
    return shift


def shift_report(shift) -> dict:
    """What the close dialog shows before it asks for anything, and what the
    office's Turnos row and record panel both render."""
    expected = expected_cash(shift)
    return {
        "opening_float": money.cents(shift.opening_float),
        "cash_sales": cash_taken(shift),
        "cash_returns": cash_refunded(shift),
        "expected_total": expected,
        "declared_total": (
            None if shift.declared_total is None else money.cents(shift.declared_total)
        ),
        "variance": None if shift.variance is None else money.cents(shift.variance),
    }


def payment_breakdown(shift) -> list[dict]:
    """This turno's takings by method, which is what `GET /api/shifts/{id}` has
    to render and what makes the cash figure above readable as a share."""
    rows = (
        Payment.objects.filter(
            tenant_id=shift.tenant_id,
            sale__shift_id=shift.id,
            sale__status=SaleStatus.CLOSED,
        )
        .values("method")
        .annotate(total=Sum("amount"))
        .order_by("method")
    )
    return [
        {"method": row["method"], "amount": money.cents(row["total"])} for row in rows
    ]


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------


def returnable(sale) -> dict:
    """`sale_line_id -> units still returnable`, after every earlier return.

    This is what the devolución's quantity stepper is capped at, and what the
    empty state `Esta venta no tiene unidades por devolver.` is derived from.
    """
    taken = {
        row["sale_line_id"]: row["total"]
        for row in SaleReturnLine.objects.filter(
            tenant_id=sale.tenant_id, sale_line__sale_id=sale.id
        )
        .values("sale_line_id")
        .annotate(total=Sum("quantity"))
    }
    return {
        line.id: max(0, int(line.quantity) - int(taken.get(line.id, 0)))
        for line in sale.lines.all()
    }


def restate_return_totals(sale_return) -> money.Totals:
    """A return's money composes exactly as a sale's, because a credit note
    reverses what was charged."""
    figures = money.totals(sale_return.lines.all())
    sale_return.total = figures.total
    sale_return.tax = figures.tax
    sale_return.save(update_fields=["total", "tax", "updated_at"])
    return figures


def stamp_return_line(row, line):
    """Copy the original line's money onto the return line.

    **Not today's price list.** A credit note must reverse what was charged, and
    a price that changed in between is exactly the case §5 says the sale's own
    record settles. The per-unit discount is prorated so a partial return of a
    discounted line refunds its share rather than the whole discount or none of
    it.
    """
    row.item_id = line.item_id
    row.lot_id = line.lot_id
    row.unit_price = money.cents(line.unit_price)
    row.vat_class = line.vat_class
    row.unit_cost = line.unit_cost
    share = Decimal(int(row.quantity)) / Decimal(int(line.quantity))
    row.discount = money.cents(money.cents(line.discount) * share)
    row.tax_amount = money.line_tax(
        row.unit_price, row.quantity, row.discount, row.vat_class
    )
    return row


def line_by_id(sale, sale_line_id):
    return SaleLine.objects.filter(
        tenant_id=sale.tenant_id, sale_id=sale.id, id=sale_line_id
    ).first()


def open_shift_for(device) -> Shift | None:
    return Shift.objects.filter(
        tenant_id=device.tenant_id, device_id=device.id, status=ShiftStatus.OPEN
    ).first()


def open_sales(tenant_id, location_ids) -> int:
    """§B.8.2 · the `Mostrador` nav counter for an office identity -- ventas
    abiertas across the sedes that identity reads."""
    return Sale.objects.filter(
        tenant_id=tenant_id,
        location_id__in=list(location_ids),
        status=SaleStatus.OPEN,
        source="counter",
    ).count()
