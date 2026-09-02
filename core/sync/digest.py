"""`GET /api/sync/digest` -- the daily divergence check.

The safety horizon is an engineering choice and not a bet **because of this
endpoint**. It answers, per collection, the row count and a checksum over
`(id, updated_at)` for this device's predicate at the horizon. The client
compares it against its local store once a day on first idle; a mismatch resets
that collection's checkpoint and re-pulls it from zero.

That turns two otherwise-silent permanent losses into a one-day-late repair: a
row written inside a transaction that outlived the horizon, and a `customers`
row that aged out of the recency window behind the device's own cursor and could
therefore never be served as a departure.

**The checksum is computed in Python, over the same strings `pull.py` would have
sent.** A SQL `md5(string_agg(...))` would be faster and would compare a
timestamp Postgres formatted against one the browser stored, which is a
permanent false mismatch that re-pulls every collection every day. One
formatter, one hash, both sides.
"""

import hashlib

from core.sync import registry


def collection_digest(collection, *, tenant_id, location_id, cursor_limit, options):
    """`(count, checksum)` for one collection at the horizon."""
    rows = (
        collection.base(tenant_id, location_id, options)
        .filter(collection.member_q(options), updated_at__lte=cursor_limit)
        .order_by("updated_at", "id")
        .values("id", "updated_at")
    )
    digest = hashlib.sha256()
    count = 0
    for record in rows.iterator(chunk_size=2000):
        head = registry.head(record)
        digest.update(f"{head['id']}:{head['updated_at']}\n".encode("utf-8"))
        count += 1
    return count, digest.hexdigest()


def build(*, tenant_id, location_id, cursor_limit, options):
    """Every collection's digest, in registry order."""
    answer = {}
    for collection in registry.COLLECTIONS:
        count, checksum = collection_digest(
            collection,
            tenant_id=tenant_id,
            location_id=location_id,
            cursor_limit=cursor_limit,
            options=options,
        )
        answer[collection.name] = {"count": count, "checksum": checksum}
    return answer
