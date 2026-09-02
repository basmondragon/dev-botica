"""`GET /api/sync/pull` -- one collection's delta after a `(updated_at, id)`
cursor, below a safety horizon.

**The cursor is the tuple, compared lexicographically and strictly.** Two
distinct failures are closed by that shape, and each of them silently loses rows
rather than erroring.

*Same-instant ties.* Postgres timestamps to the microsecond and a bulk catalog
load writes thousands of rows inside one statement. `updated_at > $C` skips
every row sharing the last-seen timestamp; `updated_at >= $C` re-serves them
forever and never advances when the tie set is larger than one page. The tuple
is a total order over rows, so a page always ends somewhere the next page can
start, however many rows share an instant.

*Commit ordering.* `updated_at` is stamped when the statement runs; the row
becomes visible when the transaction commits. A slow transaction that stamps
`10:00:01` and commits at `10:00:04` becomes visible *after* a fast one that
stamped and committed at `10:00:02`. A device that pulled at `10:00:03` would
advance past `10:00:02` and never see the slow row again. **So the pull serves
only rows at or below `server_now - pull_safety_horizon_seconds`**, and S2
requires that no transaction writing a registry table runs longer than the
horizon. Three things contain the case where that is wrong: S1's load tool
commits in batches; the horizon is a measured setting; and the client's daily
digest check turns a silent permanent loss into a one-day-late repair.

**A page carries departures as well as arrivals.** A row inside the collection's
scope that no longer satisfies its membership rule is served with a deletion
marker, computed from the predicate at read time rather than read from a delete
log -- which is only sound because a registry collection is never hard-deleted.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from core.sync import registry


@dataclass(frozen=True)
class Cursor:
    """Where a device got to. Held by the client, validated here, never stored
    server-side -- a checkpoint the server kept would be a second opinion about
    what a till has, and the till is the one that knows."""

    updated_at: datetime | None
    id: str | None

    @property
    def is_start(self):
        return self.updated_at is None


ZERO = Cursor(None, None)


def parse_cursor(updated_at, row_id) -> Cursor:
    """A checkpoint from the wire, or the refusal.

    Half a cursor is not a cursor: `updated_at` without `id` reopens the
    same-instant hole the tuple exists to close, so it is refused rather than
    defaulted.
    """
    from ninja.errors import HttpError

    if not updated_at and not row_id:
        return ZERO
    if not updated_at or not row_id:
        raise HttpError(
            422,
            "El punto de control de sincronización debe llevar `updated_at` e "
            "`id`. Un cursor a medias vuelve a abrir el empate que la tupla "
            "cierra.",
        )
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(str(updated_at))
    if parsed is None:
        raise HttpError(422, "El punto de control no es una fecha válida.")
    if not timezone.is_aware(parsed):
        parsed = timezone.make_aware(parsed)
    return Cursor(parsed, str(row_id))


def horizon(options, now=None):
    """The newest `updated_at` this pull will serve."""
    now = now or timezone.now()
    return now - timedelta(seconds=int(options["pull_safety_horizon_seconds"]))


def _after(cursor: Cursor) -> Q:
    """`(updated_at, id) > (cursor.updated_at, cursor.id)`, strictly.

    Written as the two-branch form rather than Postgres's row constructor
    because the ORM has no vocabulary for one, and the planner resolves both to
    the same index range scan on `(tenant_id, ..., updated_at, id)`.
    """
    if cursor.is_start:
        return Q()
    return Q(updated_at__gt=cursor.updated_at) | Q(
        updated_at=cursor.updated_at, id__gt=cursor.id
    )


def _page(queryset, collection, cursor, limit, ceiling):
    return list(
        queryset.filter(_after(cursor), updated_at__lte=ceiling)
        .order_by("updated_at", "id")
        .values(*collection.fields)[: limit + 1]
    )


def page(collection, *, tenant_id, location_id, cursor, limit, options, now=None):
    """One page of one collection's delta, oldest first.

    Returns `(documents, checkpoint, has_more)`. `documents` carries arrivals and
    departures interleaved in cursor order, because a device that applied them
    out of order would be applying them out of the order the server wrote them.
    """
    ceiling = horizon(options, now)
    base = collection.base(tenant_id, location_id, options)

    if collection.scope == registry.LOCATION_SCOPED:
        # Two scans, merged. `location_id = $L OR location_id IS NULL` cannot be
        # served as one ordered range on `(tenant_id, location_id, updated_at,
        # id)`, and the alternatives -- a partial index, or a bitmap union that
        # throws the ordering away -- each cost more than running the same
        # cheap scan twice.
        rows = []
        for branch in (
            Q(location_id=location_id),
            Q(location_id__isnull=True),
        ):
            rows.extend(_page(base.filter(branch), collection, cursor, limit, ceiling))
        rows.sort(key=lambda record: (record["updated_at"], str(record["id"])))
    else:
        rows = _page(base, collection, cursor, limit, ceiling)

    has_more = len(rows) > limit
    rows = rows[:limit]

    # Whatever the membership rule needs that the scan deliberately did not
    # select. One indexed lookup over the page, or nothing at all.
    if collection.enrich is not None:
        collection.enrich(rows)

    documents = []
    for record in rows:
        if collection.member(record, options):
            documents.append({**collection.document(record), "_deleted": False})
            continue
        # **A departure is its id and the marker, and nothing else.** The device
        # is being told to remove the row, so the row's contents are not part of
        # the instruction — and serving them anyway would put every customer who
        # has fallen out of the recency window, with their name, document
        # number, phone and address, on every till in the network on the first
        # sync and on every reset after it. The window exists to keep them off
        # the till; a departure that carried the payload would hand them over by
        # the other door (Ley 1581, the same reasoning `sync_conflicts.detail`
        # takes one table over).
        documents.append({**registry.head(record), "_deleted": True})

    checkpoint = (
        {"updated_at": documents[-1]["updated_at"], "id": documents[-1]["id"]}
        if documents
        else None
    )
    return documents, checkpoint, has_more
