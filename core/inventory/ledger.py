"""The stock ledger service (A3, ownership.md rule 7, cross-stage services).

**The one code path in the product that can change a quantity.** S4, S6 and
every later stage calls `append` and never writes `stock_moves` or
`stock_on_hand` itself; the database enforces the first half of that, because
migration 0010 revokes UPDATE and DELETE on `stock_moves` from the runtime role.

What `append` guarantees, in one transaction:

  * one `stock_moves` row per requested move, and the projection moved by
    exactly the sum of those rows -- killing the process between the two leaves
    neither;
  * **nothing at all** for an item whose `tracks_stock` is false (A7);
  * the sign of each type, checked here and again by the table's own CHECK
    constraint; and the lot rule per `items.tracks_lots`, which is checked here
    **only** -- a row constraint cannot read the `items` row it would have to
    consult, which is the same reason S1's category depth is a trigger;
  * both clocks, the device, the user and the client key on every row (§5 rule 4);
  * deduplication on `(tenant_id, client_uuid)` and nothing else (A5) -- so a
    replayed push, a double-clicked button and a re-run seed are all no-ops;
  * a `negative_stock` row in S2's `sync_conflicts` where the projection crosses
    below zero.

**It refuses no move on stock grounds.** A sale is a physical fact that already
happened -- the customer is holding the box -- so stock going negative is an
exception raised to the office and never a refusal at a counter (§5 rule 2).
Where a refusal *is* correct, it is a policy at the endpoint: the transfer
dispatch reads `negative_stock_block_outbound` before it calls this module.
Putting that check inside here would make the offline sale path depend on a rule
that must not apply to it.

**The advisory lock is the correctness argument for the rebuild.** Every append
takes a per-`(tenant, location)` transaction-scoped lock before it touches the
projection, and so does `rebuild`. That is what makes a rebuild and a sale
serialise rather than interleave into a lost update, and it is why the rebuild
runs per location: a 20-sede network is 20 short locks and not one long one.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date

from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    NEGATIVE_MOVE_TYPES,
    POSITIVE_MOVE_TYPES,
    REASONED_MOVE_TYPES,
    STOCK_MOVE_REASONS,
    Item,
    Lot,
    StockMove,
    StockPolicy,
    StockMoveType,
    StockOnHand,
    SyncConflict,
    SyncConflictStatus,
    SyncConflictType,
)
from core.sync import conflicts as conflict_service

#: The namespace a server-originated move derives its `client_uuid` from. A till
#: sends a uuid v7 of its own; a dispatch, a receipt, a count close and the demo
#: seed have no client, so their key is a v5 over the document that caused them.
#: **That is what makes pressing a resolution twice append nothing** -- the
#: second attempt collides with `one_move_per_client_uuid` and is answered as a
#: duplicate, exactly as a replayed push is.
NAMESPACE = uuid.UUID("9d4f1a7e-3c62-5b18-9f05-2a7de4c1b830")

#: The namespace an open negative-stock conflict derives its detail key from.
CONFLICT_NAMESPACE = uuid.UUID("41d2c9b7-8e05-5a63-bb14-7f2c0d95a6e8")


class Refused(ValueError):
    """A move this service will not write, named in Spanish for the operator.

    **Never a stock level.** Every refusal here is a malformed move -- a lot
    missing on a lot-tracked item, a positive `shrinkage`, a reason on a
    `transfer_in` -- and each of them is a defect in the caller rather than a
    condition at a counter.

    `field` names the control the operator has to change, where there is one.
    A refusal about a lot code is not a page-level failure -- it is one line of
    an entry and one box on it (§B.10.3), and a caller that knows which line it
    was holding is what turns the two into a field-scope error.
    """

    def __init__(self, message, *, field=""):
        super().__init__(message)
        self.field = field


def key_uuid(*parts) -> uuid.UUID:
    """A deterministic `client_uuid` from a document's own natural key."""
    return uuid.uuid5(NAMESPACE, ":".join(str(one) for one in parts))


@dataclass
class Move:
    """One requested movement. Every field the ledger stamps is optional here
    and is filled in from the append's own context."""

    location_id: uuid.UUID
    item_id: uuid.UUID
    quantity: int
    type: str
    lot_id: uuid.UUID | None = None
    document_type: str = ""
    document_id: uuid.UUID | None = None
    unit_cost: object = None
    occurred_at: object = None
    #: **The server's own clock, and only a fabricated history may state it.**
    #: `append` stamps `recorded_at` for every ordinary caller and that is the
    #: column the lot trace and the record panel order by -- a request that set
    #: it would be a request choosing where it lands in a legal record. It is
    #: settable for exactly one reason: a history that did not happen while the
    #: process was running (the demo seed, and a one-off import behind it) has
    #: no other way to arrive in order, and a whole batch sharing one instant
    #: leaves `(recorded_at, id)` to sort a shelf's life by uuid.
    recorded_at: object = None
    reason: str = ""
    note: str = ""
    #: Left unstated, **the ledger derives it** (§6, acceptance 11): a consuming
    #: move naming a lot other than the one `fefo_head` offered is an override.
    #: A caller that already knows -- S4's counter, which showed the queue and
    #: watched a cashier pick past its head -- states it and is believed.
    fefo_override: bool | None = None
    #: A till's uuid v7. Absent means the server originated the move and the key
    #: below is what its `client_uuid` is derived from.
    client_uuid: uuid.UUID | None = None
    #: The natural key of the document line this move is. Required when
    #: `client_uuid` is absent, because a server-originated move with no key
    #: could not be idempotent and a double-clicked button would move stock
    #: twice.
    key: str = ""
    #: The row id, for the demo seed: every seeded id is derived from a natural
    #: key so a rebuilt seed keeps the ids it had.
    id: uuid.UUID | None = None


@dataclass
class Result:
    """What one append did. `written` and `duplicates` together account for
    every move handed in that was not skipped."""

    written: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    #: Moves dropped because their item does not track stock (A7). Not an error:
    #: a service on a receipt line, a count line or a transfer line writes
    #: nothing and is not a failure of the document.
    skipped: list = field(default_factory=list)
    #: `(location_id, item_id, lot_id) -> quantity after this append`, for every
    #: key the append touched.
    quantities: dict = field(default_factory=dict)
    #: The keys this append left below zero.
    negative: list = field(default_factory=list)

    @property
    def moved(self) -> int:
        return sum(move.quantity for move in self.written)


# ---------------------------------------------------------------------------
# The append
# ---------------------------------------------------------------------------


def append(
    moves,
    *,
    tenant_id,
    actor=None,
    device=None,
    request_id="",
    recorded_at=None,
) -> Result:
    """Append moves and maintain the projection, in one transaction.

    The caller is already inside the pinned transaction -- a request, a job, a
    push batch or a management command -- and this opens a savepoint rather than
    a second connection, so a failure here rolls the moves and the projection
    back together and leaves neither.
    """
    result = Result()
    requested = list(moves)
    if not requested:
        return result

    stamped = recorded_at or timezone.now()
    items = _items(tenant_id, {move.item_id for move in requested})
    lots = _lots(tenant_id, {move.lot_id for move in requested if move.lot_id})

    prepared = []
    for move in requested:
        item = items.get(move.item_id)
        if item is None:
            raise Refused("Este movimiento nombra un producto que no existe.")
        # A7 · **an item that does not track stock writes nothing at all.** Not
        # a zero-quantity move, not a projection row: a service has no stock and
        # a row saying it has none would be a row somebody later sums.
        if not item.tracks_stock:
            result.skipped.append(move)
            continue
        _check(move, item, lots)
        prepared.append(move)

    if not prepared:
        return result

    today = timezone.localdate()
    heads: dict[tuple, object] = {}
    keyed = [(move, _client_uuid(move)) for move in prepared]
    held = {
        row.client_uuid: row
        for row in StockMove.objects.filter(
            tenant_id=tenant_id, client_uuid__in=[key for _, key in keyed]
        )
    }

    fresh = []
    for move, client_uuid in keyed:
        existing = held.get(client_uuid)
        if existing is not None:
            # A5 · a duplicate is a success. A push that timed out after the
            # server committed is retried and is a no-op, which is the failure
            # this whole shape exists to make safe.
            result.duplicates.append(existing)
            continue
        fresh.append(
            StockMove(
                id=move.id or uuid.uuid4(),
                tenant_id=tenant_id,
                location_id=move.location_id,
                item_id=move.item_id,
                lot_id=move.lot_id,
                quantity=int(move.quantity),
                type=move.type,
                document_type=move.document_type or "",
                document_id=move.document_id,
                unit_cost=move.unit_cost,
                occurred_at=move.occurred_at or stamped,
                recorded_at=move.recorded_at or stamped,
                device=device,
                user_id=getattr(actor, "id", None),
                user_name=getattr(actor, "name", "") or "",
                client_uuid=client_uuid,
                reason=move.reason or "",
                note=move.note or "",
                fefo_override=(
                    bool(move.fefo_override)
                    if move.fefo_override is not None
                    else _override(tenant_id, move, lots, heads, today)
                ),
            )
        )

    if not fresh:
        return result

    # Sorted, so two appends touching the same two sedes take the two locks in
    # the same order and cannot deadlock against each other.
    for location_id in sorted({str(row.location_id) for row in fresh}):
        lock_location(tenant_id, location_id)

    with transaction.atomic():
        fresh = _insert(tenant_id, fresh, result)
        if not fresh:
            return result
        result.written.extend(fresh)
        deltas: dict[tuple, int] = {}
        for row in fresh:
            key = (row.location_id, row.item_id, row.lot_id)
            deltas[key] = deltas.get(key, 0) + row.quantity
        result.quantities = _apply(tenant_id, deltas)
        _touch_lots(tenant_id, fresh, result.quantities)
        _touch_elsewhere(tenant_id, deltas, result.quantities)

    for key, quantity in result.quantities.items():
        if quantity < 0:
            result.negative.append(key)
            _raise_negative(
                tenant_id=tenant_id,
                key=key,
                quantity=quantity,
                moves=[
                    row
                    for row in fresh
                    if (row.location_id, row.item_id, row.lot_id) == key
                ],
                device=device,
                request_id=request_id,
            )
    return result


def _insert(tenant_id, fresh, result):
    """Insert the batch, and answer a concurrent twin with `duplicate`.

    The read above is not a lock, so two requests replaying one push can both
    find nothing and both try to insert. **The loser must see a duplicate, not a
    500** -- that is the exact race A5 exists to make safe, and a batch insert
    that raised would take nine good rows down with the tenth.

    The retry is per row and inside its own savepoint, because a failed
    statement poisons the enclosing transaction and Django then refuses the
    recovery read that finds the winner.
    """
    try:
        with transaction.atomic():
            StockMove.objects.bulk_create(fresh, batch_size=500)
        return fresh
    except IntegrityError:
        pass

    landed = []
    for row in fresh:
        try:
            with transaction.atomic():
                row.save(force_insert=True)
            landed.append(row)
        except IntegrityError:
            winner = StockMove.objects.filter(
                tenant_id=tenant_id, client_uuid=row.client_uuid
            ).first()
            if winner is None:
                raise
            result.duplicates.append(winner)
    return landed


def _touch_lots(tenant_id, fresh, quantities):
    """Move `lots.updated_at` when a lot first reaches a sede.

    **A registry row that enters a collection without being written can never be
    served.** `lots` is scoped by a join through `stock_on_hand`, so a lot that
    already existed and whose stock arrives at this sede on a transfer enters
    the till's predicate with an `updated_at` from months ago -- behind the
    device's cursor, never in a delta page, and the till holds a quantity whose
    expiry date it does not have until the daily digest resets the collection.

    S1 met the same problem from the other side and answered it the same way:
    migration 0008's trigger moves `item_barcodes.updated_at` when the item's
    `active` flag changes. This is that, for arrivals, in the one module that
    writes the projection.

    Only a **new** key is touched. Every later movement on a lot the sede
    already holds changes nothing about the predicate, and re-stamping on every
    sale would push the whole lot table past every till's cursor on every
    ticket.
    """
    arrived = {
        row.lot_id
        for row in fresh
        if row.lot_id is not None
        and quantities.get((row.location_id, row.item_id, row.lot_id)) == row.quantity
    }
    if arrived:
        Lot.objects.filter(tenant_id=tenant_id, id__in=arrived).update(
            updated_at=timezone.now()
        )


def _touch_elsewhere(tenant_id, deltas, quantities):
    """Move the **other** sedes' rows when this one crosses into trouble.

    `_touch_lots` closes this hole for the till's own sede. This closes it for
    the other-location set, which has the same shape and a worse cause: that
    set's membership is derived from *this* sede's shortage
    (`core.inventory.sync.troubled_items`), so a reference enters it when a sale
    here drops below the reorder point -- and the rows that must now reach the
    till are rows at sedes nobody wrote. Their `updated_at` is from whenever
    that shelf last moved, which is behind the device's cursor, so a delta pull
    never serves them and the till holds a `Quiebre` badge with no
    `hay 96 en Suba` clause behind it until the daily digest resets the
    collection.

    Only a **crossing** touches anything. Every later sale on a reference the
    sede is already short of changes no predicate, and re-stamping on each one
    would push the other sedes' stock past every till's cursor on every ticket.

    The write is bounded by the sedes in the network, not by the catalog: one
    crossing at a twenty-sede tenant touches at most nineteen rows and their
    lots.
    """
    falling = [key for key, delta in deltas.items() if delta < 0]
    if not falling:
        return
    crossed = _crossed_into_trouble(tenant_id, falling, deltas, quantities)
    if not crossed:
        return
    now = timezone.now()
    rows = StockOnHand.objects.filter(tenant_id=tenant_id).filter(_elsewhere_q(crossed))
    lot_ids = {one for one in rows.values_list("lot_id", flat=True) if one}
    rows.update(updated_at=now)
    if lot_ids:
        Lot.objects.filter(tenant_id=tenant_id, id__in=lot_ids).update(updated_at=now)


def _elsewhere_q(crossed):
    query = Q(pk__in=[])
    for location_id, item_id in crossed:
        query |= Q(item_id=item_id) & ~Q(location_id=location_id)
    return query


def _crossed_into_trouble(tenant_id, falling, deltas, quantities):
    """The `(location, item)` pairs this append put into trouble that were not.

    Trouble is `core.inventory.sync.TROUBLE`'s own predicate -- at or below the
    reorder point, or at or below zero -- resolved against the same policy
    precedence the rest of the stage uses: **a sede's own row wins over the
    network-wide one, whole.** Two answers to one question is how a till and a
    screen come to disagree about which references are short.
    """
    items = {key[1] for key in falling}
    locations = {key[0] for key in falling}
    thresholds: dict[tuple, int | None] = {}
    for item_id, location_id, reorder in StockPolicy.objects.filter(
        tenant_id=tenant_id, item_id__in=items
    ).values_list("item_id", "location_id", "reorder_point"):
        if location_id is None or location_id in locations:
            thresholds[(location_id, item_id)] = reorder

    crossed = set()
    for key in falling:
        location_id, item_id, _lot = key
        after = quantities.get(key)
        if after is None:
            continue
        reorder = thresholds.get(
            (location_id, item_id), thresholds.get((None, item_id))
        )

        def trouble(quantity, reorder=reorder):
            return quantity <= 0 or (reorder is not None and quantity <= reorder)

        if trouble(after) and not trouble(after - deltas[key]):
            crossed.add((location_id, item_id))
    return crossed


def lock_location(tenant_id, location_id):
    """The per-`(tenant, location)` transaction-scoped advisory lock.

    Taken by every append and by the rebuild, which is the whole of the
    serialisation argument: a rebuild and a sale at the same sede cannot
    interleave into a lost update, and two sedes never wait on each other.
    """
    token = hashlib.blake2b(
        f"stock:{tenant_id}:{location_id}".encode("utf-8"), digest_size=8
    ).digest()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [int.from_bytes(token, "big", signed=True)],
        )


def _items(tenant_id, item_ids):
    return {
        item.id: item
        for item in Item.objects.filter(tenant_id=tenant_id, id__in=list(item_ids))
    }


def _lots(tenant_id, lot_ids):
    if not lot_ids:
        return {}
    return {
        lot.id: lot
        for lot in Lot.objects.filter(tenant_id=tenant_id, id__in=list(lot_ids))
    }


def _check(move, item, lots):
    """The rules a move must satisfy before it reaches the table's own CHECKs."""
    if int(move.quantity) == 0:
        raise Refused("Un movimiento de cero unidades no es un movimiento.")
    if move.type not in StockMoveType.values:
        raise Refused(f"«{move.type}» no es un tipo de movimiento.")
    if move.type in POSITIVE_MOVE_TYPES and int(move.quantity) < 0:
        raise Refused(
            f"Un movimiento de tipo «{move.type}» suma unidades y este resta."
        )
    if move.type in NEGATIVE_MOVE_TYPES and int(move.quantity) > 0:
        raise Refused(
            f"Un movimiento de tipo «{move.type}» resta unidades y este suma."
        )
    if item.tracks_lots and move.lot_id is None:
        raise Refused(
            f"«{item.name}» se maneja por lote, así que un movimiento suyo "
            "necesita uno."
        )
    if not item.tracks_lots and move.lot_id is not None:
        raise Refused(f"«{item.name}» no se maneja por lote.")
    if move.lot_id is not None:
        lot = lots.get(move.lot_id)
        if lot is None:
            raise Refused("Este movimiento nombra un lote que no existe.")
        if lot.item_id != item.id:
            raise Refused("El lote nombrado no es de este producto.")
    reasoned = move.type in REASONED_MOVE_TYPES
    if reasoned and not move.reason:
        raise Refused("Un ajuste, una merma, un vencimiento o un conteo lleva motivo.")
    if not reasoned and move.reason:
        raise Refused(f"Un movimiento de tipo «{move.type}» no lleva motivo.")
    if move.reason and move.reason not in STOCK_MOVE_REASONS:
        raise Refused(f"«{move.reason}» no es un motivo reconocido.")


def _client_uuid(move) -> uuid.UUID:
    if move.client_uuid is not None:
        return (
            move.client_uuid
            if isinstance(move.client_uuid, uuid.UUID)
            else uuid.UUID(str(move.client_uuid))
        )
    if not move.key:
        raise Refused(
            "Un movimiento originado en el servidor necesita una clave "
            "natural: sin ella no puede ser idempotente y un botón pulsado dos "
            "veces movería existencias dos veces."
        )
    return key_uuid(move.key)


# ---------------------------------------------------------------------------
# The projection. Written here and nowhere else (rule 7).
# ---------------------------------------------------------------------------

_UPSERT = """
INSERT INTO stock_on_hand
    (id, tenant_id, location_id, item_id, lot_id, quantity, created_at, updated_at)
VALUES {values}
ON CONFLICT (tenant_id, location_id, item_id, lot_id)
DO UPDATE SET quantity = {assignment},
              updated_at = clock_timestamp()
RETURNING location_id, item_id, lot_id, quantity
"""

#: The two ways the projection ever moves, and there is no third. An append adds
#: its own delta; a rebuild states the ledger's own sum. `ON CONFLICT` infers
#: `one_projection_row_per_key`, which is `NULLS NOT DISTINCT` -- so a lot-less
#: item collides with its own row instead of accumulating one per write.
_ADD = "stock_on_hand.quantity + EXCLUDED.quantity"
_SET = "EXCLUDED.quantity"


def _apply(tenant_id, deltas, *, absolute=False):
    """Move the projection by a delta per key, or set it outright on a rebuild.

    One statement, however many keys: `stock_on_hand` is the table every grid
    page reads and a per-key round trip would make a forty-line receipt forty
    round trips.

    `clock_timestamp()` and not `now()`, for the reason migration 0008 gives on
    the other side of the same problem: `now()` is the transaction's start time,
    so a seed writing ten thousand moves in one transaction would stamp every
    projection row behind the cursor of a device that pulled while it ran.
    """
    if not deltas:
        return {}
    rows = []
    params: list = []
    for (location_id, item_id, lot_id), quantity in deltas.items():
        rows.append("(%s, %s, %s, %s, %s, %s, clock_timestamp(), clock_timestamp())")
        params.extend(
            [
                str(uuid.uuid4()),
                str(tenant_id),
                str(location_id),
                str(item_id),
                str(lot_id) if lot_id else None,
                int(quantity),
            ]
        )
    statement = _UPSERT.format(
        values=", ".join(rows), assignment=_SET if absolute else _ADD
    )
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return {
            (uuid.UUID(str(row[0])), uuid.UUID(str(row[1])), _maybe(row[2])): row[3]
            for row in cursor.fetchall()
        }


def _maybe(value):
    return uuid.UUID(str(value)) if value else None


# ---------------------------------------------------------------------------
# Negative stock: an exception raised to the office, never a refusal (§5 rule 2)
# ---------------------------------------------------------------------------


def _detail_key(location_id, item_id, lot_id):
    return str(uuid.uuid5(CONFLICT_NAMESPACE, f"{location_id}:{item_id}:{lot_id}"))


def _raise_negative(*, tenant_id, key, quantity, moves, device, request_id):
    """One open row per `(location, item, lot)`, refreshed rather than repeated.

    A third consuming move updates the standing row; it does not open a second,
    because a queue with one line per oversold unit is a queue nobody reads.

    **A row an administrator already resolved is not reopened** -- it is
    replaced by a new one. `raise_conflict` keeps a closed row closed on
    purpose, and that is right for a daily re-run of the same check; here the
    stock genuinely went negative again after somebody fixed it, which is a new
    fact and deserves its own line.
    """
    location_id, item_id, lot_id = key
    marker = _detail_key(location_id, item_id, lot_id)
    standing = SyncConflict.objects.filter(
        tenant_id=tenant_id,
        location_id=location_id,
        type=SyncConflictType.NEGATIVE_STOCK,
        status=SyncConflictStatus.OPEN,
        detail__key=marker,
    ).first()

    documents = _documents(standing, _crossers(tenant_id, key, moves, quantity))
    conflict_service.raise_conflict(
        device=device,
        tenant_id=tenant_id,
        location_id=location_id,
        type=SyncConflictType.NEGATIVE_STOCK,
        collection="stock_moves",
        occurred_at=moves[0].occurred_at if moves else None,
        detail={
            "reason": "negative_stock",
            "key": marker,
            "item_id": str(item_id),
            "lot_id": str(lot_id) if lot_id else None,
            "quantity": int(quantity),
            "documents": documents,
            "request_id": request_id,
        },
        row_id=standing.id if standing else None,
    )


#: How many causing documents a conflict row names. The office needs to know
#: which two sales crossed zero, not all forty that followed -- and `detail` is
#: bounded at 200 characters by `scrub`, which is two uuid pairs and a bit.
DOCUMENT_CAP = 4


#: How far back the walk below will look for the move that emptied the shelf.
#: Two offline tills produce two moves; a batch replayed by a flapping link
#: produces a handful. A key that has been at or below zero for two hundred
#: movements is a sede nobody has counted in months, and naming its whole
#: history would not tell the office anything the quantity does not.
CROSSING_LOOKBACK = 200


def _crossers(tenant_id, key, moves, quantity):
    """The moves that took this key below zero, newest first, walked backwards
    until the balance was last above zero.

    **Acceptance 5 names both sales, and they need not arrive together.** Two
    tills offline at one sede each sell the last box: the first push takes the
    shelf from one to zero and raises nothing, the second takes it to −1 and is
    the only move that append can see. A row naming one sale sends the office
    looking for a discrepancy with half the evidence.

    So the walk starts from this append's own moves and continues into the
    ledger, subtracting each as it goes, and stops at the first move whose
    *before* balance was positive -- that move is the one that emptied the
    shelf, and everything after it is what oversold it.
    """
    location_id, item_id, lot_id = key
    seen = {row.id for row in moves}
    earlier = (
        StockMove.objects.filter(
            tenant_id=tenant_id,
            location_id=location_id,
            item_id=item_id,
            lot_id=lot_id,
        )
        .exclude(id__in=seen)
        .order_by("-recorded_at", "-id")[:CROSSING_LOOKBACK]
    )
    walked, running = [], int(quantity)
    for row in [*reversed(list(moves)), *earlier]:
        walked.append(row)
        before = running - int(row.quantity)
        if before > 0:
            break
        running = before
    return walked[::-1]


def _documents(standing, moves):
    """The documents that crossed zero, oldest first, capped.

    A move with no document is its own document, so it names its own id -- which
    is what makes an oversell caused by a hand-written adjustment as traceable
    as one caused by two sales.
    """
    held = (standing.detail.get("documents") or "").split(",") if standing else []
    for row in moves:
        name = f"{row.document_type or row.type}:{row.document_id or row.id}"
        if name not in held:
            held.append(name)
    return ",".join([one for one in held if one][:DOCUMENT_CAP])


def resolve_negative(*, tenant_id, keys, actor, note=""):
    """Close the open negative-stock rows a count covers, in its transaction.

    §5 fixes the counting screen as where an oversell is resolved, and criterion
    7 fixes that the adjusting move and the resolution land together: a count
    that wrote the move and left the exception open would leave the office
    chasing a discrepancy somebody already fixed.
    """
    markers = [_detail_key(*key) for key in keys]
    if not markers:
        return 0
    return SyncConflict.objects.filter(
        tenant_id=tenant_id,
        type=SyncConflictType.NEGATIVE_STOCK,
        status=SyncConflictStatus.OPEN,
        detail__key__in=markers,
    ).update(
        status=SyncConflictStatus.RESOLVED,
        resolved_by_user=actor if getattr(actor, "id", None) else None,
        resolved_at=timezone.now(),
        resolution_note=note
        or "Resuelto al cerrar un conteo que cubre este producto y lote.",
        updated_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# FEFO, and the override that outranks it (§6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Available:
    """One lot on one shelf, or -- where the item does not track lots -- the
    shelf itself, whose `lot_id` is then null."""

    lot_id: uuid.UUID | None
    lot_code: str
    expires_at: date | None
    quantity: int
    unit_cost: object


def available_lots(
    *, tenant_id, location_id, item_id, include_expired=False, today=None
) -> list[Available]:
    """The lots this sede holds of this item, first expired first out.

    **Expired lots are excluded unless asked for**, because the head of a
    cashier's queue is where a lot past its date must never be. A lot with
    nothing on the shelf is excluded too: it is not available, and offering it
    would put a zero at the head of the queue.

    `expires_at IS NULL` sorts last: an item that does not track expiry has no
    place in an ordering by expiry, and putting it first would consume the
    untracked stock before the dated stock every time.
    """
    today = today or timezone.localdate()
    rows = (
        StockOnHand.objects.filter(
            tenant_id=tenant_id,
            location_id=location_id,
            item_id=item_id,
            quantity__gt=0,
        )
        .select_related("lot")
        .order_by("lot__expires_at", "lot__lot_code")
    )
    answer = []
    for row in rows:
        if row.lot is None:
            answer.append(Available(None, "", None, row.quantity, None))
            continue
        if (
            not include_expired
            and row.lot.expires_at is not None
            and row.lot.expires_at < today
        ):
            continue
        answer.append(
            Available(
                row.lot_id,
                row.lot.lot_code,
                row.lot.expires_at,
                row.quantity,
                row.lot.unit_cost,
            )
        )
    # `order_by` puts NULL expiries last on Postgres for an ascending sort, which
    # is the ordering this wants; it is restated here so the guarantee does not
    # depend on a database default a reader has to remember.
    answer.sort(key=lambda one: (one.expires_at is None, one.expires_at or today))
    return answer


def fefo_head(*, tenant_id, location_id, item_id, today=None):
    """The lot a consuming move takes by default, or None where none is free."""
    lots = available_lots(
        tenant_id=tenant_id, location_id=location_id, item_id=item_id, today=today
    )
    return lots[0] if lots else None


def _override(tenant_id, move, lots, heads, today) -> bool:
    """Whether this move takes a lot other than the one FEFO offered.

    **Stamped here rather than at each caller** (§6, acceptance 11). Every
    consuming path in the product -- a merma, a vencimiento, a negative
    ajuste, a dispatch, a count close, and S4's sale -- goes through this
    module, so deriving it once is what makes the column true everywhere
    instead of true wherever somebody remembered.

    Two moves are deliberately *not* overrides. An addition never chose a lot
    against a queue; and **a write-off of a lot already past its date is not a
    disagreement with the queue, because the queue never offered that lot** --
    `available_lots` skips expired stock precisely so it stays off a cashier's
    head, and stamping `vencimiento` as an override would mark the one
    operation that is unambiguously right.

    The head is read once per `(location, item)` per append, and read **before**
    `_apply` moves the projection -- which is the whole reason the column is
    stamped rather than derived later.
    """
    if move.quantity >= 0 or move.lot_id is None:
        return False
    lot = lots.get(move.lot_id)
    if lot is not None and lot.expires_at is not None and lot.expires_at < today:
        return False
    key = (move.location_id, move.item_id)
    if key not in heads:
        heads[key] = fefo_head(
            tenant_id=tenant_id,
            location_id=move.location_id,
            item_id=move.item_id,
            today=today,
        )
    head = heads[key]
    return head is not None and head.lot_id != move.lot_id


def is_override(*, tenant_id, location_id, item_id, lot_id, today=None) -> bool:
    """Whether choosing this lot disagrees with FEFO.

    **Stamped rather than derived at read time**: once the projection moves on,
    whether the chosen lot was the head is unrecoverable, and the disagreement
    between the algorithm and the box in a person's hand is the thing worth
    keeping (§6).
    """
    head = fefo_head(
        tenant_id=tenant_id, location_id=location_id, item_id=item_id, today=today
    )
    if head is None:
        return False
    return head.lot_id != lot_id


# ---------------------------------------------------------------------------
# The projection is disposable, and these two are the proof (deliverable 15)
# ---------------------------------------------------------------------------


def ledger_totals(tenant_id, location_id) -> dict:
    """`SUM(quantity) GROUP BY item_id, lot_id` over the ledger for one sede.

    Keys summing to zero are kept, not dropped: a lot that was received and
    entirely sold is a real key with a real quantity of zero, it is what the
    `Quiebre` badge renders from, and a rebuild that dropped it would change
    what the screen says.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT item_id, lot_id, sum(quantity)::int FROM stock_moves "
            "WHERE tenant_id = %s AND location_id = %s "
            "GROUP BY item_id, lot_id",
            [str(tenant_id), str(location_id)],
        )
        return {
            (uuid.UUID(str(row[0])), _maybe(row[1])): row[2]
            for row in cursor.fetchall()
        }


def projection_totals(tenant_id, location_id) -> dict:
    return {
        (row.item_id, row.lot_id): row.quantity
        for row in StockOnHand.objects.filter(
            tenant_id=tenant_id, location_id=location_id
        )
    }


def verify(tenant_id, location_id) -> dict:
    """Recompute and compare, **writing nothing**.

    Drift is never expected: a non-zero result means a code path wrote outside
    this module, which is a defect (rule 7). A job that silently repaired the
    projection would also silently hide the code path that broke it.

    **It takes the sede's lock even though it writes nothing.** The two sums are
    two statements, and under READ COMMITTED an append landing between them is
    seen by one and not the other -- which reports drift that never existed. A
    nightly job that cries wolf is a job whose output stops being read, and the
    whole value of this one is that a non-zero result means something.
    """
    lock_location(tenant_id, location_id)
    ledger = ledger_totals(tenant_id, location_id)
    projection = projection_totals(tenant_id, location_id)
    drift = {}
    for key in set(ledger) | set(projection):
        expected = ledger.get(key)
        actual = projection.get(key)
        if expected != actual:
            drift[key] = {"ledger": expected, "projection": actual}
    return drift


def rebuild(tenant_id, location_id) -> dict:
    """Recompute `stock_on_hand` for one sede from `stock_moves` and replace it.

    Inside one transaction holding the same advisory lock every append takes, so
    appends during a rebuild serialise rather than being lost. Naturally
    idempotent: running it twice produces the same rows.

    **The caller owns the transaction boundary, and it matters.**
    `pg_advisory_xact_lock` releases when the *transaction* ends, so the
    `atomic()` below is only a savepoint when somebody else already opened one
    -- and the sede's lock is then held until *their* transaction ends. A caller
    rebuilding several sedes must therefore open one transaction per sede, or a
    twenty-sede run becomes one long lock over the whole network instead of
    twenty short ones. `rebuild_stock_projection` pins per sede for exactly this
    reason; the `inventory.projection_rebuild` job is one sede per job and needs
    nothing extra.
    """
    with transaction.atomic():
        lock_location(tenant_id, location_id)
        ledger = ledger_totals(tenant_id, location_id)
        before = projection_totals(tenant_id, location_id)

        changed = {
            (location_id, item_id, lot_id): quantity
            for (item_id, lot_id), quantity in ledger.items()
            if before.get((item_id, lot_id)) != quantity
        }
        _apply(tenant_id, changed, absolute=True)

        # Every key the ledger no longer produces. Deleted by id rather than by
        # a `NOT IN` over a nullable column, where a single NULL `lot_id` makes
        # the predicate unknown and the delete a no-op.
        stale = [
            row.id
            for row in StockOnHand.objects.filter(
                tenant_id=tenant_id, location_id=location_id
            )
            if (row.item_id, row.lot_id) not in ledger
        ]
        if stale:
            StockOnHand.objects.filter(id__in=stale).delete()

    return {"keys": len(ledger), "changed": len(changed), "removed": len(stale)}
