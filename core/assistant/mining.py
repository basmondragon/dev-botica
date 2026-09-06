"""The cross-sell miner, stated so a rule can be argued with.

**A ticket** is one `sales` row at `status = 'closed'` with at least two
`sale_lines` over **distinct items**, at `source IN ('counter', 'imported')`,
inside `cross_sell_window_days` measured on `recorded_at` (rule 8: `recorded_at`
for every report, `occurred_at` never).

  `support`     the number of tickets containing both items
  `confidence`  P(B present | A present)
  `lift`        `confidence` over the share of all tickets containing B

**Pairs are directed.** A→B and B→A are two rows with different confidences,
because the anchor is what the customer is being sold and the suggestion is what
goes with it -- averaging the two directions loses exactly the asymmetry the
assistant uses. An item is never paired with itself, a returned line is excluded
from both sides, and a voided sale is excluded entirely.

**`lift`, and why `confidence` alone is not enough.** Confidence is P(B|A), and
a product almost every ticket carries scores high against everything. Ranking on
lift is what stops every suggestion card in the product being the same three
fast movers -- otherwise the assistant recommends bolsas and acetaminofén to
everyone, the acceptance rate looks respectable because those sell anyway, and
the tile measures nothing.

**Two scopes run per refresh**: one per `location_id`, and one network-wide at
`location_id IS NULL`. The till holds both and the ranker prefers the sede row
where one exists, which is what makes *"en este punto el 64%"* a true sentence.

**A run that finds nothing above the floor is a pass, not a failure.** On a
tenant that has never sold anything the miner must execute, write no row, move
no `computed_at` and exit zero. A job that only starts once there is history is
a job nobody remembers to start (§1, *Cold start*).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.assistant import settings as assistant_settings
from core.models import (
    CrossSellBasis,
    CrossSellConfidence,
    CrossSellRule,
    Location,
    LocationStatus,
    Sale,
    SaleLine,
    SaleReturnLine,
    SaleSource,
    SaleStatus,
)

logger = logging.getLogger(__name__)

#: Bumped when the counting changes. It records **only the algorithm**; which
#: sale population a run consumed is `basis`, on its own column.
ALGORITHM_VERSION = "cross-sell-1"

#: The bands, derived at write time from the pair's own support and the window's
#: own denominator and **never entered by hand**. A solid-looking ratio out of a
#: window that barely exists is still `low`.
MEDIUM_SUPPORT = 100
HIGH_SUPPORT = 500
MEDIUM_TICKETS = 2_000
HIGH_TICKETS = 10_000

#: How many sales' lines are fetched at once. Sized so the widest chunk -- one
#: placeholder per sale, then one per line those sales hold -- stays an order of
#: magnitude under Postgres' 65.535-parameter ceiling on the busiest ticket the
#: counter can write.
_CHUNK = 2_000


@dataclass
class Report:
    """What one refresh did, per scope, for the job's log and for Ajustes."""

    scopes: int = 0
    written: int = 0
    tickets: int = 0
    pairs_considered: int = 0
    basis: str = ""

    def as_dict(self) -> dict:
        return {
            "scopes": self.scopes,
            "written": self.written,
            "tickets": self.tickets,
            "pairs_considered": self.pairs_considered,
            "basis": self.basis,
        }


def band(support: int, ticket_count: int) -> str:
    """`confidence_band`, and it is a function of two numbers and nothing else."""
    if support >= HIGH_SUPPORT and ticket_count >= HIGH_TICKETS:
        return CrossSellConfidence.HIGH
    if MEDIUM_SUPPORT <= support < HIGH_SUPPORT and ticket_count >= MEDIUM_TICKETS:
        return CrossSellConfidence.MEDIUM
    if support >= HIGH_SUPPORT and ticket_count >= MEDIUM_TICKETS:
        return CrossSellConfidence.MEDIUM
    return CrossSellConfidence.LOW


def refresh(tenant, *, now=None) -> Report:
    """Mine every scope for one tenant: each active sede, then the network.

    Each scope is written in **one transaction**, so a till never holds half a
    refresh, and a scope that produced nothing leaves the previous rows and
    their `computed_at` exactly as they were.
    """
    settings = assistant_settings.read(tenant)
    at = now or timezone.now()
    window_days = int(settings.get("cross_sell_window_days", 90))
    opened = at - timedelta(days=window_days)
    tenant_id = getattr(tenant, "id", tenant)

    report = Report()
    # The network pass is last and is written at `location_id IS NULL`.
    for location_id in [*scopes_of(tenant_id), None]:
        one = refresh_scope(
            tenant_id=tenant_id,
            location_id=location_id,
            settings=settings,
            opened=opened,
            at=at,
            window_days=window_days,
        )
        report.scopes += 1
        report.written += one.written
        report.pairs_considered += one.pairs_considered
        report.basis = report.basis or one.basis
        if location_id is None:
            report.tickets = one.tickets
    logger.info("cross-sell refresh for %s: %s", tenant_id, report.as_dict())
    return report


def scopes_of(tenant_id) -> list:
    return list(
        Location.objects.filter(
            tenant_id=tenant_id, status=LocationStatus.ACTIVE
        ).values_list("id", flat=True)
    )


def refresh_scope(
    *, tenant_id, location_id, settings=None, opened=None, at=None, window_days=None
) -> Report:
    """One scope, loaded and mined on its own.

    **The fan-out is per scope and not per tenant**, so a sede whose window is a
    thousand tickets does not carry the network's whole basket through memory --
    and a scope that fails leaves every other scope's rules exactly where they
    were.
    """
    at = at or timezone.now()
    if settings is None:
        from core.models import Tenant

        settings = assistant_settings.read(Tenant.objects.get(id=tenant_id))
    window_days = window_days or int(settings.get("cross_sell_window_days", 90))
    opened = opened or (at - timedelta(days=window_days))

    tickets = _tickets(tenant_id, opened, at, location_id)
    basis = _basis(tickets)
    written, counted, considered = _mine_scope(
        tenant_id=tenant_id,
        location_id=location_id,
        tickets=tickets,
        settings=settings,
        window_days=window_days,
        at=at,
        basis=basis,
    )
    return Report(
        scopes=1,
        written=written,
        tickets=counted,
        pairs_considered=considered,
        basis=basis,
    )


def _tickets(tenant_id, opened, closed_at, location_id=None):
    """`{sale_id: (location_id, source, frozenset(item_ids))}` for the window.

    Read as the window's sales, then their lines a chunk at a time, and joined
    in Python. **The alternative is a self-join over `sale_lines` per pair**,
    and this job runs weekly over a window a pilot's whole network fits in
    memory ten times over.
    """
    window = {
        "status": SaleStatus.CLOSED,
        "source__in": (SaleSource.COUNTER, SaleSource.IMPORTED),
        "recorded_at__gte": opened,
        "recorded_at__lt": closed_at,
    }
    if location_id is not None:
        window["location_id"] = location_id
    sales = {
        str(row["id"]): (str(row["location_id"]), row["source"])
        for row in Sale.objects.filter(tenant_id=tenant_id, **window)
        # `-recorded_at` would sort the whole window to fill a dictionary.
        .order_by()
        .values("id", "location_id", "source")
    }
    if not sales:
        return {}

    # **The children are read a chunk of parents at a time**, by id, against
    # `sale_lines_by_sale` and `return_lines_by_original_line`.
    #
    # Neither of the two obvious alternatives survives contact with a real
    # window. One `sale_id__in=[...]` holding every id binds one placeholder
    # each, and past 65.535 of them Postgres refuses the statement outright --
    # a job that works on a seed and dies on the network it was written for.
    # Re-selecting the children by the parent's own predicate instead, as a
    # join, removes the ceiling and replaces it with a worse failure: the plan
    # is then only as good as the statistics, and inside a transaction that
    # has just inserted every row -- a seed run, a test -- there are none, so
    # the planner reads both sides as empty and nested-loops a scan of
    # `sale_lines` per sale. Measured on the `default` seed: eighty-three
    # seconds for **one** scope, against 0,32 s for all seven the way it reads
    # now.
    #
    # Chunking is the shape that has neither failure. The parameter count is
    # bounded by the chunk, and every lookup is an index range on a column the
    # ledger already indexes, whatever the planner believes about row counts.
    baskets: dict[str, set[str]] = defaultdict(set)
    sale_ids = list(sales)
    for start in range(0, len(sale_ids), _CHUNK):
        chunk = sale_ids[start : start + _CHUNK]
        lines = list(
            SaleLine.objects.filter(tenant_id=tenant_id, sale_id__in=chunk)
            # The model orders by `position` and nothing here reads it; the sort
            # would be over the whole window for no one.
            .order_by()
            .values("id", "sale_id", "item_id", "quantity")
        )
        # **A returned line is excluded from both sides.** A line the customer
        # brought back is not evidence that the two products go together, and
        # the returned quantity is summed because a partial return leaves a line
        # that still sold something.
        returned: dict[str, int] = defaultdict(int)
        for credit in (
            SaleReturnLine.objects.filter(
                tenant_id=tenant_id,
                sale_line_id__in=[row["id"] for row in lines],
            )
            .order_by()
            .values("sale_line_id", "quantity")
        ):
            returned[str(credit["sale_line_id"])] += int(credit["quantity"] or 0)

        for line in lines:
            line_id = str(line["id"])
            if returned.get(line_id, 0) >= int(line["quantity"] or 0):
                continue
            baskets[str(line["sale_id"])].add(str(line["item_id"]))

    return {
        sale_id: (sales[sale_id][0], sales[sale_id][1], frozenset(items))
        # **At least two distinct items.** A single-line ticket carries no pair
        # and a legacy export that recorded one line per ticket produces none at
        # all -- which the miner reports as a support floor nothing clears
        # rather than as a broken job.
        for sale_id, items in baskets.items()
        if len(items) >= 2
    }


def _basis(tickets) -> str:
    sources = {source for _location, source, _items in tickets.values()}
    if sources == {SaleSource.IMPORTED}:
        return CrossSellBasis.IMPORTED
    if SaleSource.IMPORTED in sources and SaleSource.COUNTER in sources:
        return CrossSellBasis.MIXED
    return CrossSellBasis.COUNTER


def _mine_scope(*, tenant_id, location_id, tickets, settings, window_days, at, basis):
    """One scope's rules, written whole or not at all."""
    baskets = [items for _location, _source, items in tickets.values()]
    ticket_count = len(baskets)
    min_support = int(settings.get("cross_sell_min_support", 25))
    min_confidence = assistant_settings.min_confidence(settings)
    per_item = int(settings.get("cross_sell_rules_per_item", 4))

    if ticket_count == 0:
        return 0, 0, 0

    singles: dict[str, int] = defaultdict(int)
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for items in baskets:
        ordered = sorted(items)
        for item in ordered:
            singles[item] += 1
        for first in ordered:
            for second in ordered:
                if first != second:
                    pairs[(first, second)] += 1

    considered = len(pairs)
    by_anchor: dict[str, list[dict]] = defaultdict(list)
    for (anchor, partner), support in pairs.items():
        if support < min_support:
            continue
        confidence = Decimal(support) / Decimal(singles[anchor])
        if confidence < min_confidence:
            continue
        partner_share = Decimal(singles[partner]) / Decimal(ticket_count)
        if partner_share <= 0:
            continue
        by_anchor[anchor].append(
            {
                "item_a_id": anchor,
                "item_b_id": partner,
                "support": support,
                "confidence": confidence.quantize(Decimal("0.0001")),
                "lift": (confidence / partner_share).quantize(Decimal("0.0001")),
            }
        )

    rows = []
    for anchor, found in by_anchor.items():
        del anchor
        # **The cap is enforced by the job that writes the rows, not by the
        # predicate that reads them** -- a cap enforced by the reader is a hope,
        # because the table it is reading has already been written. The trailing
        # id keeps two runs over one window in one order (criterion 20).
        found.sort(key=lambda one: (-one["lift"], -one["support"], one["item_b_id"]))
        for position, row in enumerate(found[:per_item], start=1):
            rows.append({**row, "rank": position})

    if not rows:
        # A run that finds nothing writes nothing and **moves no
        # `computed_at`**: the tills keep the rules they have and the panel
        # keeps showing the older date. It never shows a fresh timestamp over
        # stale rules.
        return 0, ticket_count, considered

    window = f"{window_days}d"
    _publish(
        tenant_id=tenant_id,
        location_id=location_id,
        rows=rows,
        window=window,
        at=at,
        basis=basis,
        ticket_count=ticket_count,
    )
    return len(rows), ticket_count, considered


def _publish(*, tenant_id, location_id, rows, window, at, basis, ticket_count):
    """Write one scope's rules **whole, and without deleting a registry row.**

    `cross_sell_rules` is a collection every till pulls, and S2's rule is that a
    row a device holds is never hard-deleted: a deleted row leaves nothing to
    serve a departure marker from and no `updated_at` to serve it at, so the
    till keeps it forever. A pair that has stopped clearing the floor therefore
    has its `support` **zeroed in place** -- which is below any floor, which is
    what the registry's own membership predicate reads, and which is what turns
    it into a departure on the next pull.

    The whole scope is one transaction, so a till never holds half a refresh.
    """
    wanted = {(row["item_a_id"], row["item_b_id"]): row for row in rows}
    now = timezone.now()
    with transaction.atomic():
        held = {
            (str(one.item_a_id), str(one.item_b_id)): one
            for one in CrossSellRule.objects.select_for_update().filter(
                tenant_id=tenant_id, location_id=location_id
            )
        }
        fresh, changed, departed = [], [], []
        for key, row in wanted.items():
            values = dict(
                support=row["support"],
                confidence=row["confidence"],
                lift=row["lift"],
                rank=row["rank"],
                window=window,
                computed_at=at,
                algorithm_version=ALGORITHM_VERSION,
                basis=basis,
                ticket_count=ticket_count,
                confidence_band=band(row["support"], ticket_count),
            )
            existing = held.get(key)
            if existing is None:
                fresh.append(
                    CrossSellRule(
                        tenant_id=tenant_id,
                        location_id=location_id,
                        item_a_id=row["item_a_id"],
                        item_b_id=row["item_b_id"],
                        **values,
                    )
                )
                continue
            # **A rule whose figures did not move is not rewritten.** Its
            # `updated_at` is the delta cursor, and touching it would re-serve
            # the whole rule set to every till in the network every Sunday for
            # nothing -- 0,8 MB a week per till to say the same thing again.
            if all(
                getattr(existing, field) == value
                for field, value in values.items()
                if field != "computed_at"
            ):
                continue
            for field, value in values.items():
                setattr(existing, field, value)
            # `bulk_update` does not fire `auto_now`, and the delta cursor is
            # this column: a rule whose figures moved and whose `updated_at` did
            # not is a rule no till ever hears about again.
            existing.updated_at = now
            changed.append(existing)
        for key, existing in held.items():
            if key in wanted or existing.support == 0:
                continue
            existing.support = 0
            existing.updated_at = now
            departed.append(existing)

        CrossSellRule.objects.bulk_create(fresh, batch_size=1000)
        if changed or departed:
            CrossSellRule.objects.bulk_update(
                [*changed, *departed],
                [
                    "support",
                    "confidence",
                    "lift",
                    "rank",
                    "window",
                    "computed_at",
                    "algorithm_version",
                    "basis",
                    "ticket_count",
                    "confidence_band",
                    "updated_at",
                ],
                batch_size=1000,
            )
