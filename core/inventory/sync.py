"""What S3 puts on a till, and what a till may write back.

**The registry amendment itself lives in `core/sync/registry.py`** (ledger
rule 9): one declared artefact naming every collection that reaches a device,
amended rather than shadowed. What lives here is the two things that would
otherwise have to be written inside S2's module -- the predicate behind the
other-location set, and the two push writers -- because both are inventory rules
and S2 owns neither.

**The pushable pair, and why each is pushable.** `Cargar mercancía` is
offline-capable because merchandise arrives whether or not the internet is up,
and a box that cannot be received is a box that gets sold from while being
invisible. The counting surface is offline-capable because a count is walked
around a back room where the wifi is worst. Everything else this stage writes is
online-only and says so on its own screen: a transfer dispatch commits stock at
another location's expense, a transfer receipt is an append against a document
the device has never seen, and closing a count writes adjusting moves against a
projection the device cannot see whole.

**Neither writer touches `stock_moves` directly.** Both call the ledger service,
exactly as the HTTP endpoints do (rule 7). A push writer that inserted a move
would be the one path in the product that moved a quantity without maintaining
the projection, and it would be invisible to every check that runs against a
browser.
"""

import uuid

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from core.inventory import ledger
from core.models import (
    Item,
    Location,
    LocationStatus,
    StockCount,
    StockCountLine,
    StockMoveType,
    StockOnHand,
)
from core.sync import push as push_service

#: **The hard cap on the other-location set**, and the one figure in the
#: registry amendment that is ours rather than the seed's or the pilot's. The
#: set scales with the number of the device's *own* problems and not with the
#: size of the network, which is what keeps A4 true; the cap is what makes that
#: a guarantee rather than an expectation. A missing cap passes every check run
#: on a six-sede network and fails on a twenty-sede one.
OTHER_LOCATION_ROW_CAP = 2000

#: The trouble predicate: what the till's own sede holds at or below its reorder
#: point, or at zero. **No S3 surface renders these rows** -- Existencias is
#: online-only and server-authoritative -- so they are provisioned for S4's
#: `hay 96 en Suba` clause and measured once here rather than twice.
#: **A location-specific policy wins over a network-wide one, whole.** Joining
#: both and ORing them would pull an item into the set on a threshold its own
#: sede's row overrides -- and because the cap binds on a twenty-sede network,
#: every false positive displaces a reference that is genuinely short. The
#: `Estado` derivation resolves the same precedence the same way
#: (`core.inventory.states._policy`); two answers to one question is how a till
#: and a screen come to disagree about which references are in trouble.
TROUBLE = """
SELECT soh.item_id
FROM stock_on_hand soh
LEFT JOIN LATERAL (
    SELECT p.reorder_point
    FROM stock_policies p
    WHERE p.tenant_id = soh.tenant_id
      AND p.item_id = soh.item_id
      AND (p.location_id = soh.location_id OR p.location_id IS NULL)
    ORDER BY p.location_id DESC NULLS LAST
    LIMIT 1
) policy ON TRUE
WHERE soh.tenant_id = %s
  AND soh.location_id = %s
  AND (soh.quantity <= 0 OR soh.quantity <= policy.reorder_point)
GROUP BY soh.item_id
ORDER BY soh.item_id
LIMIT %s
"""


def troubled_items(tenant_id, location_id) -> list:
    """The items this sede is in trouble on, capped so the set cannot grow with
    the network.

    The cap is applied to **items** and derived from the number of other active
    locations, because the row count is items × the sedes that hold them: at
    twenty locations that is 105 items and at most 1.995 rows, and at six it is
    400 items the network never reaches. Ordering by `item_id` makes the capped
    set stable between pulls, so a device does not receive and lose the same
    rows on alternate cycles.
    """
    others = max(
        1,
        Location.objects.filter(tenant_id=tenant_id, status=LocationStatus.ACTIVE)
        .exclude(id=location_id)
        .count(),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            TROUBLE,
            [str(tenant_id), str(location_id), OTHER_LOCATION_ROW_CAP // others],
        )
        return [uuid.UUID(str(row[0])) for row in cursor.fetchall()]


def elsewhere_scope(*, tenant_id, location_id, options):
    """Rows at **other** sedes, for the items this one is in trouble on.

    **A row that stops being in trouble leaves the scope rather than arriving as
    a departure**, which is the same shape `customers` takes when a row ages out
    of its recency window, and it has the same answer: `GET /api/sync/digest`
    disagrees, the collection re-pulls from zero, and the stale row is gone
    within a day rather than never. Widening the scope so a departure could be
    served would mean scanning every other sede's stock on every pull, which is
    exactly the A4 failure the predicate exists to prevent.
    """
    del options
    troubled = troubled_items(tenant_id, location_id)
    if not troubled:
        # An empty `IN ()` is the honest answer: this sede has no problems, so
        # there is nothing to know about anybody else's shelf.
        return Q(pk__in=[])
    return (
        Q(tenant_id=tenant_id) & ~Q(location_id=location_id) & Q(item_id__in=troubled)
    )


def lot_scope(*, tenant_id, location_id, options):
    """The lots either stock set references.

    Joined through `stock_on_hand` rather than scoped by location, because
    `lots` has no `location_id` -- a lot is a property of merchandise and the
    same lot sits in several sedes.
    """
    del options
    troubled = troubled_items(tenant_id, location_id)
    held = StockOnHand.objects.filter(tenant_id=tenant_id).filter(
        Q(location_id=location_id) | Q(item_id__in=troubled)
    )
    return Q(tenant_id=tenant_id) & Q(
        id__in=held.filter(lot_id__isnull=False).values("lot_id")
    )


# ---------------------------------------------------------------------------
# The two push writers
# ---------------------------------------------------------------------------


def _write_receipt_line(device, collection, row, client_uuid, options):
    """One line of `Cargar mercancía`, arriving from a till that was offline.

    **The server creates or matches the `lots` row inside the pinned push
    transaction**, since `lots` is not a till-written table (rule 8) and the
    push carries the lot's natural key rather than a row. That is the whole
    reason this writer exists rather than rule 8's generic form: a receipt line
    is an event about merchandise, and the lot it names may be one no device has
    ever seen.
    """
    del collection, options
    from core.inventory.api import resolve_lot

    payload = row.get("payload") or {}
    item = Item.objects.filter(
        tenant_id=device.tenant_id, id=_uuid(payload.get("item_id"))
    ).first()
    if item is None:
        raise push_service.Rejected(
            "Esta línea nombra un producto que no existe.",
            code="item_unknown",
            field="item_id",
        )
    quantity = _int(payload.get("quantity"))
    if quantity is None or quantity <= 0:
        raise push_service.Rejected(
            "Una entrada trae una cantidad en unidades base mayor que cero.",
            code="quantity_invalid",
            field="quantity",
        )
    reason = _text(payload.get("reason")) or "standalone_receipt"
    if reason not in ("opening_stock", "standalone_receipt"):
        raise push_service.Rejected(
            "Una entrada desde el mostrador se registra como recepción directa.",
            code="reason_invalid",
            field="reason",
        )
    try:
        lot = resolve_lot(
            tenant_id=device.tenant_id,
            item=item,
            # **Every value here is a browser's.** A `lot_code` that arrives as
            # a number reaches `.strip()` as an `AttributeError`, which is not
            # in S2's `ROW_FAILURES` -- so it would leave the row's savepoint as
            # a 500 and take the nine good lines of the batch with it. Coercing
            # is what keeps one malformed line one malformed line.
            lot_code=_text(payload.get("lot_code")),
            expires_at=_date(payload.get("expires_at")),
            unit_cost=payload.get("unit_cost"),
            supplier_id=_uuid(payload.get("supplier_id")),
        )
        result = ledger.append(
            [
                ledger.Move(
                    location_id=device.location_id,
                    item_id=item.id,
                    lot_id=lot.id if lot else None,
                    quantity=quantity,
                    type=StockMoveType.ADJUSTMENT,
                    reason=reason,
                    note=_text(payload.get("note")),
                    unit_cost=payload.get("unit_cost"),
                    document_type="receipts",
                    document_id=_uuid(payload.get("document_id")),
                    client_uuid=client_uuid,
                    # **Both clocks, and the till owns the first one** (§5 rule
                    # 4). The box was received when the person scanned it, not
                    # when the link came back -- and a receipt stamped with
                    # server time puts an hour of offline work at one instant,
                    # which is the reading that makes a trace worthless.
                    occurred_at=push_service._occurred(row),
                )
            ],
            tenant_id=device.tenant_id,
            device=device,
            recorded_at=timezone.now(),
        )
    except ledger.Refused as refusal:
        raise push_service.Rejected(
            str(refusal), code="move_refused", field="quantity"
        ) from refusal

    if result.skipped:
        # A7 · a service on a receipt line writes nothing and is not a failure
        # of the entry. The row leaves the outbox, because there is nothing left
        # for it to do.
        return push_service.Outcome(client_uuid, push_service.APPLIED)
    if result.duplicates:
        return push_service.Outcome(
            client_uuid, push_service.DUPLICATE, id=str(result.duplicates[0].id)
        )
    return push_service.Outcome(
        client_uuid, push_service.APPLIED, id=str(result.written[0].id)
    )


def _write_count_line(device, collection, row, client_uuid, options):
    """One counted line, arriving from a till walked around a back room.

    **`expected_quantity` is stamped when the line reaches the server**, which
    is the same rule the online path follows -- the line is entered when it is
    entered, and a device that was offline for an hour stamps the projection as
    it stands when its batch lands. Closing a count is online-only precisely so
    that the arithmetic between the stamp and the close is the server's.
    """
    del collection, options
    payload = row.get("payload") or {}
    count = StockCount.objects.filter(
        tenant_id=device.tenant_id, id=_uuid(payload.get("count_id"))
    ).first()
    if count is None:
        raise push_service.Rejected(
            "Este conteo ya no existe en el servidor.",
            code="count_unknown",
            field="count_id",
        )
    if count.location_id != device.location_id:
        raise push_service.Rejected(
            "Este conteo es de otra sede.",
            code="count_foreign_location",
            field="count_id",
        )
    if count.status == "closed":
        raise push_service.Rejected(
            "Este conteo ya está cerrado y no admite más líneas.",
            code="count_closed",
            field="count_id",
        )
    item = Item.objects.filter(
        tenant_id=device.tenant_id, id=_uuid(payload.get("item_id"))
    ).first()
    if item is None:
        raise push_service.Rejected(
            "Esta línea nombra un producto que no existe.",
            code="item_unknown",
            field="item_id",
        )
    lot_id = _uuid(payload.get("lot_id"))
    if item.tracks_lots and lot_id is None:
        raise push_service.Rejected(
            f"«{item.name}» se maneja por lote, así que la línea necesita uno.",
            code="lot_required",
            field="lot_id",
        )
    counted = _int(payload.get("counted_quantity"))
    if counted is None or counted < 0:
        raise push_service.Rejected(
            "Un conteo registra un número de unidades de cero para arriba.",
            code="quantity_invalid",
            field="counted_quantity",
        )

    # Rule 8's first form, and it comes first: `stock_count_lines` is on that
    # list, so a replayed batch is a `duplicate` and not a merge that rewrites
    # a figure somebody has since corrected on the same line.
    replayed = StockCountLine.objects.filter(
        tenant_id=device.tenant_id, client_uuid=client_uuid
    ).first()
    if replayed is not None:
        return push_service.Outcome(
            client_uuid, push_service.DUPLICATE, id=str(replayed.id)
        )

    existing = StockCountLine.objects.filter(
        tenant_id=device.tenant_id, count=count, item_id=item.id, lot_id=lot_id
    ).first()
    if existing is not None:
        existing.counted_quantity = counted
        existing.save(update_fields=["counted_quantity", "updated_at"])
        return push_service.Outcome(
            client_uuid, push_service.MERGED, id=str(existing.id)
        )

    expected = (
        StockOnHand.objects.filter(
            tenant_id=device.tenant_id,
            location_id=count.location_id,
            item_id=item.id,
            lot_id=lot_id,
        )
        .values_list("quantity", flat=True)
        .first()
        or 0
    )
    line = StockCountLine.objects.create(
        tenant_id=device.tenant_id,
        count=count,
        item_id=item.id,
        lot_id=lot_id,
        expected_quantity=expected,
        counted_quantity=counted,
        entered_at=timezone.now(),
        client_uuid=client_uuid,
        device=device,
        occurred_at=push_service._occurred(row) or timezone.now(),
        recorded_at=timezone.now(),
    )
    return push_service.Outcome(client_uuid, push_service.APPLIED, id=str(line.id))


def _text(value) -> str:
    """One payload field, as text. `None` is the empty string, and anything else
    is whatever it prints as -- the same coercion S2's customer writer makes,
    and for the same reason."""
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


def _date(value):
    if not value:
        return None
    from django.utils.dateparse import parse_date

    return parse_date(str(value))


def register():
    """Hand S2's push endpoint the two writers this stage owns."""
    push_service.register_writer("receipt_lines", _write_receipt_line)
    push_service.register_writer("stock_count_lines", _write_count_line)
