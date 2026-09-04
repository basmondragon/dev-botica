"""What a till writes back, and how each of the six collections arrives.

**The registry amendment itself lives in `core/sync/registry.py`** (ledger
rule 9): one declared artefact naming every collection that reaches a device,
amended rather than shadowed. What lives here is the six push writers, because
every one of them encodes a counter rule and S2 owns none of them.

**Two idempotency shapes, and the split is the point.**

Four of the six -- `sale_lines`, `payments`, `sale_returns` and
`sale_return_lines` -- are written once and never again, so the envelope's
`client_uuid` *is* the row's, which is rule 8's first form exactly as
`stock_count_lines` takes it.

Two of them -- `shifts` and `sales` -- receive more than one event about one
row. A turno is opened and later closed; a ticket is opened, closed and
sometimes voided. **So the envelope's key identifies the *event* and the
payload's `client_uuid` identifies the *row*.** Each event is idempotent on its
own envelope key, and all of them converge on the one row under
`UNIQUE (tenant_id, client_uuid)`. That is what "pushes carry events, never row
states" means for a row that legitimately has a state machine: two events about
one sale, each replayable, neither able to move the row backwards.

**Nothing here writes `stock_moves` or `stock_on_hand`.** Every movement is an
append through S3's ledger service under a deterministic natural key (rule 7,
A3), so a batch replayed after a push that timed out is a no-op at the ledger as
surely as it is at the push.

**A line's move is appended as the line lands, not when the header closes.** A
till pushes an open ticket's *header* on the ordinary delta cadence and never
its lines (§5), so a `sale_lines` row only ever reaches the server inside the
batch that closes its ticket -- the line's arrival is the sale happening. Doing
it at the header instead would mean carrying the till's own FEFO observation
from the line's arrival to the header's, which would be a second stamp beside
the ledger's and a second truth to keep in step (§6).
"""

import uuid
from decimal import InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.counter import money, sales as sale_service
from core.fiscal import service as handoff
from core.inventory import ledger
from core.models import (
    Customer,
    Item,
    Lot,
    Payment,
    PaymentMethod,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleSource,
    SaleStatus,
    Shift,
    ShiftStatus,
    SyncConflictType,
    User,
    VatClass,
)
from core.sync import conflicts as conflict_service, push as push_service


# ---------------------------------------------------------------------------
# Coercion. **Every value here is a browser's** -- the same rule S2's customer
# writer and S3's receipt writer follow, and for the same reason: a field that
# arrives as a list reaches `.strip()` as an `AttributeError`, which is not in
# S2's `ROW_FAILURES`, so it would leave the row's savepoint as a 500 and take
# the nine good rows of the batch with it.
# ---------------------------------------------------------------------------


def _text(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _money(value, *, field, allow_none=False):
    if value is None:
        if allow_none:
            return None
        return money.ZERO
    try:
        return money.cents(value)
    except (InvalidOperation, ValueError, TypeError, ArithmeticError) as failure:
        raise push_service.Rejected(
            "Esta fila trae un importe que no es un número.",
            code="amount_invalid",
            field=field,
        ) from failure


def _when(row, payload=None, key=""):
    """A stamp from the till, or the envelope's `occurred_at`.

    Never adjusted and never replaced by the server's clock (§5 rule 4): the box
    left the shelf when the customer took it, not when the link came back, and a
    sale stamped with server time puts an hour of offline trading at one instant.
    """
    if payload is not None and key:
        parsed = _datetime(payload.get(key))
        if parsed is not None:
            return parsed
    return push_service._occurred(row) or timezone.now()


def _datetime(value):
    if not value:
        return None
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def _row_key(payload):
    """The **row's** key for the two collections that receive several events.

    Refused rather than defaulted to the envelope's: the two keys mean different
    things, and a client that sent only one of them would silently create a
    second sale on every close.
    """
    key = _uuid(payload.get("client_uuid"))
    if key is None:
        raise push_service.Rejected(
            "Esta fila no trae la clave de la venta o del turno que describe.",
            code="row_client_uuid_required",
            field="client_uuid",
        )
    return key


def _user(tenant_id, payload, key):
    """The person a row names, resolved inside the tenant.

    Null is admitted: a till whose session expired mid-shift still has to be
    able to push what it already rang, and a row that named nobody is better
    than a batch that could not land.
    """
    user_id = _uuid(payload.get(key))
    if user_id is None:
        return None
    return User.objects.filter(tenant_id=tenant_id, id=user_id).first()


# ---------------------------------------------------------------------------
# `shifts` -- opened, then closed
# ---------------------------------------------------------------------------


def _write_shift(device, collection, row, client_uuid, options):
    """A turno, arriving as an `open` event and later a `closed` one.

    **Opening is a local write and never a network one** -- a till that could
    not open a drawer without the internet is a till that cannot sell -- so this
    is the only path by which a turno reaches the server.

    `variance` is **recomputed here** from the sales this server actually holds,
    not taken from the till: the count is the cashier's and the expectation is
    the record's, and the difference between them is the number the whole table
    exists for. Every sale of the turno sorts before its close event, because
    `client_uuid` is uuid v7, so the arithmetic runs over a complete shift.
    """
    del collection, options
    payload = row.get("payload") or {}
    key = _row_key(payload)
    status = _text(payload.get("status")) or ShiftStatus.OPEN
    if status not in ShiftStatus.values:
        raise push_service.Rejected(
            f"«{status}» no es un estado de turno.",
            code="shift_status_unknown",
            field="status",
        )

    existing = Shift.objects.filter(tenant_id=device.tenant_id, client_uuid=key).first()
    opened = existing is None
    if existing is None:
        # **A close for a turno the server never saw opened is reconstructed,
        # not refused.** Ordering makes it rare -- uuid v7 puts the open event
        # first -- but it is reachable when the open event was itself rejected,
        # and the client removes a rejected row from its outbox. Refusing here
        # would therefore lose the cash count permanently, which is the one
        # number a turno exists to produce. The event carries the drawer's own
        # identity, its float and its count, so what is written is a
        # reconstruction rather than an invention.
        actor = _user(device.tenant_id, payload, "user_id")
        shift = Shift(
            tenant_id=device.tenant_id,
            location_id=device.location_id,
            user=actor,
            user_name=getattr(actor, "name", "") or "",
            opened_at=_when(row, payload, "opened_at"),
            opening_float=_money(payload.get("opening_float"), field="opening_float"),
            status=ShiftStatus.OPEN,
            client_uuid=key,
            device=device,
            occurred_at=_when(row),
            recorded_at=timezone.now(),
        )
        if _uuid(payload.get("id")):
            shift.id = _uuid(payload.get("id"))
        try:
            with transaction.atomic():
                shift.save()
        except IntegrityError as failure:
            winner = Shift.objects.filter(
                tenant_id=device.tenant_id, client_uuid=key
            ).first()
            if winner is not None:
                return push_service.Outcome(
                    client_uuid, push_service.DUPLICATE, id=str(winner.id)
                )
            # **The device already has a turno open.** Not a duplicate and not a
            # server fault: two open drawers on one till is the state the
            # partial unique index exists to refuse, and the cashier has to
            # close the one they left open.
            raise push_service.Rejected(
                "Este equipo ya tiene un turno abierto. Ciérrelo antes de abrir otro.",
                code="shift_already_open",
                field="status",
            ) from failure
        # A reconstruction falls through to the transition below, so a close
        # event that arrived alone opens the drawer and closes it in one step.
        existing = shift

    if status == ShiftStatus.OPEN or existing.status == ShiftStatus.CLOSED:
        return push_service.Outcome(
            client_uuid,
            push_service.APPLIED if opened else push_service.DUPLICATE,
            id=str(existing.id),
        )

    if payload.get("declared_total") is None:
        # **A close with no count is not a close.** Storing a zero here would
        # claim the drawer was counted and found empty, which is the exact thing
        # the forced-close path refuses to do -- and a forced close is the
        # office's own decision, taken through its own endpoint with a reason.
        raise push_service.Rejected(
            "Un cierre de turno lleva el efectivo contado.",
            code="declared_total_required",
            field="declared_total",
        )
    sale_service.close_shift(
        existing,
        declared_total=_money(payload.get("declared_total"), field="declared_total"),
        closed_at=_when(row, payload, "closed_at"),
    )
    return push_service.Outcome(client_uuid, push_service.APPLIED, id=str(existing.id))


# ---------------------------------------------------------------------------
# `sales` -- opened, closed, sometimes voided
# ---------------------------------------------------------------------------


def _resolve_customer(tenant_id, payload):
    """The acquirer, by id and then by the natural key.

    **The natural key is the fallback that makes an offline registration safe.**
    A customer registered at the counter travels in the same batch as the sale
    that needed it and dedupes on `(tenant_id, document_type, document)`; where
    a second till registered the same person during the same blackout the push
    answers `merged` and the server's row keeps a **different** id from the one
    the till chose. The sale would then name an id that does not exist. It names
    the person instead, which is the key both rows converged on (rule 8, second
    paragraph).
    """
    customer_id = _uuid(payload.get("customer_id"))
    if customer_id is not None:
        found = Customer.objects.filter(tenant_id=tenant_id, id=customer_id).first()
        if found is not None:
            return found
    document = _text(payload.get("customer_document")).strip()
    document_type = _text(payload.get("customer_document_type")).strip().upper()
    if document and document_type:
        return Customer.objects.filter(
            tenant_id=tenant_id, document_type=document_type, document=document
        ).first()
    return None


def _write_sale(device, collection, row, client_uuid, options):
    """A ticket, arriving as `open` and later `closed` or `voided`.

    The header carries no money the server keeps: `subtotal`, `discount`, `tax`
    and `total` are **recomputed from the lines** when the ticket closes, because
    a till's totals are a browser's arithmetic. What is taken exactly as sent is
    every line's `unit_price`, which records what was actually charged and which
    no later price list may restate (§5).
    """
    del collection, options
    payload = row.get("payload") or {}
    key = _row_key(payload)
    status = _text(payload.get("status")) or SaleStatus.OPEN
    if status not in SaleStatus.values:
        raise push_service.Rejected(
            f"«{status}» no es un estado de venta.",
            code="sale_status_unknown",
            field="status",
        )

    existing = Sale.objects.filter(tenant_id=device.tenant_id, client_uuid=key).first()
    opened = False
    if existing is None:
        existing = _open_sale(device, row, payload, key)
        if isinstance(existing, push_service.Outcome):
            return existing
        opened = True

    if status == SaleStatus.VOIDED:
        if existing.status == SaleStatus.VOIDED:
            return push_service.Outcome(
                client_uuid, push_service.DUPLICATE, id=str(existing.id)
            )
        sale_service.void(
            existing,
            device=device,
            actor=existing.sold_by_user,
            reason=_text(payload.get("void_reason")),
            at=_when(row, payload, "voided_at"),
        )
        return push_service.Outcome(
            client_uuid, push_service.APPLIED, id=str(existing.id)
        )

    if status == SaleStatus.CLOSED and existing.status == SaleStatus.OPEN:
        # The acquirer and the turno are attached at `Cobrar`, so the close
        # event is the first one that can carry either.
        customer = _resolve_customer(device.tenant_id, payload)
        if customer is not None:
            existing.customer = customer
        # **The turno is only ever re-attributed to one this event names.**
        # `_shift_for` falls back to whatever is open on the device, which is
        # right when a ticket is opened and wrong here: a sale pushed after its
        # own turno closed -- the case acceptance 16 is about -- would be moved
        # into the drawer that is open now, and two cash counts would go wrong
        # at once.
        named = _named_shift(device, payload)
        if named is not None:
            existing.shift = named
        existing.save(update_fields=["customer", "shift", "updated_at"])
        sale_service.close(existing, closed_at=_when(row, payload, "closed_at"))
        return push_service.Outcome(
            client_uuid, push_service.APPLIED, id=str(existing.id)
        )

    # The ticket was opened by this very event, so it applied; anything else at
    # this point is an event the row has already moved past.
    return push_service.Outcome(
        client_uuid,
        push_service.APPLIED if opened else push_service.DUPLICATE,
        id=str(existing.id),
    )


def _land(instance, model, device, client_uuid):
    """Insert one row, and answer a concurrent twin with `duplicate`.

    The replay read at the top of each writer is not a lock, so two requests
    carrying one batch can both find nothing and both try to insert. **The loser
    must see a duplicate, not a rejection** — that is the exact race A5 exists to
    make safe, and a client that saw `rejected` would drop a row the server
    already holds.

    Its own savepoint, because a failed `INSERT` marks the enclosing transaction
    for rollback and Django then refuses the recovery read that finds the winner.
    """
    try:
        with transaction.atomic():
            instance.save()
    except IntegrityError:
        winner = model._default_manager.filter(
            tenant_id=device.tenant_id, client_uuid=client_uuid
        ).first()
        if winner is None:
            raise
        return push_service.Outcome(
            client_uuid, push_service.DUPLICATE, id=str(winner.id)
        )
    return push_service.Outcome(client_uuid, push_service.APPLIED, id=str(instance.id))


def _named_shift(device, payload):
    """The turno this row names, whatever state it is in.

    A closed turno resolves like any other: a sale pushed after its own drawer
    was counted still belongs to that drawer, which is what makes a late arrival
    visible in the office's recomputed expectation rather than silently folded
    into today's.
    """
    shift_id = _uuid(payload.get("shift_id"))
    if shift_id is None:
        return None
    return Shift.objects.filter(
        tenant_id=device.tenant_id,
        id=shift_id,
        location_id=device.location_id,
        # **This till's own drawer and no other's.** Two tills at one sede both
        # hold each other's turnos in their local store, so a client bug naming
        # the wrong one would put one till's takings in the other's cash count
        # and leave both reconciliations wrong. The row falls back to whatever
        # this device has open rather than being refused: a sale is never lost
        # over which drawer it is filed in.
        device_id=device.id,
    ).first()


def _shift_for(device, payload):
    """The turno a row names, or the one open on this device.

    The fallback is for a row that names none -- a ticket opened by a till that
    did not carry the id -- and never for one whose named turno has moved on.
    """
    return _named_shift(device, payload) or sale_service.open_shift_for(device)


def _open_sale(device, row, payload, key):
    """Create the ticket the till opened. Returns the row, or an `Outcome` where
    a concurrent twin beat us to it."""
    number = _text(payload.get("number")).strip().upper()
    if not sale_service.valid_number(number):
        raise push_service.Rejected(
            "El número de esta venta no tiene la forma «CAJA-000», que es la "
            "única que se puede asignar sin conexión.",
            code="number_invalid",
            field="number",
        )
    shift = _shift_for(device, payload)
    if shift is None:
        # **A counter sale outside a turno cannot be reconciled** and the
        # table's own CHECK refuses it. Rejecting the row rather than inventing
        # a shift is what keeps the cash arithmetic answerable.
        raise push_service.Rejected(
            "Esta venta no nombra un turno abierto en este equipo.",
            code="shift_required",
            field="shift_id",
        )
    seller = _user(device.tenant_id, payload, "sold_by_user_id")
    sale = Sale(
        tenant_id=device.tenant_id,
        location_id=device.location_id,
        shift=shift,
        number=number,
        status=SaleStatus.OPEN,
        source=SaleSource.COUNTER,
        customer=_resolve_customer(device.tenant_id, payload),
        sold_by_user=seller,
        sold_by_name=getattr(seller, "name", "") or "",
        client_uuid=key,
        device=device,
        occurred_at=_when(row, payload, "occurred_at"),
        recorded_at=timezone.now(),
    )
    if _uuid(payload.get("id")):
        sale.id = _uuid(payload.get("id"))
    try:
        sale.save()
    except IntegrityError as failure:
        winner = Sale.objects.filter(
            tenant_id=device.tenant_id, client_uuid=key
        ).first()
        if winner is not None:
            return push_service.Outcome(
                str(row.get("client_uuid") or ""),
                push_service.DUPLICATE,
                id=str(winner.id),
            )
        raise push_service.Rejected(
            f"Ya hay otra venta con el número «{number}» en esta sede.",
            code="number_taken",
            field="number",
        ) from failure
    return sale


# ---------------------------------------------------------------------------
# `sale_lines` -- one line, one move, and the two reconciliations
# ---------------------------------------------------------------------------


def _write_sale_line(device, collection, row, client_uuid, options):
    """One line of a ticket that is closing.

    **The sale stands at what was charged.** Neither reconciliation this raises
    corrects anything: a price that moved while the till was offline is reported
    to the office and the line keeps its stamped `unit_price`, and an item
    deactivated while the till was offline is flagged and the line stands,
    because a cashier who sold a box that was on the shelf is right about the
    world and the catalog is late (§5).
    """
    del collection
    payload = row.get("payload") or {}
    replayed = SaleLine.objects.filter(
        tenant_id=device.tenant_id, client_uuid=client_uuid
    ).first()
    if replayed is not None:
        return push_service.Outcome(
            client_uuid, push_service.DUPLICATE, id=str(replayed.id)
        )

    sale = _sale_for(device, payload)
    item = _item_for(device, payload)
    quantity = _int(payload.get("quantity"))
    if quantity is None or quantity <= 0:
        raise push_service.Rejected(
            "Una línea de venta lleva una cantidad en unidades base mayor que cero.",
            code="quantity_invalid",
            field="quantity",
        )
    position = _int(payload.get("position"))
    if position is None or position < 0:
        raise push_service.Rejected(
            "Esta línea no dice en qué renglón del tiquete va.",
            code="position_invalid",
            field="position",
        )
    vat_class = _text(payload.get("vat_class")) or item.vat_class
    if vat_class not in VatClass.values:
        raise push_service.Rejected(
            f"«{vat_class}» no es una clase de IVA reconocida.",
            code="vat_class_unknown",
            field="vat_class",
        )
    lot = _lot_for(device, payload, item)
    unit_price = _money(payload.get("unit_price"), field="unit_price")
    discount = _money(payload.get("discount"), field="discount")
    if discount < money.ZERO or discount > money.line_gross(unit_price, quantity):
        # **A discount larger than the line is not a discount.** Left through,
        # it makes the line's net negative and the IVA contained in it negative
        # too -- and `tax_is_contained_in_the_total` then refuses the whole
        # ticket at the database, at the moment a cashier is holding a
        # customer's money. One malformed line is this row's problem.
        raise push_service.Rejected(
            "El descuento de una línea no puede pasar de lo que vale la línea.",
            code="discount_exceeds_line",
            field="discount",
        )
    unit_cost = _money(payload.get("unit_cost"), field="unit_cost", allow_none=True)
    if unit_cost is None:
        unit_cost = lot.unit_cost if lot is not None else item.service_cost

    line = SaleLine(
        tenant_id=device.tenant_id,
        sale=sale,
        location_id=device.location_id,
        position=position,
        item=item,
        lot=lot,
        quantity=quantity,
        unit_price=unit_price,
        discount=discount,
        vat_class=vat_class,
        # Derived here and never taken from the payload: the tax contained in a
        # line is arithmetic over figures the server already holds, and a
        # browser's answer to it is not evidence of anything (§3).
        tax_amount=money.line_tax(unit_price, quantity, discount, vat_class),
        unit_cost=unit_cost,
        client_uuid=client_uuid,
        device=device,
        occurred_at=_when(row),
        recorded_at=timezone.now(),
    )
    if _uuid(payload.get("id")):
        line.id = _uuid(payload.get("id"))
    try:
        landed = _land(line, SaleLine, device, client_uuid)
    except IntegrityError as failure:
        raise push_service.Rejected(
            "Este tiquete ya tiene una línea en ese renglón.",
            code="position_taken",
            field="position",
        ) from failure
    if landed.outcome == push_service.DUPLICATE:
        return landed

    if item.tracks_lots and lot is None:
        # **The sede holds no lot of this item at all**, so there is nothing for
        # S3's service to move units off -- it refuses a lot-tracked move with
        # no lot, and rightly. The line stands and the office is told: this is
        # the catalog being late about merchandise that was on the shelf, which
        # is the sentence `catalog_divergence` is defined by.
        conflict_service.raise_conflict(
            device=device,
            type=SyncConflictType.CATALOG_DIVERGENCE,
            collection="sale_lines",
            client_uuid=line.client_uuid,
            occurred_at=line.occurred_at,
            detail={
                "reason": "no_lot_at_location",
                "sale_id": line.sale_id,
                "line_id": line.id,
                "item_id": line.item_id,
                "quantity": line.quantity,
                "state": "lot_missing",
                "request_id": _text(payload.get("request_id")),
            },
        )
    else:
        try:
            sale_service.sell_line(
                line,
                item=item,
                device=device,
                actor=sale.sold_by_user,
                fefo_override=_override(payload),
            )
        except ledger.Refused as failure:
            # A malformed move is a defect in the caller rather than a condition
            # at a counter -- the ledger refuses no move on stock grounds -- so
            # it is this row's problem and not the batch's.
            raise push_service.Rejected(
                str(failure), code="move_refused", field="lot_id"
            ) from failure

    sale_service.reconcile_line(line, item=item, device=device)
    sale_service.restate_totals(sale)
    return landed


def _override(payload):
    """The till's own observation of the FEFO deviation, or `None`.

    `None` means the till said nothing and the ledger derives it, which is its
    documented default. A stated value is believed: the counter showed the lot
    queue and watched a cashier pick past its head, and a server applying a sale
    that happened three hours ago would recompute the head against a projection
    that has moved since (§6).
    """
    value = payload.get("fefo_override")
    return None if value is None else bool(value)


def _sale_for(device, payload):
    sale = Sale.objects.filter(
        tenant_id=device.tenant_id,
        id=_uuid(payload.get("sale_id")),
        location_id=device.location_id,
    ).first()
    if sale is None:
        raise push_service.Rejected(
            "Esta línea nombra una venta que el servidor no tiene.",
            code="sale_unknown",
            field="sale_id",
        )
    if sale.status == SaleStatus.VOIDED:
        raise push_service.Rejected(
            "Esta venta está anulada y no admite más líneas.",
            code="sale_voided",
            field="sale_id",
        )
    return sale


def _item_for(device, payload):
    item = Item.objects.filter(
        tenant_id=device.tenant_id, id=_uuid(payload.get("item_id"))
    ).first()
    if item is None:
        raise push_service.Rejected(
            "Esta línea nombra un producto que no existe.",
            code="item_unknown",
            field="item_id",
        )
    return item


def _lot_for(device, payload, item):
    lot_id = _uuid(payload.get("lot_id"))
    if lot_id is None:
        return None
    lot = Lot.objects.filter(tenant_id=device.tenant_id, id=lot_id).first()
    if lot is None:
        raise push_service.Rejected(
            "Esta línea nombra un lote que no existe.",
            code="lot_unknown",
            field="lot_id",
        )
    if lot.item_id != item.id:
        raise push_service.Rejected(
            "El lote nombrado no es de este producto.",
            code="lot_foreign_item",
            field="lot_id",
        )
    return lot


# ---------------------------------------------------------------------------
# `payments`
# ---------------------------------------------------------------------------


def _write_payment(device, collection, row, client_uuid, options):
    """One method applied to one sale.

    **`amount` is what was applied, not what was tendered.** Cash tendered and
    the change given back are display figures on the receipt; the sale was paid
    for with its total, however many notes crossed the counter.
    """
    del collection, options
    payload = row.get("payload") or {}
    replayed = Payment.objects.filter(
        tenant_id=device.tenant_id, client_uuid=client_uuid
    ).first()
    if replayed is not None:
        return push_service.Outcome(
            client_uuid, push_service.DUPLICATE, id=str(replayed.id)
        )

    sale = _sale_for(device, payload)
    method = _text(payload.get("method"))
    if method not in PaymentMethod.values:
        raise push_service.Rejected(
            f"«{method}» no es un medio de pago reconocido.",
            code="method_unknown",
            field="method",
        )
    amount = _money(payload.get("amount"), field="amount")
    if amount <= money.ZERO:
        raise push_service.Rejected(
            "Un pago de cero no es un pago.", code="amount_invalid", field="amount"
        )
    payment = Payment(
        tenant_id=device.tenant_id,
        sale=sale,
        location_id=device.location_id,
        method=method,
        amount=amount,
        reference=_text(payload.get("reference"))[:64],
        client_uuid=client_uuid,
        device=device,
        occurred_at=_when(row),
        recorded_at=timezone.now(),
    )
    if _uuid(payload.get("id")):
        payment.id = _uuid(payload.get("id"))
    return _land(payment, Payment, device, client_uuid)


# ---------------------------------------------------------------------------
# `sale_returns` and `sale_return_lines`
# ---------------------------------------------------------------------------


def _write_sale_return(device, collection, row, client_uuid, options):
    """A devolución's header. Its lines follow it in the same batch."""
    del collection, options
    payload = row.get("payload") or {}
    replayed = SaleReturn.objects.filter(
        tenant_id=device.tenant_id, client_uuid=client_uuid
    ).first()
    if replayed is not None:
        return push_service.Outcome(
            client_uuid, push_service.DUPLICATE, id=str(replayed.id)
        )

    sale = _sale_for(device, payload)
    if sale.status != SaleStatus.CLOSED:
        raise push_service.Rejected(
            "Solo se devuelve contra una venta cerrada.",
            code="sale_not_closed",
            field="sale_id",
        )
    number = _text(payload.get("number")).strip().upper()
    if not sale_service.valid_number(number):
        raise push_service.Rejected(
            "El número de esta devolución no tiene la forma «CAJA-000».",
            code="number_invalid",
            field="number",
        )
    reason = _text(payload.get("reason")).strip()
    if not reason:
        raise push_service.Rejected(
            "Una devolución lleva motivo.", code="reason_required", field="reason"
        )
    method = _text(payload.get("refund_method"))
    if method not in PaymentMethod.values:
        raise push_service.Rejected(
            f"«{method}» no es un medio de reembolso reconocido.",
            code="method_unknown",
            field="refund_method",
        )
    actor = _user(device.tenant_id, payload, "returned_by_user_id")
    sale_return = SaleReturn(
        tenant_id=device.tenant_id,
        sale=sale,
        location_id=device.location_id,
        shift=_shift_for(device, payload),
        number=number,
        reason=reason[:2000],
        refund_method=method,
        returned_by_user=actor,
        returned_by_name=getattr(actor, "name", "") or "",
        client_uuid=client_uuid,
        device=device,
        occurred_at=_when(row),
        recorded_at=timezone.now(),
    )
    if _uuid(payload.get("id")):
        sale_return.id = _uuid(payload.get("id"))
    try:
        landed = _land(sale_return, SaleReturn, device, client_uuid)
    except IntegrityError as failure:
        raise push_service.Rejected(
            f"Ya hay otra devolución con el número «{number}» en esta sede.",
            code="number_taken",
            field="number",
        ) from failure
    if landed.outcome == push_service.APPLIED:
        # **S5's second attach point.** The credit-note row is written here, in
        # the header's own pinned transaction, and its payload is built when it
        # is about to be sent -- by which time the lines that arrive after this
        # row in the same batch have all landed. Building it here instead would
        # produce a credit note with no lines (S5, *the sale handoff service*).
        handoff.hand_off_return(sale_return)
    return landed


def _write_sale_return_line(device, collection, row, client_uuid, options):
    """One returned line: the stock back on its **original** lot, and the money
    stamped from the **original** line rather than from today's price list."""
    del collection, options
    payload = row.get("payload") or {}
    replayed = SaleReturnLine.objects.filter(
        tenant_id=device.tenant_id, client_uuid=client_uuid
    ).first()
    if replayed is not None:
        return push_service.Outcome(
            client_uuid, push_service.DUPLICATE, id=str(replayed.id)
        )

    sale_return = SaleReturn.objects.filter(
        tenant_id=device.tenant_id,
        id=_uuid(payload.get("sale_return_id")),
        location_id=device.location_id,
    ).first()
    if sale_return is None:
        raise push_service.Rejected(
            "Esta línea nombra una devolución que el servidor no tiene.",
            code="return_unknown",
            field="sale_return_id",
        )
    line = sale_service.line_by_id(sale_return.sale, _uuid(payload.get("sale_line_id")))
    if line is None:
        raise push_service.Rejected(
            "Esta línea de devolución no corresponde a ninguna línea de la venta.",
            code="sale_line_unknown",
            field="sale_line_id",
        )
    quantity = _int(payload.get("quantity"))
    remaining = sale_service.returnable(sale_return.sale).get(line.id, 0)
    if quantity is None or quantity <= 0:
        raise push_service.Rejected(
            "Una devolución devuelve al menos una unidad.",
            code="quantity_invalid",
            field="quantity",
        )
    if quantity > remaining:
        raise push_service.Rejected(
            f"De esa línea quedan {remaining} unidades por devolver.",
            code="quantity_exceeds_remaining",
            field="quantity",
        )

    returned = SaleReturnLine(
        tenant_id=device.tenant_id,
        sale_return=sale_return,
        sale_line=line,
        location_id=device.location_id,
        quantity=quantity,
        client_uuid=client_uuid,
        device=device,
        occurred_at=_when(row),
        recorded_at=timezone.now(),
    )
    sale_service.stamp_return_line(returned, line)
    if _uuid(payload.get("id")):
        returned.id = _uuid(payload.get("id"))
    try:
        landed = _land(returned, SaleReturnLine, device, client_uuid)
    except IntegrityError as failure:
        raise push_service.Rejected(
            "Esa línea ya fue devuelta en esta devolución.",
            code="line_already_returned",
            field="sale_line_id",
        ) from failure
    if landed.outcome == push_service.DUPLICATE:
        return landed

    item = Item.objects.filter(tenant_id=device.tenant_id, id=returned.item_id).first()
    if item is not None:
        sale_service.return_line_stock(
            returned,
            item=item,
            device=device,
            actor=sale_return.returned_by_user,
        )
    sale_service.restate_return_totals(sale_return)
    return push_service.Outcome(client_uuid, push_service.APPLIED, id=str(returned.id))


def register():
    """Hand S2's push endpoint the six writers this stage owns."""
    push_service.register_writer("shifts", _write_shift)
    push_service.register_writer("sales", _write_sale)
    push_service.register_writer("sale_lines", _write_sale_line)
    push_service.register_writer("payments", _write_payment)
    push_service.register_writer("sale_returns", _write_sale_return)
    push_service.register_writer("sale_return_lines", _write_sale_return_line)
