"""The sync registry, version 1 (ledger rule 9, A4).

**One declared artefact naming every collection that reaches a device.** A table
absent from this file does not reach a device, and a stage that wants one there
amends this file and S2's stage document with its own per-location row estimate.

That is not bureaucracy. At the pilot's six sedes the whole of registry v1 is
about 24.800 rows and 4,6 MB, which is comfortable. The same figures with one
predicate dropped are not: S3's `stock_on_hand` unscoped across a twenty-sede
network is 140.000 rows and some 16 MB, three times the entire catalog, for data
nineteen sedes' worth of which the till never reads. Nothing about that failure
announces itself in code review -- it announces itself as a pilot till taking
ninety seconds to start.

**Each collection declares four things separately, and the split is deliberate.**

  `scope`      the Q the tuple scan runs under. Tenant, and location where the
               collection is location-scoped. It is index-shaped by
               construction, because `/api/sync/pull` has a 20 ms p95 budget
               (§4) and anything that turns it into a join is a defect.
  `member`     whether a row *currently belongs* to the collection. Evaluated
               over the returned page in Python, never in the scan's WHERE.
  `member_q`   the same rule as a queryset filter, for the two cold paths --
               the registry's per-device totals and the daily digest -- where a
               join costs nothing because they run once a day.
  `document`   the row as the device stores it.

A row inside `scope` but failing `member` is served **with a deletion marker**,
which is how an item that was deactivated leaves a till. That is only sound
because a registry collection is never hard-deleted: a hard delete leaves no row
to evaluate and no `updated_at` to serve, so the row would survive on every till
indefinitely. S1's model already works this way -- `items.active` and
`item_prices.effective_to` are the mechanisms.
"""

from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Callable

from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Category,
    Customer,
    Item,
    ItemBarcode,
    ItemPrice,
    Manufacturer,
)

#: Bumped whenever a collection is added, removed, or its document shape
#: changes. Every pull response carries it; a client behind the server enters
#: `degraded · versión desactualizada` and reloads the application shell.
REGISTRY_VERSION = 1

TENANT_WIDE = "tenant"
LOCATION_SCOPED = "location"


class Unpushable(Exception):
    """A collection the push endpoint refuses.

    Rule 8 has exactly two idempotency forms and no third: `(tenant_id,
    client_uuid)` for every table in its list, and a **declared natural key**
    otherwise. A table with neither is not pushable, and saying so here is what
    keeps that a property of the registry rather than of a review.
    """


@dataclass(frozen=True)
class Collection:
    """One row of the registry table in S2's stage document."""

    name: str
    model: type[models.Model]
    scope: str
    #: Whether a device may write it. `no` is the enforcement rule 5 of §5's
    #: five wants: a stage that tries to push a projection is refused here.
    push: bool
    #: The declared natural key, for a pushable collection outside rule 8's
    #: list. `None` on a collection that dedupes on `(tenant_id, client_uuid)`.
    natural_key: tuple[str, ...] | None
    #: The pilot's own sizing, per location. Not the seed's -- the seed builds
    #: around forty customers where a pilot holds nine thousand, and a check
    #: asserting these against a seeded tenant fails on every run.
    rows_per_location: int
    bytes_per_location: int
    #: `(model_field, document_field)` pairs, plus the joined flags `member`
    #: needs. Stated rather than derived, so adding a column to `items` does not
    #: silently widen what every till in the network downloads.
    fields: tuple[str, ...]
    scope_q: Callable[..., Q]
    member_q: Callable[..., Q]
    member: Callable[[dict, dict], bool]
    document: Callable[[dict], dict]
    #: Fills in whatever `member` needs that is not on the row itself, for the
    #: page and only for the page. `None` where the row answers on its own.
    #:
    #: This exists so that the cursor query stays a **pure index range scan**.
    #: `/api/sync/pull` has a 20 ms p95 budget (§4) and the document is
    #: emphatic that anything making it a join is a defect -- so the one
    #: collection whose membership rule reads another table resolves it in a
    #: second indexed lookup over at most `limit` primary keys, rather than by
    #: putting a join in the scan the budget is measured on.
    enrich: Callable[[list[dict]], None] | None = None

    def base(self, tenant_id, location_id, options):
        """The queryset every read of this collection starts from."""
        # `_default_manager` rather than `.objects`: the field is typed as the
        # model class, and Django's own accessor is what a type checker can
        # follow through it.
        return self.model._default_manager.filter(
            self.scope_q(tenant_id=tenant_id, location_id=location_id, options=options)
        )


def _iso(value):
    """The one timestamp format the wire uses.

    It is stated once because the digest is a checksum over these exact strings:
    the client hashes what it stored and the server hashes what it would have
    sent, and two formatters is a permanent false mismatch that resets a
    collection every day.
    """
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _uuid(value):
    return None if value is None else str(value)


def _decimal(value):
    """A money or quantity value as a string.

    Never a float. `12345.67` through IEEE 754 and back is not `12345.67`, and
    a price is the one figure in this product a customer is about to pay.
    """
    return None if value is None else str(value)


def _date(value):
    return None if value is None else value.isoformat()


def head(record):
    """`id` and `updated_at` -- what every document carries, what the cursor and
    the digest are both computed over, and **the whole of a departure**."""
    return {"id": str(record["id"]), "updated_at": _iso(record["updated_at"])}


#: The private spelling the collections below use. One function, two names, so
#: that `pull.py` and `digest.py` reading it is a public act rather than a reach
#: into another module's underscore.
_head = head


# ---------------------------------------------------------------------------
# The six collections of version 1.
# ---------------------------------------------------------------------------


def _mark_active_items(records):
    """Stamp `item_active` on a page of barcodes, in one indexed lookup.

    At most `pull_page_size` primary keys, and zero when the page is empty --
    which is the case the p95 budget is about.
    """
    if not records:
        return
    ids = {record["item_id"] for record in records}
    active = dict(Item.objects.filter(id__in=ids).values_list("id", "active"))
    for record in records:
        record["item_active"] = bool(active.get(record["item_id"], False))


def _tenant_scope(*, tenant_id, location_id, options):
    del location_id, options
    return Q(tenant_id=tenant_id)


def _price_scope(*, tenant_id, location_id, options):
    """This sede's prices and the network-wide ones, and no other sede's.

    The `OR location_id IS NULL` half is why `pull.py` runs the tuple scan twice
    and merges: one scan per branch keeps both on
    `(tenant_id, location_id, updated_at, id)` rather than making the index
    partial or forcing a bitmap union that loses the ordering the cursor needs.
    """
    del options
    return Q(tenant_id=tenant_id) & (
        Q(location_id=location_id) | Q(location_id__isnull=True)
    )


def _customer_window(options):
    months = int(options["customer_recency_months"])
    # Calendar months are not needed here and a month is not a fixed length; the
    # window is a proxy for recency and 30 days is the honest approximation.
    return timezone.now() - timedelta(days=30 * months)


ITEMS = Collection(
    name="items",
    model=Item,
    scope=TENANT_WIDE,
    push=False,
    natural_key=None,
    rows_per_location=4284,
    bytes_per_location=1_800_000,
    fields=(
        "id",
        "updated_at",
        "name",
        "search_name",
        "type",
        "presentation",
        "active_ingredient",
        "strength",
        "unit",
        "units_per_pack",
        "splittable",
        "vat_class",
        "manufacturer_id",
        "category_id",
        "requires_prescription",
        "controlled",
        "cold_chain",
        "tracks_stock",
        "tracks_lots",
        "tracks_expiry",
        "invima_status",
        "active",
    ),
    scope_q=_tenant_scope,
    member_q=lambda options: Q(active=True),
    member=lambda record, options: bool(record["active"]),
    document=lambda record: {
        **_head(record),
        "name": record["name"],
        "search_name": record["search_name"] or "",
        "type": record["type"],
        "presentation": record["presentation"],
        "active_ingredient": record["active_ingredient"],
        "strength": record["strength"],
        "unit": record["unit"],
        "units_per_pack": record["units_per_pack"],
        "splittable": record["splittable"],
        "vat_class": record["vat_class"],
        "manufacturer_id": _uuid(record["manufacturer_id"]),
        "category_id": _uuid(record["category_id"]),
        "requires_prescription": record["requires_prescription"],
        "controlled": record["controlled"],
        "cold_chain": record["cold_chain"],
        "tracks_stock": record["tracks_stock"],
        "tracks_lots": record["tracks_lots"],
        "tracks_expiry": record["tracks_expiry"],
        "invima_status": record["invima_status"],
    },
)

BARCODES = Collection(
    name="item_barcodes",
    model=ItemBarcode,
    scope=TENANT_WIDE,
    push=False,
    natural_key=None,
    rows_per_location=6900,
    bytes_per_location=550_000,
    # The scan selects only this table's own columns. Whether the barcode's item
    # is still active is `enrich`'s job, in a second lookup over the page --
    # because a barcode whose item was deactivated has to arrive as a
    # **departure**, so a WHERE that excluded it would leave it on every till
    # forever, and a join would put the item table in the query the 20 ms budget
    # is measured on.
    fields=("id", "updated_at", "item_id", "code", "is_primary"),
    scope_q=_tenant_scope,
    # The cold paths -- the registry's per-device totals and the daily digest --
    # may join: they run once per device per day, not every eight seconds.
    member_q=lambda options: Q(item__active=True),
    member=lambda record, options: bool(record["item_active"]),
    enrich=lambda records: _mark_active_items(records),
    document=lambda record: {
        **_head(record),
        "item_id": _uuid(record["item_id"]),
        "code": record["code"],
        "is_primary": record["is_primary"],
    },
)

MANUFACTURERS = Collection(
    name="manufacturers",
    model=Manufacturer,
    scope=TENANT_WIDE,
    push=False,
    natural_key=None,
    rows_per_location=120,
    bytes_per_location=11_000,
    fields=("id", "updated_at", "name", "search_name"),
    scope_q=_tenant_scope,
    member_q=lambda options: Q(),
    member=lambda record, options: True,
    document=lambda record: {
        **_head(record),
        "name": record["name"],
        "search_name": record["search_name"] or "",
    },
)

CATEGORIES = Collection(
    name="categories",
    model=Category,
    scope=TENANT_WIDE,
    push=False,
    natural_key=None,
    rows_per_location=80,
    bytes_per_location=6_000,
    fields=("id", "updated_at", "name", "parent_id"),
    scope_q=_tenant_scope,
    member_q=lambda options: Q(),
    member=lambda record, options: True,
    document=lambda record: {
        **_head(record),
        "name": record["name"],
        "parent_id": _uuid(record["parent_id"]),
    },
)

PRICES = Collection(
    name="item_prices",
    model=ItemPrice,
    scope=LOCATION_SCOPED,
    push=False,
    natural_key=None,
    rows_per_location=4400,
    bytes_per_location=480_000,
    fields=(
        "id",
        "updated_at",
        "item_id",
        "location_id",
        "price",
        "effective_from",
        "effective_to",
    ),
    scope_q=_price_scope,
    # An open window, or one that has not closed yet. A price whose
    # `effective_to` has passed leaves the till as a departure, which is the
    # mechanism S1 already built rather than one this stage invented.
    member_q=lambda options: (
        Q(effective_to__isnull=True) | Q(effective_to__gt=timezone.localdate())
    ),
    member=lambda record, options: (
        record["effective_to"] is None or record["effective_to"] > timezone.localdate()
    ),
    document=lambda record: {
        **_head(record),
        "item_id": _uuid(record["item_id"]),
        "location_id": _uuid(record["location_id"]),
        "price": _decimal(record["price"]),
        "effective_from": _date(record["effective_from"]),
        "effective_to": _date(record["effective_to"]),
    },
)

CUSTOMERS = Collection(
    name="customers",
    model=Customer,
    scope=TENANT_WIDE,
    push=True,
    #: Ledger rule 8's second paragraph, as this stage's declared natural key.
    #: It is also S1's `one_customer_per_document_per_tenant`, which is what
    #: makes two tills registering the same person converge on one row.
    natural_key=("document_type", "document"),
    rows_per_location=9000,
    bytes_per_location=1_710_000,
    fields=(
        "id",
        "updated_at",
        "document_type",
        "document",
        "name",
        "phone",
        "email",
        "address",
        "data_consent",
    ),
    scope_q=_tenant_scope,
    # A window rather than the whole list: a network's customer list has no
    # ceiling and a till's storage does.
    #
    # A row that ages out of the window has an `updated_at` **behind** the
    # device's cursor, so it is never served again and never arrives as a
    # departure. It is not the only such case -- a price whose `effective_to`
    # passes leaves the predicate by the calendar rather than by a write, and so
    # does a row any endpoint hard-deletes -- and every one of them has the same
    # answer: `GET /api/sync/digest` disagrees, the collection re-pulls from
    # zero, and the stale row is gone within a day rather than never. Two of the
    # three are held to *within a pull interval* instead, because their
    # departure really is a write: `items.active`, and `item_barcodes` through
    # the trigger migration 0008 installs for exactly this.
    member_q=lambda options: Q(updated_at__gte=_customer_window(options)),
    member=lambda record, options: record["updated_at"] >= _customer_window(options),
    document=lambda record: {
        **_head(record),
        "document_type": record["document_type"],
        "document": record["document"],
        "name": record["name"],
        "phone": record["phone"],
        "email": record["email"],
        "address": record["address"],
        "data_consent": record["data_consent"],
    },
)


#: **Version 1.** Ordered as a first sync should run: the catalog before the
#: prices that reference it, so a half-synced till never renders a price with no
#: product behind it.
COLLECTIONS: tuple[Collection, ...] = (
    ITEMS,
    BARCODES,
    MANUFACTURERS,
    CATEGORIES,
    PRICES,
    CUSTOMERS,
)

BY_NAME: dict[str, Collection] = {one.name: one for one in COLLECTIONS}

#: The tables a device may write, as a set, for the push endpoint's own guard.
PUSHABLE = frozenset(one.name for one in COLLECTIONS if one.push)


def get(name: str) -> Collection:
    """One collection by its registry name, or a refusal naming the registry."""
    collection = BY_NAME.get(name)
    if collection is None:
        raise LookupError(
            f"{name!r} is not in the sync registry. A table absent from the "
            "registry does not reach a device (ownership.md rule 9); adding one "
            "is an edit to core/sync/registry.py and to S2's stage document."
        )
    return collection


def pushable(name: str) -> Collection:
    """One collection a device may write, or the refusal rule 8 requires."""
    collection = get(name)
    if not collection.push:
        raise Unpushable(
            f"{name!r} is a reference collection and a device may not write it. "
            "The registry's `push` column is the enforcement."
        )
    if collection.natural_key is None and not _has_client_uuid(collection.model):
        raise Unpushable(
            f"{name!r} declares neither a natural key nor a `client_uuid`, so no "
            "push of it can be idempotent. Rule 8 has two forms and no third."
        )
    return collection


def _has_client_uuid(model) -> bool:
    """Whether a model carries rule 8's client-write quartet.

    No S2 collection does -- `customers` is S1's master-data table and is pushed
    under its natural key. S3's `stock_moves` and S4's `sales` are the first
    that will, and `push.py` already knows how to dedupe them.
    """
    return any(field.name == "client_uuid" for field in model._meta.get_fields())


def totals(tenant_id, location_id, options) -> dict[str, int]:
    """The real row count per collection for **this** device.

    This is what the first-sync card counts against, and it is a read rather
    than an estimate on purpose: a progress bar counting toward a figure the
    client made up is a progress bar that finishes at 94%.
    """
    counts = {}
    for collection in COLLECTIONS:
        counts[collection.name] = (
            collection.base(tenant_id, location_id, options)
            .filter(collection.member_q(options))
            .count()
        )
    return counts
