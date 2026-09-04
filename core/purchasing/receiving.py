"""Receiving against an order, and returning to the supplier.

**Confirmation is one atomic act and it moves stock only through S3's ledger
service** (rule 7, A3). Nothing in this module writes a `stock_moves` row or a
`stock_on_hand` row -- it creates or attaches lots, hands the ledger one move
per line, and lets the projection follow. The database enforces the first half:
the runtime role holds no UPDATE on `stock_moves`.

**It is idempotent on the receipt.** Every move's `client_uuid` is derived from
`(goods_receipt_id, line_id)`, so a double-clicked `Confirmar recepción` or a
request retried after a timeout that committed moves stock exactly once -- which
is the failure A5 exists for and the one that actually happens.

**Over-delivery is accepted and flagged, never refused.** The supplier sent
them, they are on the shelf, and a receiving screen that refuses reality is a
screen that gets bypassed with a manual adjustment -- which is the same units
arriving with no order, no cost and no lot behind them.
"""

import logging
import uuid
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from core.inventory import ledger
from core.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
    GoodsReceiptType,
    Item,
    Lot,
    PurchaseOrder,
    PurchaseOrderLine,
    StockMoveType,
    Supplier,
    SupplierItem,
)
from core.purchasing import orders as order_service

logger = logging.getLogger(__name__)

#: How many observations the supplier's lead time is the median of. Ten, and a
#: **median** rather than a mean, because one forty-day back-order would
#: otherwise move the reorder point of every item that supplier carries.
LEAD_TIME_OBSERVATIONS = 10


class Refused(ValueError):
    """A receipt this module will not confirm, named in Spanish for the operator.

    `line` and `field` name the row and the control an operator has to change,
    where there is one -- a lot code missing on a lot-tracked reference is one
    line of an entry and one box on it, not a page-level failure (§B.10.3).
    """

    def __init__(self, message, *, line=None, field=""):
        super().__init__(message)
        self.line = line
        self.field = field


def resolve_lot(
    *, tenant_id, item, lot_code, expires_at, supplier_id, unit_cost, position
):
    """The lot this line's units join, created if it is new.

    A code that matches an existing lot for the same reference **and the same
    expiry** attaches to it. A code that matches with a *different* expiry is
    refused rather than merged: two different dates under one code is either a
    typo on the carton or a supplier reusing a code, and silently folding them
    together destroys the one answer a recall needs.
    """
    if not item.tracks_lots:
        if lot_code:
            raise Refused(
                f"«{item.name}» no se maneja por lote, así que esta línea no "
                "lleva código de lote.",
                line=position,
                field="lot_code",
            )
        return None

    code = (lot_code or "").strip()
    if not code:
        raise Refused(
            f"«{item.name}» se maneja por lote: escriba el código impreso en la caja.",
            line=position,
            field="lot_code",
        )
    if item.tracks_expiry and expires_at is None:
        raise Refused(
            f"«{item.name}» lleva fecha de vencimiento: escríbala como MM/AAAA.",
            line=position,
            field="expires_at",
        )

    standing = Lot.objects.filter(
        tenant_id=tenant_id, item_id=item.id, lot_code=code
    ).first()
    if standing is not None:
        if item.tracks_expiry and standing.expires_at != expires_at:
            raise Refused(
                f"El lote «{code}» de «{item.name}» ya existe con vencimiento "
                f"{standing.expires_at:%m/%Y}. Revise la fecha en la caja: dos "
                "fechas bajo un mismo código dejan una trazabilidad que nadie "
                "puede responder.",
                line=position,
                field="expires_at",
            )
        if unit_cost is not None and standing.unit_cost != unit_cost:
            Lot.objects.filter(id=standing.id).update(
                unit_cost=unit_cost, updated_at=timezone.now()
            )
            standing.unit_cost = unit_cost
        return standing

    return Lot.objects.create(
        tenant_id=tenant_id,
        item_id=item.id,
        lot_code=code,
        expires_at=expires_at if item.tracks_expiry else None,
        supplier_id=supplier_id,
        unit_cost=unit_cost,
    )


@transaction.atomic
def confirm(receipt, *, actor=None, request_id="", move_id_for=None):
    """Create or attach the lots, move the stock, write back what was paid.

    In order, and all of it inside one transaction so a failure leaves neither
    half: lots, one ledger call for every line, `lots.unit_cost` and
    `supplier_items.cost` from what was actually paid rather than what was
    quoted, `purchase_order_lines.received_quantity`, and the order settled at
    `received` or `partially_received` depending on what the supplier shorted.

    `move_id_for` exists for the demo seed and for nothing else, exactly as
    `ledger.Move.id` does: every seeded id is derived from a natural key, so a
    rebuilt seed keeps the ids it had and a screenshot, a saved link and a bug
    report still point at the same row. Left unset -- which is every other
    caller -- the ledger mints its own.
    """
    # **The row lock is what makes the early return below authoritative.**
    # Without it two requests can both read `draft`, both pass the check, and
    # both add what arrived to `purchase_order_lines.received_quantity` -- the
    # ledger would still move the stock once, because the moves collide on
    # `(tenant_id, client_uuid)`, but the order would settle `received` on a
    # delivery that came up short. The stock and the document have to agree, so
    # the second request waits here and then finds the receipt confirmed.
    standing = (
        GoodsReceipt.objects.select_for_update()
        .filter(id=receipt.id)
        .values_list("status", flat=True)
        .first()
    )
    if standing is None:
        # The row is gone. Nothing to confirm and nothing to say about it.
        return receipt
    if standing == GoodsReceiptStatus.CONFIRMED:
        receipt.status = GoodsReceiptStatus.CONFIRMED
        return receipt

    # **Ordered the way the screen orders them.** A field-scope refusal names a
    # line number, and a number counted over an unordered queryset points at
    # whichever row the planner happened to return third (§B.10.3).
    lines = list(
        GoodsReceiptLine.objects.filter(goods_receipt=receipt)
        .select_related("item")
        .order_by("item__name")
    )
    if not lines:
        raise Refused("Una recepción sin líneas no mueve nada. Escriba lo que llegó.")

    inbound = receipt.type == GoodsReceiptType.RECEIPT
    move_type = StockMoveType.RECEIPT if inbound else StockMoveType.SUPPLIER_RETURN

    moves = []
    for position, line in enumerate(lines, start=1):
        item = line.item
        if not item.tracks_stock:
            # A7 · a service on a receipt writes nothing and is not a failure of
            # the document. The ledger skips it too; this keeps the lot
            # resolution from asking a service for a code it cannot have.
            continue
        lot = resolve_lot(
            tenant_id=receipt.tenant_id,
            item=item,
            lot_code=line.lot_code,
            expires_at=line.expires_at,
            supplier_id=receipt.supplier_id,
            unit_cost=line.unit_cost,
            position=position,
        )
        if lot is not None and line.lot_id != lot.id:
            GoodsReceiptLine.objects.filter(id=line.id).update(
                lot=lot, updated_at=timezone.now()
            )
            line.lot = lot
            line.lot_id = lot.id
        moves.append(
            ledger.Move(
                id=move_id_for(line) if move_id_for else None,
                location_id=receipt.location_id,
                item_id=item.id,
                quantity=line.quantity if inbound else -line.quantity,
                type=move_type,
                lot_id=line.lot_id,
                document_type="goods_receipt",
                document_id=receipt.id,
                unit_cost=line.unit_cost,
                occurred_at=receipt.received_at,
                key=f"goods_receipt:{receipt.id}:{line.id}",
            )
        )

    ledger.append(
        moves,
        tenant_id=receipt.tenant_id,
        actor=actor,
        request_id=request_id,
    )

    now = timezone.now()
    GoodsReceipt.objects.filter(id=receipt.id).update(
        status=GoodsReceiptStatus.CONFIRMED,
        confirmed_at=now,
        received_by=actor if getattr(actor, "id", None) else None,
        received_by_name=getattr(actor, "name", "") or "",
        updated_at=now,
    )
    receipt.status = GoodsReceiptStatus.CONFIRMED
    receipt.confirmed_at = now
    receipt.received_by_name = getattr(actor, "name", "") or ""

    _write_back_cost(receipt, lines)
    if receipt.purchase_order_id and inbound:
        _apply_to_order(receipt, lines)
    return receipt


def _write_back_cost(receipt, lines):
    """`supplier_items.cost` from what a confirmed receipt actually paid.

    Per **purchase pack**, which is what that column holds -- the receipt lines
    carry a base-unit cost, so it is multiplied back up by `units_per_pack`. A
    cost quoted last quarter and a cost paid this morning are different numbers,
    and every margin figure downstream reads the second one.
    """
    if receipt.type != GoodsReceiptType.RECEIPT:
        return
    packs = dict(
        Item.objects.filter(id__in=[line.item_id for line in lines]).values_list(
            "id", "units_per_pack"
        )
    )
    for line in lines:
        if line.unit_cost is None:
            continue
        pack = max(1, int(packs.get(line.item_id) or 1))
        SupplierItem.objects.filter(
            tenant_id=receipt.tenant_id,
            supplier_id=receipt.supplier_id,
            item_id=line.item_id,
        ).update(
            cost=(line.unit_cost * Decimal(pack)).quantize(Decimal("0.01")),
            updated_at=timezone.now(),
        )


def _apply_to_order(receipt, lines):
    """Add what arrived to each order line and settle the order."""
    received: dict = {}
    for line in lines:
        if line.purchase_order_line_id:
            received[line.purchase_order_line_id] = (
                received.get(line.purchase_order_line_id, 0) + line.quantity
            )
    for line_id, quantity in received.items():
        # `F` rather than a read-modify-write: two receipts against one order
        # confirmed in the same second would otherwise lose one of them, and at
        # a six-sede network on a Monday morning that is not hypothetical.
        PurchaseOrderLine.objects.filter(id=line_id).update(
            received_quantity=F("received_quantity") + quantity,
            updated_at=timezone.now(),
        )
    order = PurchaseOrder.objects.filter(id=receipt.purchase_order_id).first()
    if order is not None:
        order_service.settle(order)


def observed_lead_time(tenant_id, supplier_id) -> int | None:
    """The rolling median of the last ten order-to-receipt intervals.

    A **median**, because one forty-day back-order would otherwise move the
    reorder point of every item that supplier carries for the next six months.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXTRACT(EPOCH FROM (r.received_at - o.sent_at)) / 86400
            FROM goods_receipts r
            JOIN purchase_orders o ON o.id = r.purchase_order_id
            WHERE r.tenant_id = %s
              AND r.supplier_id = %s
              AND r.type = 'receipt'
              AND r.status = 'confirmed'
              AND o.sent_at IS NOT NULL
              AND r.received_at >= o.sent_at
            ORDER BY r.received_at DESC
            LIMIT %s
            """,
            [str(tenant_id), str(supplier_id), LEAD_TIME_OBSERVATIONS],
        )
        observations = sorted(float(row[0]) for row in cursor.fetchall())
    if not observations:
        return None
    middle = len(observations) // 2
    if len(observations) % 2:
        median = observations[middle]
    else:
        median = (observations[middle - 1] + observations[middle]) / 2
    return max(1, round(median))


def refresh_lead_time(tenant_id, supplier_id) -> int | None:
    """Rewrite `suppliers.lead_time_days` from what was observed, or leave it.

    A supplier with no completed order-to-receipt interval keeps whatever was
    typed at onboarding: an observation nobody made is not a reason to erase a
    number somebody did.
    """
    observed = observed_lead_time(tenant_id, supplier_id)
    if observed is None:
        return None
    Supplier.objects.filter(tenant_id=tenant_id, id=supplier_id).update(
        lead_time_days=observed, updated_at=timezone.now()
    )
    return observed


def open_against(order, *, actor=None, received_at=None, supplier_document_number=""):
    """A `draft` receipt with one line per outstanding order line.

    Defaulted to `approved_quantity`, because the common case is the whole order
    arriving and the screen should ask a person to correct what did not.
    """
    if not order_service.receivable(order):
        raise Refused("Solo se recibe contra una orden que ya salió al proveedor.")
    receipt = GoodsReceipt.objects.create(
        tenant_id=order.tenant_id,
        purchase_order=order,
        location_id=order.location_id,
        supplier_id=order.supplier_id,
        type=GoodsReceiptType.RECEIPT,
        status=GoodsReceiptStatus.DRAFT,
        received_at=received_at or timezone.now(),
        received_by=actor if getattr(actor, "id", None) else None,
        received_by_name=getattr(actor, "name", "") or "",
        supplier_document_number=supplier_document_number,
    )
    lines = [
        GoodsReceiptLine(
            tenant_id=order.tenant_id,
            goods_receipt=receipt,
            purchase_order_line=line,
            item_id=line.item_id,
            quantity=max(0, line.approved_quantity - line.received_quantity),
            unit_cost=line.unit_cost,
        )
        for line in PurchaseOrderLine.objects.filter(purchase_order=order)
        if line.approved_quantity - line.received_quantity > 0
    ]
    if lines:
        GoodsReceiptLine.objects.bulk_create(lines, batch_size=500)
    return receipt


def key_for(receipt_id, line_id) -> uuid.UUID:
    """The `client_uuid` one receipt line's move takes. Public so a test can
    assert that a retried confirmation collides rather than appending."""
    return ledger.key_uuid(f"goods_receipt:{receipt_id}:{line_id}")
