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
    AssistantQuery,
    AssistantSuggestion,
    Category,
    CrossSellRule,
    Customer,
    Item,
    ItemBarcode,
    ItemPrice,
    ItemWarning,
    Lot,
    Manufacturer,
    Payment,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    Shift,
    StockCountLine,
    StockMove,
    StockOnHand,
    StockPolicy,
)

#: Bumped whenever a collection is added, removed, or its document shape
#: changes. Every pull response carries it; a client behind the server enters
#: `degraded · versión desactualizada` and reloads the application shell.
#:
#: **2 · S3's amendment** (rule 9): four collections a till reads --
#: `stock_on_hand`, `stock_elsewhere`, `lots` and `stock_policies` -- and two it
#: only ever writes, `receipt_lines` and `stock_count_lines`.
#:
#: **3 · S4's amendment** (rule 9): the six the till both reads and writes --
#: `shifts`, `sales`, `sale_lines`, `payments`, `sale_returns` and
#: `sale_return_lines`. They are the first collections in the registry that go
#: both ways, which is what an offline till selling actually is.
#:
#: **4 · S8's amendment** (rule 9, A8): `cross_sell_rules` and `item_warnings`
#: down to the till under a per-location cap, and `assistant_queries` and
#: `assistant_suggestions` up through the outbox. The safety layer reaching the
#: device is what makes the offline assistant filter on something rather than
#: looking exactly as if it does.
REGISTRY_VERSION = 4

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
    #: Whether a device may **read** it. Every S2 collection does; S3 adds the
    #: first two that a till writes and never pulls -- a receipt line and a
    #: counted line are events, and the till already knows what it sent. A
    #: push-only collection is absent from the digest, from the first-sync
    #: card's totals and from `GET /api/sync/pull`, because each of the three
    #: asks a question about a snapshot and an event log is not one.
    pull: bool = True

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


# ---------------------------------------------------------------------------
# S3's amendment · four collections a till reads and two it only writes
# (ownership.md rule 9).
#
# The estimates are the pilot's, from the handoff's own figures: 1.184 active
# references per sede and roughly 1,6 open lots per referencia. They are not the
# seed's, and a check that asserts one against the other is red on every run for
# a reason that has nothing to do with the code.
#
# **Existencias is online-only and server-authoritative, so no S3 surface reads
# any of these in a browser.** They are provisioned for S4's counter -- its
# stock, its FEFO queue, its expiry display and its low-stock signal, all at
# zero latency -- and the registry is amended and measured once rather than
# twice.
# ---------------------------------------------------------------------------


def _own_location(*, tenant_id, location_id, options):
    del options
    return Q(tenant_id=tenant_id, location_id=location_id)


def _policy_scope(*, tenant_id, location_id, options):
    """This sede's thresholds and the network-wide ones, and no other sede's.

    The same `OR location_id IS NULL` shape `item_prices` takes, and served the
    same way: `pull.py` runs the tuple scan once per branch and merges, which
    keeps both on `(tenant_id, location_id, updated_at, id)`.
    """
    del options
    return Q(tenant_id=tenant_id) & (
        Q(location_id=location_id) | Q(location_id__isnull=True)
    )


def _elsewhere_scope(*, tenant_id, location_id, options):
    from core.inventory.sync import elsewhere_scope

    return elsewhere_scope(
        tenant_id=tenant_id, location_id=location_id, options=options
    )


def _lot_scope(*, tenant_id, location_id, options):
    from core.inventory.sync import lot_scope

    return lot_scope(tenant_id=tenant_id, location_id=location_id, options=options)


def _stock_document(record):
    return {
        **_head(record),
        "location_id": _uuid(record["location_id"]),
        "item_id": _uuid(record["item_id"]),
        "lot_id": _uuid(record["lot_id"]),
        "quantity": record["quantity"],
        # Carried only by the other-location set, below.
        "location_name": record.get("location_name"),
    }


def _mark_location_names(records):
    """Stamp the sede's name on a page of other-location rows.

    **A till holds no `locations` collection** -- the registry has thirteen RxDB
    slots and none of them is spent on six names -- so `hay 96 en Suba` could
    not be rendered offline from an id. One indexed lookup over the handful of
    sedes on the page is what makes the clause answerable with the cable out of
    the wall, which is the only condition it is ever read in.
    """
    if not records:
        return
    from core.models import Location

    ids = {record["location_id"] for record in records}
    names = dict(Location.objects.filter(id__in=ids).values_list("id", "name"))
    for record in records:
        record["location_name"] = names.get(record["location_id"], "")


STOCK_FIELDS = ("id", "updated_at", "location_id", "item_id", "lot_id", "quantity")

STOCK_ON_HAND = Collection(
    name="stock_on_hand",
    model=StockOnHand,
    scope=LOCATION_SCOPED,
    push=False,
    natural_key=None,
    rows_per_location=1900,
    bytes_per_location=230_000,
    fields=STOCK_FIELDS,
    scope_q=_own_location,
    # Every row in scope belongs: the till's own sede's stock at lot grain is
    # tier 1 authoritative and carries no staleness marker (§B.9.2). A key that
    # sums to zero stays -- it is what `Quiebre` renders from.
    member_q=lambda options: Q(),
    member=lambda record, options: True,
    document=_stock_document,
)

STOCK_ELSEWHERE = Collection(
    name="stock_elsewhere",
    model=StockOnHand,
    # **Tenant-wide by declaration and not by predicate.** The scan is not "this
    # sede's rows" -- it is every *other* sede's, for the items this one is in
    # trouble on -- so `pull.py`'s location branch, which serves
    # `location_id = $L OR location_id IS NULL`, would return nothing at all.
    scope=TENANT_WIDE,
    push=False,
    natural_key=None,
    # ≈ 420 at six sedes and ≈ 1.600 at twenty, hard-capped at 2.000 in
    # `core.inventory.sync`. The set scales with the number of the device's own
    # problems, not with the size of the network, which is what keeps A4 true.
    rows_per_location=420,
    bytes_per_location=50_000,
    fields=STOCK_FIELDS,
    scope_q=_elsewhere_scope,
    member_q=lambda options: Q(),
    member=lambda record, options: True,
    # The sede's own name, because the till has no `locations` collection to
    # join against and `hay 96 en Suba` is read offline or not at all.
    enrich=lambda records: _mark_location_names(records),
    document=_stock_document,
)

LOTS = Collection(
    name="lots",
    model=Lot,
    # `lots` has no `location_id`, so the scan cannot be location-scoped; the
    # predicate joins through `stock_on_hand` instead, and the delta cursor on
    # this table is `(tenant_id, updated_at, id)` for the same reason.
    scope=TENANT_WIDE,
    push=False,
    natural_key=None,
    rows_per_location=2300,
    bytes_per_location=253_000,
    fields=("id", "updated_at", "item_id", "lot_code", "expires_at", "unit_cost"),
    scope_q=_lot_scope,
    member_q=lambda options: Q(),
    member=lambda record, options: True,
    document=lambda record: {
        **_head(record),
        "item_id": _uuid(record["item_id"]),
        "lot_code": record["lot_code"],
        "expires_at": _date(record["expires_at"]),
        "unit_cost": _decimal(record["unit_cost"]),
    },
)

POLICIES = Collection(
    name="stock_policies",
    model=StockPolicy,
    scope=LOCATION_SCOPED,
    push=False,
    natural_key=None,
    rows_per_location=1200,
    bytes_per_location=144_000,
    fields=(
        "id",
        "updated_at",
        "item_id",
        "location_id",
        "min_quantity",
        "max_quantity",
        "reorder_point",
        "target_coverage_days",
        "source",
    ),
    scope_q=_policy_scope,
    member_q=lambda options: Q(),
    member=lambda record, options: True,
    document=lambda record: {
        **_head(record),
        # **The discriminator, and it is stamped rather than guessed.** S8's two
        # reference collections share this store on the device -- RxDB's
        # open-core build opens thirteen and the outbox is the thirteenth -- so
        # each stream's rows are told apart by a `kind` the server writes, which
        # is the same mechanism S4's five shared documents already use. A
        # heuristic over which fields are present would delete the wrong rows on
        # a reset and leave the till sitting on a hole until the next digest.
        "kind": "policy",
        "item_id": _uuid(record["item_id"]),
        "location_id": _uuid(record["location_id"]),
        "min_quantity": record["min_quantity"],
        "max_quantity": record["max_quantity"],
        "reorder_point": record["reorder_point"],
        "target_coverage_days": record["target_coverage_days"],
        "source": record["source"],
    },
)


# ---------------------------------------------------------------------------
# S4's amendment · six collections the till reads **and** writes
# (ownership.md rule 9).
#
# They are the first two-way collections in the registry, which is what an
# offline till selling actually is: the same rows it writes are the rows a
# return, a shift's cash arithmetic and the average-ticket note read back.
#
# **Seven days, not thirty.** Thirty is 18.000 sales and roughly 54.000 lines
# per sede, which is no longer "a few megabytes" (§4) and pushes cold start
# against its 2,5 s budget. Seven covers the return window a droguería actually
# sees and keeps the store small. The estimates below are the pilot's, against
# §4's own load figure of 600 tickets per sede per day -- not the seed's.
#
# **The predicate is the location, not the device.** A customer returns to the
# counter, not to a machine, so every till at a sede can serve a return rung up
# on any of them.
#
# **The two retention windows are constants here rather than settings keys**,
# because the ledger's key register assigns S4 no `tenants.settings` group and
# S4 does not invent one (S4, *Gated on*). A pilot that needs them configurable
# is a change to the register first.
#
# **What S4 does not do to `customers`.** S4's stage document narrows that
# collection to *seen at this location within 180 days*. Answering "seen at
# this location" means joining `sales`, and `scope_q` is the query
# `/api/sync/pull` budgets at 20 ms p95 -- a join there is the defect that
# budget exists to prevent, and `member` cannot answer it either because the
# fact is not on the row. The recency window S2 already applies is what bounds
# the slice; doing it properly would mean a denormalised
# `customers.last_seen_location_id`, which is a column on S1's table and S1's to
# decide.
# ---------------------------------------------------------------------------

#: A turno stays on the till a month: the open one, and the recent closed ones
#: the shift list shows.
SHIFT_RETENTION_DAYS = 30

#: A sale stays a week. Every `open` sale stays regardless of age -- a stranded
#: ticket is exactly the row a till must not lose sight of.
SALE_RETENTION_DAYS = 7


def _window(days):
    return timezone.now() - timedelta(days=days)


#: **The membership predicate names `source`, and S6 is why** (rule 4's spirit:
#: an invariant belongs to the stage whose rows make it necessary, not to the
#: table's owner). S6's history loader writes `sales` rows at `source =
#: imported` -- documents another system issued years ago, with no shift, no
#: device, no payment and no `stock_moves` row behind them. A legacy export that
#: runs right up to cutover carries last week's sales, which would fall inside
#: the retention window below and replicate to every till in the sede: the
#: ticket list would show sales no cashier rang, and the counter's own average
#: would be computed over two systems.
COUNTER_ONLY = Q(source="counter")


def _sale_member_q(options):
    del options
    return COUNTER_ONLY & (
        Q(occurred_at__gte=_window(SALE_RETENTION_DAYS)) | Q(status="open")
    )


def _child_of_sale_member_q(options):
    del options
    return Q(sale__source="counter") & (
        Q(sale__occurred_at__gte=_window(SALE_RETENTION_DAYS)) | Q(sale__status="open")
    )


def _mark_parent_window(records, parent_field, model, columns):
    """Stamp a page of children with what their parent's membership turns on.

    At most `pull_page_size` primary keys, and zero when the page is empty --
    which is the case the p95 budget is about. This is the same shape
    `_mark_active_items` takes for `item_barcodes`, and it exists for the same
    reason: a child whose parent left the window has to arrive as a
    **departure**, so a WHERE that excluded it would leave it on every till
    forever, and a join would put the parent table in the query the 20 ms budget
    is measured on.
    """
    if not records:
        return
    ids = {record[parent_field] for record in records if record[parent_field]}
    if not ids:
        return
    parents = {
        row["id"]: row
        for row in model._default_manager.filter(id__in=ids).values("id", *columns)
    }
    for record in records:
        parent = parents.get(record[parent_field]) or {}
        for column in columns:
            record[f"parent_{column}"] = parent.get(column)


def _child_in_sale_window(record) -> bool:
    occurred = record.get("parent_occurred_at")
    if record.get("parent_status") == "open":
        return True
    return occurred is not None and occurred >= _window(SALE_RETENTION_DAYS)


SHIFTS = Collection(
    name="shifts",
    model=Shift,
    scope=LOCATION_SCOPED,
    push=True,
    natural_key=None,
    rows_per_location=60,
    bytes_per_location=14_000,
    fields=(
        "id",
        "updated_at",
        "location_id",
        # **The till the drawer belongs to.** The collection is scoped by sede,
        # so a second till at the same sede pulls the first one's open turno --
        # and with no `device_id` on the document it could not tell that turno
        # from its own. The drawer belongs to the till, not to the person, and
        # this is the column that says so on the device.
        "device_id",
        "user_id",
        "user_name",
        "opened_at",
        "closed_at",
        "opening_float",
        "declared_total",
        "variance",
        "status",
    ),
    scope_q=_own_location,
    member_q=lambda options: Q(opened_at__gte=_window(SHIFT_RETENTION_DAYS)),
    member=lambda record, options: record["opened_at"] >= _window(SHIFT_RETENTION_DAYS),
    document=lambda record: {
        **_head(record),
        "location_id": _uuid(record["location_id"]),
        "device_id": _uuid(record["device_id"]),
        "user_id": _uuid(record["user_id"]),
        "user_name": record["user_name"],
        "opened_at": _iso(record["opened_at"]),
        "closed_at": _iso(record["closed_at"]),
        "opening_float": _decimal(record["opening_float"]),
        "declared_total": _decimal(record["declared_total"]),
        "variance": _decimal(record["variance"]),
        "status": record["status"],
    },
)

SALES = Collection(
    name="sales",
    model=Sale,
    scope=LOCATION_SCOPED,
    push=True,
    natural_key=None,
    rows_per_location=4200,
    bytes_per_location=1_150_000,
    fields=(
        "id",
        "updated_at",
        "location_id",
        "shift_id",
        "number",
        "status",
        "source",
        "customer_id",
        "subtotal",
        "discount",
        "tax",
        "total",
        "sold_by_user_id",
        "sold_by_name",
        "occurred_at",
    ),
    scope_q=_own_location,
    member_q=_sale_member_q,
    member=lambda record, options: (
        record["source"] == "counter"
        and (
            record["status"] == "open"
            or record["occurred_at"] >= _window(SALE_RETENTION_DAYS)
        )
    ),
    document=lambda record: {
        **_head(record),
        # **The store's discriminator, and it is the device's storage shape
        # rather than a column.** RxDB's open-core build caps a database at
        # thirteen collections and the registry is committed to more streams
        # than that, so a sale and a return share one store on the till exactly
        # as the two stock streams already do -- and the split has to be a field
        # on the row, because that is all `belongsTo` has to read.
        "kind": "sale",
        "location_id": _uuid(record["location_id"]),
        "shift_id": _uuid(record["shift_id"]),
        "number": record["number"],
        "status": record["status"],
        "source": record["source"],
        "customer_id": _uuid(record["customer_id"]),
        "subtotal": _decimal(record["subtotal"]),
        "discount": _decimal(record["discount"]),
        "tax": _decimal(record["tax"]),
        "total": _decimal(record["total"]),
        "sold_by_user_id": _uuid(record["sold_by_user_id"]),
        "sold_by_name": record["sold_by_name"],
        "occurred_at": _iso(record["occurred_at"]),
    },
)

SALE_LINES = Collection(
    name="sale_lines",
    model=SaleLine,
    scope=LOCATION_SCOPED,
    push=True,
    natural_key=None,
    rows_per_location=12600,
    bytes_per_location=2_520_000,
    fields=(
        "id",
        "updated_at",
        "sale_id",
        "location_id",
        "position",
        "item_id",
        "lot_id",
        "quantity",
        "unit_price",
        "discount",
        "vat_class",
        "tax_amount",
        "unit_cost",
        "from_suggestion",
    ),
    scope_q=_own_location,
    member_q=_child_of_sale_member_q,
    member=lambda record, options: _child_in_sale_window(record),
    enrich=lambda records: _mark_parent_window(
        records, "sale_id", Sale, ("occurred_at", "status")
    ),
    document=lambda record: {
        **_head(record),
        "kind": "line",
        "parent_id": _uuid(record["sale_id"]),
        "sale_line_id": None,
        "location_id": _uuid(record["location_id"]),
        "position": record["position"],
        "item_id": _uuid(record["item_id"]),
        "lot_id": _uuid(record["lot_id"]),
        "quantity": record["quantity"],
        "unit_price": _decimal(record["unit_price"]),
        "discount": _decimal(record["discount"]),
        "vat_class": record["vat_class"],
        "tax_amount": _decimal(record["tax_amount"]),
        "unit_cost": _decimal(record["unit_cost"]),
        "from_suggestion": record["from_suggestion"],
    },
)

PAYMENTS = Collection(
    name="payments",
    model=Payment,
    scope=LOCATION_SCOPED,
    push=True,
    natural_key=None,
    rows_per_location=4700,
    bytes_per_location=610_000,
    fields=(
        "id",
        "updated_at",
        "sale_id",
        "location_id",
        "method",
        "amount",
        "reference",
    ),
    scope_q=_own_location,
    member_q=_child_of_sale_member_q,
    member=lambda record, options: _child_in_sale_window(record),
    enrich=lambda records: _mark_parent_window(
        records, "sale_id", Sale, ("occurred_at", "status")
    ),
    document=lambda record: {
        **_head(record),
        "kind": "payment",
        "parent_id": _uuid(record["sale_id"]),
        "sale_line_id": None,
        "location_id": _uuid(record["location_id"]),
        "method": record["method"],
        "amount": _decimal(record["amount"]),
        "reference": record["reference"],
    },
)

SALE_RETURNS = Collection(
    name="sale_returns",
    model=SaleReturn,
    scope=LOCATION_SCOPED,
    push=True,
    natural_key=None,
    rows_per_location=200,
    bytes_per_location=46_000,
    fields=(
        "id",
        "updated_at",
        "sale_id",
        "location_id",
        "shift_id",
        "number",
        "total",
        "tax",
        "reason",
        "refund_method",
        "occurred_at",
    ),
    scope_q=_own_location,
    member_q=lambda options: Q(occurred_at__gte=_window(SALE_RETENTION_DAYS)),
    member=lambda record, options: (
        record["occurred_at"] >= _window(SALE_RETENTION_DAYS)
    ),
    document=lambda record: {
        **_head(record),
        "kind": "return",
        "sale_id": _uuid(record["sale_id"]),
        "location_id": _uuid(record["location_id"]),
        "shift_id": _uuid(record["shift_id"]),
        "number": record["number"],
        "total": _decimal(record["total"]),
        "tax": _decimal(record["tax"]),
        "reason": record["reason"],
        "refund_method": record["refund_method"],
        "occurred_at": _iso(record["occurred_at"]),
    },
)

SALE_RETURN_LINES = Collection(
    name="sale_return_lines",
    model=SaleReturnLine,
    scope=LOCATION_SCOPED,
    push=True,
    natural_key=None,
    rows_per_location=400,
    bytes_per_location=82_000,
    fields=(
        "id",
        "updated_at",
        "sale_return_id",
        "sale_line_id",
        "location_id",
        "item_id",
        "lot_id",
        "quantity",
        "unit_price",
        "discount",
        "vat_class",
        "tax_amount",
        "unit_cost",
    ),
    scope_q=_own_location,
    member_q=lambda options: Q(
        sale_return__occurred_at__gte=_window(SALE_RETENTION_DAYS)
    ),
    member=lambda record, options: (
        (record.get("parent_occurred_at") is not None)
        and record["parent_occurred_at"] >= _window(SALE_RETENTION_DAYS)
    ),
    enrich=lambda records: _mark_parent_window(
        records, "sale_return_id", SaleReturn, ("occurred_at",)
    ),
    document=lambda record: {
        **_head(record),
        "kind": "return_line",
        "parent_id": _uuid(record["sale_return_id"]),
        "sale_line_id": _uuid(record["sale_line_id"]),
        "location_id": _uuid(record["location_id"]),
        "item_id": _uuid(record["item_id"]),
        "lot_id": _uuid(record["lot_id"]),
        "quantity": record["quantity"],
        "unit_price": _decimal(record["unit_price"]),
        "discount": _decimal(record["discount"]),
        "vat_class": record["vat_class"],
        "tax_amount": _decimal(record["tax_amount"]),
        "unit_cost": _decimal(record["unit_cost"]),
    },
)


def _never(*, tenant_id, location_id, options):
    """The scan a push-only collection would run, if anything ever ran it.

    Nothing does: `pullable` refuses these two by name. It is stated rather than
    left as `None` so that a future reader who wires one into a pull gets an
    empty page instead of an exception on a hot path.
    """
    del tenant_id, location_id, options
    return Q(pk__in=[])


RECEIPT_LINES = Collection(
    name="receipt_lines",
    model=StockMove,
    scope=LOCATION_SCOPED,
    push=True,
    pull=False,
    # Rule 8's first form. `stock_moves` carries the client-write quartet, so
    # `(tenant_id, client_uuid)` is the whole of deduplication -- but the writer
    # is S3's, because a receipt line creates or matches a `lots` row and
    # appends through the ledger service rather than inserting a payload.
    natural_key=None,
    rows_per_location=0,
    bytes_per_location=0,
    fields=("id", "updated_at"),
    scope_q=_never,
    member_q=lambda options: Q(),
    member=lambda record, options: False,
    document=lambda record: _head(record),
)

COUNT_LINES = Collection(
    name="stock_count_lines",
    model=StockCountLine,
    scope=LOCATION_SCOPED,
    push=True,
    pull=False,
    natural_key=None,
    rows_per_location=0,
    bytes_per_location=0,
    fields=("id", "updated_at"),
    scope_q=_never,
    member_q=lambda options: Q(),
    member=lambda record, options: False,
    document=lambda record: _head(record),
)


# ---------------------------------------------------------------------------
# S8's amendment · two collections the till reads, two it only writes
#
# **The safety layer and the mined rules go down to the till, and the offers and
# acceptances come up** (rule 9, A8). That is the whole of what makes the
# assistant work with the fibre cut: the filter runs against `item_warnings`
# that are already on the device, the `Se lleva junto` card is a
# `cross_sell_rules` row that is already on the device, and the offer rows are
# written locally and queued.
#
# **`item_warnings` is never the lever on the disk budget.** S2 measures under
# 12 MB for a first sync and S3, S4 and S8 all draw on that ceiling; if the
# total is over, `cross_sell_rules_per_item` is a setting and degrades the
# breadth of `bought_together` suggestions. A blackout must remove the network,
# not the safety layer (A8, ledger disputed columns).
# ---------------------------------------------------------------------------


def _rule_scope(*, tenant_id, location_id, options):
    """This sede's rules and the network-wide ones, and no other sede's.

    The same two-branch shape `item_prices` takes, and for the same reason:
    `pull.py` runs the tuple scan once per branch so both stay on
    `(tenant_id, location_id, updated_at, id)` rather than forcing a bitmap
    union that loses the ordering the cursor needs.
    """
    del options
    return Q(tenant_id=tenant_id) & (
        Q(location_id=location_id) | Q(location_id__isnull=True)
    )


def _borrowed(options, key) -> int:
    """One of the two `assistant` keys this file's membership rule reads.

    `sync_settings.options()` copies them in beside S2's own group, and that is
    the map every request-path caller passes. A caller holding only S2's group
    -- a job, a check, a test reaching for `sync_settings.DEFAULTS` -- gets the
    same answer here rather than a `KeyError` from inside a predicate, because
    the floor a rule has to clear is a number this stage publishes a default
    for, not something the sync group is entitled to be missing.
    """
    from core.assistant import settings as assistant_settings

    return int(options.get(key, assistant_settings.DEFAULTS[key]))


def _rule_member(record, options) -> bool:
    """**The cap is enforced by the job that writes the rows, not here.**

    This predicate is the registry's own floor -- a rule below the support floor
    or outside the per-anchor cap is not a rule a till should be carrying -- and
    it is evaluated over the returned page rather than in the scan, exactly as
    every other membership rule in this file is.
    """
    return record["support"] >= _borrowed(options, "cross_sell_min_support") and record[
        "rank"
    ] <= _borrowed(options, "cross_sell_rules_per_item")


def _rule_member_q(options):
    return Q(support__gte=_borrowed(options, "cross_sell_min_support")) & Q(
        rank__lte=_borrowed(options, "cross_sell_rules_per_item")
    )


CROSS_SELL_RULES = Collection(
    name="cross_sell_rules",
    model=CrossSellRule,
    scope=LOCATION_SCOPED,
    push=False,
    natural_key=None,
    #: The pilot's own sizing: roughly 1.600 items clear the support floor at
    #: four rules each, per scope. **Not the seed's** -- the demo catalog is
    #: smaller and a check asserting this against a seeded tenant fails on every
    #: run.
    rows_per_location=6400,
    bytes_per_location=800_000,
    fields=(
        "id",
        "updated_at",
        "location_id",
        "item_a_id",
        "item_b_id",
        "support",
        "confidence",
        "lift",
        "rank",
        "confidence_band",
        "computed_at",
    ),
    scope_q=_rule_scope,
    member_q=_rule_member_q,
    member=_rule_member,
    document=lambda record: {
        **_head(record),
        "kind": "rule",
        "location_id": _uuid(record["location_id"]),
        "item_id": _uuid(record["item_a_id"]),
        "item_b_id": _uuid(record["item_b_id"]),
        "support": record["support"],
        "confidence": _decimal(record["confidence"]),
        "lift": _decimal(record["lift"]),
        "rank": record["rank"],
        # **The one of the three provenance columns the device actually reads**,
        # because it selects which form of the `bought_together_location` reason
        # line renders. `basis` and `ticket_count` are Ajustes' and Ajustes is
        # online-only, so neither crosses the wire.
        "confidence_band": record["confidence_band"],
        # **The one staleness figure this stage puts on a till.** The mined
        # rules can be a week old and the percentage inside a `Se lleva junto`
        # reason is a figure from that run, so the sync panel states their
        # freshness **once** -- `Reglas del asistente · hace 3 días` -- exactly
        # as §B.9.2 states the price list's freshness once rather than on every
        # ticket line. Forty dots on a counter screen is the alarm fatigue that
        # convention exists to prevent.
        "computed_at": _iso(record["computed_at"]),
    },
)

ITEM_WARNINGS = Collection(
    name="item_warnings",
    model=ItemWarning,
    scope=TENANT_WIDE,
    push=False,
    natural_key=None,
    rows_per_location=900,
    bytes_per_location=270_000,
    fields=(
        "id",
        "updated_at",
        "item_id",
        "type",
        "text",
        "severity",
        "triggers",
        "active",
    ),
    scope_q=_tenant_scope,
    member_q=lambda options: Q(active=True),
    # A deactivated warning is served **with a deletion marker** and leaves every
    # till within one pull interval, which is why this stage hard-deletes
    # nothing (S2, criterion 14).
    member=lambda record, options: bool(record["active"]),
    document=lambda record: {
        **_head(record),
        "kind": "warning",
        "item_id": _uuid(record["item_id"]),
        "type": record["type"],
        "text": record["text"],
        "severity": record["severity"],
        "triggers": record["triggers"],
    },
)

ASSISTANT_QUERIES = Collection(
    name="assistant_queries",
    model=AssistantQuery,
    scope=LOCATION_SCOPED,
    push=True,
    pull=False,
    # Rule 8's first form on the row, and S4's second form on the wire: the
    # envelope key identifies the **event** -- the offer, the attach, the
    # supersede -- and the payload's `client_uuid` identifies the row they all
    # converge on.
    natural_key=None,
    rows_per_location=0,
    bytes_per_location=0,
    fields=("id", "updated_at"),
    scope_q=_never,
    member_q=lambda options: Q(),
    member=lambda record, options: False,
    document=lambda record: _head(record),
)

ASSISTANT_SUGGESTIONS = Collection(
    name="assistant_suggestions",
    model=AssistantSuggestion,
    scope=LOCATION_SCOPED,
    push=True,
    pull=False,
    natural_key=None,
    rows_per_location=0,
    bytes_per_location=0,
    fields=("id", "updated_at"),
    scope_q=_never,
    member_q=lambda options: Q(),
    member=lambda record, options: False,
    document=lambda record: _head(record),
)


#: **Version 4.** Ordered as a first sync should run: the catalog before the
#: prices that reference it, the lots before the stock that references them, and
#: S4's six last of all -- the shift before the sales that sit in it, the sale
#: before its lines and its payments, the return before its lines -- so a
#: half-synced till never renders a price with no product behind it, a quantity
#: with no expiry date, or a ticket line with no ticket.
COLLECTIONS: tuple[Collection, ...] = (
    ITEMS,
    BARCODES,
    MANUFACTURERS,
    CATEGORIES,
    PRICES,
    CUSTOMERS,
    LOTS,
    STOCK_ON_HAND,
    STOCK_ELSEWHERE,
    POLICIES,
    SHIFTS,
    SALES,
    SALE_LINES,
    PAYMENTS,
    SALE_RETURNS,
    SALE_RETURN_LINES,
    # S8's two, after the items and the sedes' stock they are about: a warning
    # with no product behind it filters nothing, and a rule naming two items the
    # till does not hold yet ranks nothing.
    CROSS_SELL_RULES,
    ITEM_WARNINGS,
)

#: Collections a device only ever writes. They are **not** in `COLLECTIONS`,
#: which is what keeps them out of the digest, out of the first-sync card's
#: totals and out of the pull -- each of those three asks a question about a
#: snapshot, and an event log is not one.
PUSH_ONLY: tuple[Collection, ...] = (
    RECEIPT_LINES,
    COUNT_LINES,
    ASSISTANT_QUERIES,
    ASSISTANT_SUGGESTIONS,
)

BY_NAME: dict[str, Collection] = {one.name: one for one in (*COLLECTIONS, *PUSH_ONLY)}

#: The tables a device may write, as a set, for the push endpoint's own guard.
PUSHABLE = frozenset(one.name for one in BY_NAME.values() if one.push)


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


def pullable(name: str) -> Collection:
    """One collection a device may read, or the refusal that names the register.

    A push-only collection is refused here rather than served empty: a client
    asking for a page of `receipt_lines` is a client that misread the registry,
    and an empty page would let it advance a cursor forever over nothing.
    """
    collection = get(name)
    if not collection.pull:
        raise LookupError(
            f"{name!r} is a write-only collection: a device sends it and never "
            "reads it back. The pullable collections are: "
            + ", ".join(one.name for one in COLLECTIONS)
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
