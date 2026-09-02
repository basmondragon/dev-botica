"""The office's arrival queue.

S2 raises the protocol-level rows -- a foreign tenant, a foreign location, an
unknown collection, a rejected payload, a revoked device, a silent device -- and
lists every row out. **S3 writes the negative-stock rows and S4 writes
`stale_price` and `catalog_divergence` into this same table** and neither
re-exposes the reader, which is what gives all three of §5's named
reconciliations one queue instead of three.

**`detail` never carries the rejected payload verbatim.** It carries the
collection, the failing field, the reason code and the correlation id. A
rejected `customers` row contains a person's document number, and a conflict
queue is not a place to accumulate identifying data nobody asked to store
(Ley 1581, §7's reasoning one table over). `scrub` is what makes that a
mechanism rather than a habit.
"""

import uuid

from django.utils import timezone

from core.models import SyncConflict, SyncConflictStatus, SyncConflictType

#: What a `detail` may hold. Anything else is dropped rather than truncated,
#: because a truncated document number is still a document number.
ALLOWED_DETAIL_KEYS = frozenset(
    {
        "reason",
        "field",
        "request_id",
        "batch_id",
        "collection",
        "count",
        "hours",
        "last_synced_at",
        "item_id",
        "lot_id",
        "sale_id",
        # S3's negative-stock rows: the resulting quantity, the documents that
        # crossed zero, and the `(location, item, lot)` marker the ledger finds
        # a standing row by. None of the three is a person's data, which is the
        # rule this list exists to keep.
        "quantity",
        "documents",
        "key",
    }
)

#: The namespace S2 derives a deterministic conflict id from, where the row's
#: identity is its idempotency key rather than a new uuid. Only the daily
#: stale-device check uses it.
NAMESPACE = uuid.UUID("2f3c9c66-2b7a-5a2e-9a44-6d0dbf1c7e21")


def scrub(detail):
    """The allowed keys, with every value coerced to something small.

    Enforced here and not at each call site: eight call sites across four stages
    is eight chances to pass a payload through, and the one that does will be
    the one carrying a cédula.
    """
    if not detail:
        return {}
    clean: dict[str, object] = {}
    for key, value in detail.items():
        if key not in ALLOWED_DETAIL_KEYS:
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            clean[key] = value
        else:
            clean[key] = str(value)[:200]
    return clean


def raise_conflict(
    *,
    device=None,
    type,
    collection="",
    client_uuid=None,
    occurred_at=None,
    detail=None,
    tenant_id=None,
    location_id=None,
    row_id=None,
):
    """Append one row to the queue, or refresh the one already standing.

    `row_id` is for a caller whose row has an idempotency key of its own -- the
    daily stale-device check derives its id from `(tenant, device, date)` so a
    re-run on the same day updates the existing row rather than adding a second.

    **`device` is optional, and S3 is why.** A negative-stock row is raised by
    the ledger service on whatever path drove the projection below zero, and
    three of those paths -- a management command, a transfer receipt and a count
    close -- have no till behind them. `sync_conflicts.device` was created
    nullable for exactly this; the tenant and the location then have to be named
    outright, because there is no device to read them from.
    """
    if device is None and (tenant_id is None or location_id is None):
        raise ValueError(
            "A conflict raised with no device must name its tenant and its "
            "location: there is nothing else to read them from."
        )
    fields = dict(
        tenant_id=tenant_id or device.tenant_id,
        device=device,
        location_id=location_id or (device.location_id if device else None),
        # Bounded here rather than at each call site: `collection` comes off a
        # push payload, so it is a value a browser chose, and a 100-character
        # one would turn the refusal it names into a 500 that records nothing.
        collection=(collection or "")[:64],
        client_uuid=client_uuid,
        type=type,
        detail=scrub(detail),
        status=SyncConflictStatus.OPEN,
        occurred_at=occurred_at,
        recorded_at=timezone.now(),
    )
    if row_id is None:
        return SyncConflict.objects.create(**fields)
    existing = SyncConflict.objects.filter(id=row_id).first()
    if existing is None:
        return SyncConflict.objects.create(id=row_id, **fields)
    # **A row an administrator already closed stays closed.** Refreshing it back
    # to `open` would reopen a conflict somebody dismissed with a reason, and
    # would leave their resolution stamps attached to a row that now claims to
    # be unresolved -- which is the queue answering a question nobody asked
    # again, every day, until they stop reading it.
    if existing.status != SyncConflictStatus.OPEN:
        return existing
    for name, value in fields.items():
        setattr(existing, name, value)
    existing.save()
    return existing


def daily_id(tenant_id, device_id, day):
    """`(tenant_id, device_id, date)` -- the stale check's idempotency key."""
    return uuid.uuid5(NAMESPACE, f"{tenant_id}:{device_id}:{day.isoformat()}")


def open_counts(tenant_id, device_ids):
    """Open conflicts per device, for the office list's one extra column."""
    from django.db.models import Count

    rows = (
        SyncConflict.objects.filter(
            tenant_id=tenant_id,
            device_id__in=list(device_ids),
            status=SyncConflictStatus.OPEN,
        )
        .values("device_id")
        .annotate(total=Count("id"))
    )
    return {str(row["device_id"]): row["total"] for row in rows}


#: The values S2 itself raises. S3 and S4 write the other three; the enum
#: carries all nine from creation so neither ships an `ALTER TYPE`.
PROTOCOL_TYPES = (
    SyncConflictType.FOREIGN_TENANT,
    SyncConflictType.FOREIGN_LOCATION,
    SyncConflictType.UNKNOWN_COLLECTION,
    SyncConflictType.PAYLOAD_REJECTED,
    SyncConflictType.DEVICE_REVOKED,
    SyncConflictType.DEVICE_SILENT,
)
