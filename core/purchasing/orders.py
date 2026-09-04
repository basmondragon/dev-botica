"""Generating, editing, approving and dispatching a purchase order.

**Two columns, never one.** `suggested_quantity` is written once, here, at
generation; every later write in this module touches `approved_quantity` and
nothing else. The difference between them is the only honest measure the product
will ever have of whether the model is trusted, and overwriting the proposal
destroys that measurement permanently and irrecoverably (§3, ledger).

**A line is a decision, not a row of the catalog.** The order carries the
references the model proposes a quantity for, plus the references whose *zero*
is itself a finding -- a shelf at or below the threshold its own pharmacist set
whose shortfall is already on its way, capital tied up in ninety days of cover,
a shelf concentrated in a short-dated lot. A reference with comfortable cover and
nothing remarkable about it is not a line: an order of 1.184 rows reading *no
pedir* is the catalog with a total at the bottom.

*Stated assumption.* S6-purchasing.md's `cold`-profile check counts the
generated order's lines against every `manual` `stock_policies` row at the sede.
This module proposes a parametric quantity **only where the shelf has fallen to
the threshold that row carries** -- `Sin histórico · sugerido por el punto de
reorden de la sede` is what the line says, and a reference sitting comfortably
above the reorder point its own pharmacist set is not one that pharmacist wants
ordered. The substance that check is about is unchanged and is asserted in the
suite: only references carrying a manual row appear, `parametric` is the only
basis on that tenant, and a reference with neither history nor a policy row is
absent from the order entirely.
"""

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from core.inventory import settings as inventory_settings
from core.models import (
    ForecastBasis,
    Item,
    Location,
    PolicySource,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSource,
    PurchaseOrderStatus,
    StockOnHand,
    Tenant,
)
from core.purchasing import forecast, reasons
from core.purchasing import settings as purchasing_settings

logger = logging.getLogger(__name__)

#: The statuses whose outstanding lines count as `on_order`, so two consecutive
#: mornings do not order the same shortfall twice.
OPEN_STATUSES = (PurchaseOrderStatus.APPROVED, PurchaseOrderStatus.SENT)

#: The statuses a receipt may be opened against.
RECEIVABLE_STATUSES = (
    PurchaseOrderStatus.SENT,
    PurchaseOrderStatus.PARTIALLY_RECEIVED,
)

#: How much of a sede's stock of a reference must sit in one lot before that
#: lot's expiry is the thing to say about the reference.
LOT_CONCENTRATION = Decimal("0.5")

#: What makes a pair worth naming: this many shared tickets, and this share of
#: the reference's own. A pair seen twice is a coincidence.
CROSS_SELL_MIN_TICKETS = 12
CROSS_SELL_MIN_SHARE = Decimal("0.2")

#: How many zero-quantity findings one order carries beside its purchases, and
#: how few lines it takes to earn one. **A finding rides along beside a purchase,
#: never instead of one**: an order half of whose rows read `Sobrestock` is a
#: report with a total at the bottom, and the buyer opened it to buy something.
#: One finding per three purchased lines, a floor of two so the state is always
#: visible, and a ceiling of twelve because the thirteenth is on tomorrow's
#: order if it still matters.
FINDINGS_PER_ORDER = 12
FINDINGS_FLOOR = 2
LINES_PER_FINDING = 3

#: How many candidate references the co-occurrence query is run over. The
#: self-join is bounded by the order's own candidates and by this, because it is
#: the one quadratic read in the stage and the code it feeds is the last in
#: precedence -- it may cost a little, never a lot.
CROSS_SELL_CANDIDATES = 400


class Refused(ValueError):
    """An order operation this module will not perform, named in Spanish."""


@dataclass
class Proposal:
    """One candidate line, before it becomes a row."""

    item_id: object
    quantity: int
    basis: str
    confidence: Decimal | None
    coverage_days: Decimal | None
    reason_code: str
    reason_fallback: str
    unit_cost: Decimal | None
    supplier_id: object
    #: What ranks it when the order's value cap has to trim something.
    urgency: Decimal
    #: The capital standing on the shelf behind it, which is what ranks a
    #: zero-quantity finding against the other findings competing for the
    #: order's attention.
    material: Decimal = Decimal("0")


def lock_location(tenant_id, location_id):
    """The per-`(tenant, location)` advisory lock the number allocator takes.

    `purchase_orders.number` is a per-location consecutive and there is no
    sequence behind it, so two generation runs at one sede must not read the
    same maximum. The lock is transaction-scoped and is the same shape S3's
    ledger uses, under its own namespace so the two never contend.
    """
    token = hashlib.blake2b(
        f"purchasing:{tenant_id}:{location_id}".encode("utf-8"), digest_size=8
    ).digest()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [int.from_bytes(token, "big", signed=True)],
        )


def next_number(tenant_id, location_id) -> int:
    """The next consecutive at one sede. Called under the lock above."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT coalesce(max(number), 0) + 1 FROM purchase_orders "
            "WHERE tenant_id = %s AND location_id = %s",
            [str(tenant_id), str(location_id)],
        )
        return int(cursor.fetchone()[0])


# ---------------------------------------------------------------------------
# What the sede already has coming, and what other sedes hold
# ---------------------------------------------------------------------------


def on_order(tenant_id, location_id) -> dict:
    """`item_id -> units outstanding on an approved or sent order`.

    Outstanding, not ordered: a line 180 of whose 200 units arrived has 20 still
    coming, and counting the whole 200 would leave the shortfall unordered for
    as long as the order stayed open.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT l.item_id,
                   sum(greatest(l.approved_quantity - l.received_quantity, 0))::int
            FROM purchase_order_lines l
            JOIN purchase_orders o ON o.id = l.purchase_order_id
            WHERE l.tenant_id = %s AND o.location_id = %s
              AND o.status = ANY(%s::purchase_order_status[])
            GROUP BY 1
            """,
            [str(tenant_id), str(location_id), [str(one) for one in OPEN_STATUSES]],
        )
        return {row[0]: int(row[1] or 0) for row in cursor.fetchall()}


def elsewhere(tenant_id, location_id, item_ids) -> dict:
    """`item_id -> (units, sede name)` at whichever other sede holds the most.

    The figure behind `En quiebre, hay 96 en Suba`. **It carries no staleness
    marker**: §B.9.2's marker marks a figure a till read from its local store,
    and every surface in this stage is server-authoritative and online-only.
    """
    if not item_ids:
        return {}
    rows = (
        StockOnHand.objects.filter(
            tenant_id=tenant_id, item_id__in=list(item_ids), quantity__gt=0
        )
        .exclude(location_id=location_id)
        .values_list("item_id", "location__name", "quantity")
    )
    held: dict = {}
    for item_id, name, quantity in rows:
        total = held.setdefault(item_id, {})
        total[name] = total.get(name, 0) + int(quantity)
    answer = {}
    for item_id, by_sede in held.items():
        # Most units first, ties broken by name -- the same rule S3's own
        # availability clause uses, so the two screens never name different
        # sedes for the same reference.
        name, quantity = sorted(by_sede.items(), key=lambda one: (-one[1], one[0]))[0]
        answer[item_id] = (quantity, name)
    return answer


def expiring_lots(tenant_id, location_id, item_ids, *, horizon_days, today) -> dict:
    """`item_id -> months to expiry`, where the shelf sits in one short-dated lot.

    S3's `expiry_alert_days` is the horizon, read from S3's own settings group
    rather than restated here: a horizon written into this module is a horizon a
    pilot cannot move.
    """
    if not item_ids:
        return {}
    rows = (
        StockOnHand.objects.filter(
            tenant_id=tenant_id,
            location_id=location_id,
            item_id__in=list(item_ids),
            quantity__gt=0,
        )
        .select_related("lot")
        .values_list("item_id", "lot__expires_at", "quantity")
    )
    totals: dict = {}
    dated: dict = {}
    for item_id, expires_at, quantity in rows:
        totals[item_id] = totals.get(item_id, 0) + int(quantity)
        if expires_at is None:
            continue
        held = dated.get(item_id)
        if held is None or int(quantity) > held[1]:
            dated[item_id] = (expires_at, int(quantity))

    limit = today + timedelta(days=int(horizon_days))
    answer = {}
    for item_id, (expires_at, quantity) in dated.items():
        total = totals.get(item_id, 0)
        if total <= 0 or expires_at > limit:
            continue
        if Decimal(quantity) / Decimal(total) < LOT_CONCENTRATION:
            continue
        answer[item_id] = max(1, round((expires_at - today).days / 30))
    return answer


def cross_sell_partners(tenant_id, location_id, item_ids, start) -> dict:
    """`item_id -> the reference it most often leaves the counter with`.

    One self-join over `sale_lines`, which is exactly what this stage's own
    `(tenant_id, item_id, sale_id)` index was created for. It is bounded by the
    order's own candidate set, because it is the one quadratic read here and it
    feeds the last code in precedence.
    """
    items = list(item_ids)[:CROSS_SELL_CANDIDATES]
    if len(items) < 2:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            WITH lines AS (
                SELECT sl.item_id, sl.sale_id
                FROM sale_lines sl
                JOIN sales s ON s.id = sl.sale_id
                WHERE sl.tenant_id = %s
                  AND sl.location_id = %s
                  AND s.status = 'closed'
                  AND s.source IN ('counter', 'imported')
                  AND s.occurred_at >= %s
            ),
            totals AS (SELECT item_id, count(*)::int AS tickets FROM lines GROUP BY 1)
            SELECT a.item_id, b.item_id, count(*)::int, t.tickets
            FROM lines a
            JOIN lines b ON b.sale_id = a.sale_id AND b.item_id <> a.item_id
            JOIN totals t ON t.item_id = a.item_id
            WHERE a.item_id = ANY(%s)
            GROUP BY 1, 2, 4
            HAVING count(*) >= %s
            """,
            [
                str(tenant_id),
                str(location_id),
                start,
                [str(one) for one in items],
                CROSS_SELL_MIN_TICKETS,
            ],
        )
        best: dict = {}
        for item_id, partner_id, shared, tickets in cursor.fetchall():
            if not tickets or Decimal(shared) / Decimal(tickets) < CROSS_SELL_MIN_SHARE:
                continue
            held = best.get(item_id)
            if held is None or shared > held[1]:
                best[item_id] = (partner_id, shared)
    if not best:
        return {}
    names = dict(
        Item.objects.filter(
            tenant_id=tenant_id, id__in=[one for one, _ in best.values()]
        ).values_list("id", "name")
    )
    return {
        item_id: names[partner_id]
        for item_id, (partner_id, _shared) in best.items()
        if partner_id in names
    }


def recent_stockout(tenant_id, location_id, item_ids, today) -> set:
    """The references whose shelf hit zero inside the trailing four weeks.

    Read off `stock_on_hand` for the ones at zero now and off the ledger for the
    ones that recovered, because a quiebre a transfer fixed on Tuesday is still
    the reason the model is buying more of it on Friday.
    """
    if not item_ids:
        return set()
    since = today - timedelta(weeks=4)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT c.item_id FROM (
                SELECT m.item_id,
                       sum(m.quantity) OVER (
                           PARTITION BY m.item_id ORDER BY m.recorded_at, m.id
                       ) AS running,
                       (SELECT coalesce(sum(h.quantity), 0) FROM stock_on_hand h
                        WHERE h.tenant_id = m.tenant_id
                          AND h.location_id = m.location_id
                          AND h.item_id = m.item_id) AS held,
                       (SELECT coalesce(sum(n.quantity), 0) FROM stock_moves n
                        WHERE n.tenant_id = m.tenant_id
                          AND n.location_id = m.location_id
                          AND n.item_id = m.item_id
                          AND n.recorded_at >= %s) AS moved
                FROM stock_moves m
                WHERE m.tenant_id = %s AND m.location_id = %s
                  AND m.item_id = ANY(%s) AND m.recorded_at >= %s
            ) c
            WHERE c.held - c.moved + c.running <= 0
            """,
            [
                since,
                str(tenant_id),
                str(location_id),
                [str(one) for one in item_ids],
                since,
            ],
        )
        return {row[0] for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _supplier_links(tenant_id, item_ids) -> dict:
    """`item_id -> (supplier_id, cost per base unit, min_order_pack, units_per_pack)`.

    One supplier per reference: the preferred link if a pharmacist named one,
    otherwise the cheapest, ties broken by supplier name so an order's grouping
    does not depend on the ordering of a query. A reference with no supplier
    link cannot be ordered at all -- there is nobody to send it to -- and it is
    absent rather than grouped under a guess.
    """
    if not item_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (si.item_id)
                   si.item_id, si.supplier_id, si.cost, si.min_order_pack,
                   i.units_per_pack
            FROM supplier_items si
            JOIN suppliers s ON s.id = si.supplier_id
            JOIN items i ON i.id = si.item_id
            WHERE si.tenant_id = %s AND si.item_id = ANY(%s)
            ORDER BY si.item_id, si.is_preferred DESC,
                     si.cost NULLS LAST, s.name
            """,
            [str(tenant_id), [str(one) for one in item_ids]],
        )
        links = {}
        for item_id, supplier_id, cost, min_pack, pack in cursor.fetchall():
            pack = max(1, int(pack or 1))
            unit_cost = (
                (Decimal(cost) / Decimal(pack)).quantize(Decimal("0.01"))
                if cost is not None
                else None
            )
            links[item_id] = (supplier_id, unit_cost, max(1, int(min_pack or 1)), pack)
        return links


def _round_up(quantity: int, min_pack: int, pack: int) -> int:
    """Whole packs, at least the supplier's minimum. **A zero stays a zero**:
    rounding a decision not to order up to one pack is an order nobody made."""
    if quantity <= 0:
        return 0
    packs = max(min_pack, math.ceil(quantity / pack))
    return packs * pack


def _parametric_quantity(policy, on_hand, coming, min_pack, pack):
    """Path 1 and its refusal, in that order (*Data*).

    Returns the quantity, or `None` where the sede has no pharmacist's policy to
    apply -- neither a maximum nor a reorder point, which sends the reference to
    path 2 and, failing that, to path 3's refusal.

    **The trigger is the pharmacist's own threshold.** A reference sitting above
    the reorder point somebody set on purpose is not one they want ordered, and
    topping every reference up to its maximum every morning would turn a
    replenishment list into a repeat of the opening order.
    """
    if policy is None:
        return None
    reorder = policy.reorder_point
    ceiling = policy.max_quantity
    if reorder is None and ceiling is None:
        return None
    available = on_hand + coming
    if reorder is not None and available > reorder:
        return 0
    if ceiling is not None:
        return max(0, ceiling - available)
    return max(0, (reorder or 0) + min_pack * pack - available)


def _category_quantity(median, on_hand, coming, target_days, lead_days):
    """Path 2 -- the category's own median weekly figure, carried at the group's
    target coverage. **An assumption about the category and never a finding
    about the item**, which is what its reason string says in words."""
    target = median * Decimal(target_days + lead_days) / Decimal(7)
    return int(max(Decimal("0"), target - Decimal(on_hand + coming)))


def _fire(
    *,
    item_id,
    quantity,
    computed,
    context,
):
    """Every reason code this line's arithmetic supports, before precedence.

    A code is *fired* by the arithmetic; whether the line may **claim** it is
    `reasons.admits`'s decision, and that split is what makes a `parametric`
    line unable to claim a season however the category behaved.
    """
    fired: dict = {}
    on_hand = computed.on_hand
    coverage = computed.coverage_days

    if on_hand <= 0 and item_id in context["elsewhere"]:
        units, sede = context["elsewhere"][item_id]
        if units >= max(1, quantity):
            fired["stockout_available_elsewhere"] = {"elsewhere": units, "sede": sede}

    if item_id in context["expiring"]:
        fired["lot_expiring"] = {"months": context["expiring"][item_id]}

    multiplier = context["multipliers"].get(context["categories"].get(item_id))
    if multiplier is not None and multiplier >= forecast.SEASONAL_PEAK:
        if item_id in context["stockouts"]:
            fired["seasonal_peak_recent_stockout"] = {}
        fired["seasonal_peak"] = {}

    if quantity == 0 and coverage is not None:
        if coverage > forecast.OVERSTOCK_DAYS:
            fired["overstock"] = {}
        elif coverage >= context["covered_at"]:
            fired["sufficient_coverage"] = {}

    if computed.basis in (ForecastBasis.LEARNING, ForecastBasis.LEARNED):
        fired["measured_demand"] = {}

    if computed.basis == ForecastBasis.PARAMETRIC:
        fired[
            "parametric_policy"
            if context["policies"].get(item_id) is not None
            else "parametric_category_default"
        ] = {}

    if context["floored"].get(item_id):
        fired["learning_floor"] = {}

    if (
        computed.variation is not None
        and computed.variation < forecast.CHRONIC_VARIATION
    ):
        fired["predictable_chronic"] = {}

    if (
        computed.trend is not None
        and abs(computed.trend) < forecast.STABLE_TREND
        and computed.confidence >= forecast.BAND_HIGH
        and coverage is not None
    ):
        fired["stable_rotation"] = {"coverage": int(coverage)}

    if item_id in context["partners"]:
        fired["cross_sell_pair"] = {"partner": context["partners"][item_id]}
    return fired


def propose(tenant_id, location_id, *, today=None) -> list:
    """Every line this sede's forecast supports today, ungrouped.

    Public because the generation job and the demo seed's own report both read
    it, and because a proposal a person can print before an order exists is what
    makes the arithmetic arguable.
    """
    from core.models import DemandForecast

    today = today or timezone.localdate()
    tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant is None:
        return []
    options = purchasing_settings.read(tenant)
    inventory = inventory_settings.read(tenant)

    rows = list(
        DemandForecast.objects.filter(tenant_id=tenant_id, location_id=location_id)
    )
    if not rows:
        return []

    item_ids = [row.item_id for row in rows]
    links = _supplier_links(tenant_id, item_ids)
    coming = on_order(tenant_id, location_id)
    held = forecast.on_hand_at(tenant_id, location_id)
    policies = forecast.policies_at(tenant_id, location_id)
    medians = forecast.category_medians(tenant_id)
    categories = dict(
        Item.objects.filter(tenant_id=tenant_id, id__in=item_ids).values_list(
            "id", "category_id"
        )
    )
    leads = forecast.lead_times(tenant_id, options)

    target_days = int(options["target_coverage_days"])
    cap_weeks = int(options["order_cap_weeks_per_line"])
    min_items = int(options["category_default_min_items"])
    multipliers = (
        forecast.category_multipliers(tenant_id, today)
        if options["seasonal_multiplier_enabled"]
        else {}
    )

    floored: dict = {}
    candidates: list = []
    for row in rows:
        link = links.get(row.item_id)
        if link is None:
            continue
        supplier_id, unit_cost, min_pack, pack = link
        on_hand = held.get(row.item_id, 0)
        already = coming.get(row.item_id, 0)
        lead_days = leads.get(row.item_id, int(options["default_lead_time_days"]))
        # **Only a pharmacist's row is a parameter.** A `source = model` row is
        # this stage's own arithmetic written back for S3's screen, and reading
        # it as an instruction here would let yesterday's forecast stand in for
        # a threshold nobody set -- which is the laundering the ledger's
        # `source` column exists to prevent.
        standing = policies.get(row.item_id)
        policy = (
            standing if standing and standing.source == PolicySource.MANUAL else None
        )

        parametric = _parametric_quantity(policy, on_hand, already, min_pack, pack)

        if row.basis == ForecastBasis.PARAMETRIC:
            if parametric is not None:
                quantity = parametric
            else:
                median, count = medians.get(categories.get(row.item_id), (None, 0))
                if median is None or count < min_items or median <= 0:
                    # **Path 3: the model withholds.** No policy row, no
                    # category to assume from, so there is no line at all --
                    # a screen full of invented quantities is worse than a
                    # screen that says what it is missing (§1).
                    continue
                quantity = _category_quantity(
                    median, on_hand, already, target_days, lead_days
                )
            # A parametric zero means the shelf is above the threshold its own
            # pharmacist set. There is nothing to decide and nothing to say, so
            # the reference is not a line -- the two facts below can still put
            # it on the order, and they are the only things that will.
            reviewable = quantity > 0
            material = Decimal(on_hand) * (unit_cost or Decimal("0"))
        else:
            weekly = row.weekly_sales or Decimal("0")
            target = weekly * Decimal(target_days + lead_days) / Decimal(7) + Decimal(
                row.safety_stock
            )
            # **The year-ago category multiplier scales the target, and only on
            # a `learned` row** (*Data*). It exists at all only where the tenant
            # carries 52 weeks, so on a young network this is arithmetic that
            # never runs -- which is the point: a model that invents a pollen
            # season out of eleven weeks of data is worse than one that says
            # `Rotación estable`.
            if row.basis == ForecastBasis.LEARNED:
                seasonal = multipliers.get(categories.get(row.item_id))
                if seasonal is not None:
                    target = target * seasonal
            quantity = int(max(Decimal("0"), target - Decimal(on_hand + already)))
            if weekly > 0:
                quantity = min(quantity, int(weekly * cap_weeks))
            if row.basis == ForecastBasis.LEARNING and parametric is not None:
                # **The parametric floor**, so a fortnight of quiet weeks cannot
                # talk the model out of restocking something the sede's own
                # policy says to keep.
                if parametric > quantity:
                    quantity = parametric
                    floored[row.item_id] = True
            # A measured zero is a line only where the zero is itself a
            # finding: capital tied up past ninety days of cover, or a shelf
            # with more cover than the order was aiming to leave on it. A
            # reference whose shortfall is merely already on its way is not a
            # decision -- it was decided on the order it is coming in on.
            covered = row.coverage_days is not None and row.coverage_days >= Decimal(
                target_days + lead_days
            )
            reviewable = quantity > 0 or covered
            material = Decimal(on_hand) * (unit_cost or Decimal("0"))

        quantity = _round_up(quantity, min_pack, pack)
        candidates.append(
            {
                "row": row,
                "supplier_id": supplier_id,
                "unit_cost": unit_cost,
                "quantity": quantity,
                "on_hand": on_hand,
                "reviewable": reviewable,
                "material": material,
                "policy": policy,
            }
        )

    keep = [one for one in candidates if one["reviewable"]]
    keep_ids = [one["row"].item_id for one in keep]
    context = {
        "elsewhere": elsewhere(tenant_id, location_id, keep_ids),
        "expiring": expiring_lots(
            tenant_id,
            location_id,
            keep_ids,
            horizon_days=inventory["expiry_alert_days"],
            today=today,
        ),
        "multipliers": multipliers,
        "categories": categories,
        "stockouts": (
            recent_stockout(tenant_id, location_id, keep_ids, today)
            if multipliers
            else set()
        ),
        # Which references carry a threshold a person set, which is what tells
        # `parametric_policy` apart from `parametric_category_default`.
        "policies": {
            item_id: standing
            for item_id, standing in policies.items()
            if standing.source == PolicySource.MANUAL
        },
        "floored": floored,
        #: The cover an order aims to leave, which is what `Cobertura
        #: suficiente, no pedir` is measured against.
        "covered_at": Decimal(target_days + int(options["default_lead_time_days"])),
        "partners": cross_sell_partners(
            tenant_id, location_id, keep_ids, forecast.window_start(today)
        ),
    }

    proposals = []
    for one in keep:
        row = one["row"]
        computed = forecast.Estimate(
            item_id=row.item_id,
            basis=row.basis,
            weekly_sales=row.weekly_sales,
            trend=row.trend,
            coverage_days=row.coverage_days,
            reorder_point=row.reorder_point,
            safety_stock=row.safety_stock,
            confidence=row.confidence,
            usable_weeks=row.usable_weeks,
            variation=row.variation,
            imported_share=row.imported_share,
            on_hand=one["on_hand"],
        )
        fired = _fire(
            item_id=row.item_id,
            quantity=one["quantity"],
            computed=computed,
            context=context,
        )
        code, fallback = reasons.resolve(fired, row.basis)
        proposals.append(
            Proposal(
                item_id=row.item_id,
                quantity=one["quantity"],
                basis=row.basis,
                confidence=row.confidence,
                coverage_days=row.coverage_days,
                reason_code=code,
                reason_fallback=fallback,
                unit_cost=one["unit_cost"],
                supplier_id=one["supplier_id"],
                urgency=(
                    row.coverage_days
                    if row.coverage_days is not None
                    else Decimal("999")
                ),
                material=one["material"],
            )
        )
    return proposals


def _within_finding_cap(lines):
    """Keep every proposed line, and the findings that are worth reading.

    A zero-quantity line is a finding rather than a purchase, and a catalog of
    4.284 references has a long tail whose cover legitimately runs into the
    hundreds of days -- so an order that listed every over-covered reference
    would be sixty rows of `Sobrestock` around the eleven a buyer is actually
    deciding on. The findings ride along ranked by the capital standing behind
    them, capped, so the ones with the most money in them are the ones on the
    screen.
    """
    proposed = [line for line in lines if line.quantity > 0]
    findings = sorted(
        (line for line in lines if line.quantity <= 0),
        key=lambda line: -line.material,
    )
    room = min(
        FINDINGS_PER_ORDER,
        max(FINDINGS_FLOOR, len(proposed) // LINES_PER_FINDING),
    )
    return proposed + findings[:room]


def generate(tenant_id, location_id, *, today=None) -> list:
    """One `suggested` order per supplier at one sede, for today.

    **A second run on the same day updates the order in place and never creates
    a second one**, and it never touches an order past `suggested`: an
    administrator's edits from an hour ago are not overwritten by the 05:00
    job's own retry. `suggested_quantity` on a line that already exists is never
    rewritten, because no code path in this stage may move it after generation.
    """
    today = today or timezone.localdate()
    tenant = Tenant.objects.filter(id=tenant_id).first()
    location = Location.objects.filter(id=location_id).first()
    if tenant is None or location is None:
        return []

    options = purchasing_settings.read(tenant)
    cap_value = Decimal(options["order_cap_value"])
    proposals = propose(tenant_id, location_id, today=today)
    model_version = f"{forecast.ALGORITHM}:{today.isoformat()}"

    grouped: dict = {}
    for proposal in proposals:
        grouped.setdefault(proposal.supplier_id, []).append(proposal)

    orders = []
    for supplier_id, lines in grouped.items():
        lines = _within_cap(_within_finding_cap(lines), cap_value)
        if not lines:
            continue
        orders.append(
            _write_order(
                tenant_id=tenant_id,
                location_id=location_id,
                supplier_id=supplier_id,
                lines=lines,
                model_version=model_version,
                today=today,
            )
        )
    return orders


def _within_cap(lines, cap_value):
    """Trim the least urgent lines until the order fits the group's value cap.

    The cap is a guard against a forecast that has gone wrong in one direction
    on one morning, not a budget: a trimmed line is one this morning did not
    propose, and tomorrow's run proposes it again. Trimming by urgency -- most
    days of cover first -- is what keeps the quiebres on the order.
    """
    total = sum(
        (line.unit_cost or Decimal("0")) * Decimal(line.quantity) for line in lines
    )
    if total <= cap_value:
        return lines
    ordered = sorted(lines, key=lambda line: -line.urgency)
    trimmed = set()
    for line in ordered:
        if total <= cap_value:
            break
        if line.quantity <= 0:
            continue
        total -= (line.unit_cost or Decimal("0")) * Decimal(line.quantity)
        trimmed.add(id(line))
    # Dropped, not zeroed. A line at zero is a decision the model made and says
    # so in its `Por qué`; a line the cap removed is a proposal this morning did
    # not make, and dressing it as `Cobertura suficiente, no pedir` would put a
    # sentence on the screen that is not true of that reference.
    return [line for line in lines if id(line) not in trimmed]


@transaction.atomic
def _write_order(*, tenant_id, location_id, supplier_id, lines, model_version, today):
    lock_location(tenant_id, location_id)
    start = timezone.make_aware(datetime.combine(today, time.min))
    order = (
        PurchaseOrder.objects.filter(
            tenant_id=tenant_id,
            location_id=location_id,
            supplier_id=supplier_id,
            status=PurchaseOrderStatus.SUGGESTED,
            source=PurchaseOrderSource.MODEL,
            created_at__gte=start,
        )
        .order_by("created_at")
        .first()
    )
    if order is None:
        order = PurchaseOrder.objects.create(
            tenant_id=tenant_id,
            location_id=location_id,
            supplier_id=supplier_id,
            number=next_number(tenant_id, location_id),
            status=PurchaseOrderStatus.SUGGESTED,
            source=PurchaseOrderSource.MODEL,
            model_version=model_version,
        )

    held = set(
        PurchaseOrderLine.objects.filter(purchase_order=order).values_list(
            "item_id", flat=True
        )
    )
    fresh = [
        PurchaseOrderLine(
            tenant_id=tenant_id,
            purchase_order=order,
            item_id=line.item_id,
            suggested_quantity=line.quantity,
            approved_quantity=line.quantity,
            unit_cost=line.unit_cost,
            reason_code=line.reason_code,
            basis=line.basis,
            confidence=line.confidence,
            coverage_days=line.coverage_days,
        )
        for line in lines
        if line.item_id not in held
    ]
    if fresh:
        PurchaseOrderLine.objects.bulk_create(fresh, batch_size=500)
    recompute_total(order)
    return order


def recompute_total(order) -> Decimal:
    """Σ `approved_quantity × unit_cost`, stored on the order.

    Stored rather than summed on read because it is the footer of a
    server-paginated table, and a total computed over the page would be the
    page's total rendered confidently as the order's.

    **The order row is locked before the sum is taken.** Two administrators
    editing two lines of one order at the same second would otherwise both read
    the sum before either wrote, and whichever committed last would store a
    total missing the other's change -- a footer that disagrees with its own
    rows, on the screen an approval is pressed from.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM purchase_orders WHERE id = %s FOR UPDATE",
            [str(order.id)],
        )
        cursor.execute(
            "SELECT coalesce(sum(approved_quantity * coalesce(unit_cost, 0)), 0) "
            "FROM purchase_order_lines WHERE purchase_order_id = %s",
            [str(order.id)],
        )
        total = Decimal(cursor.fetchone()[0] or 0).quantize(Decimal("0.01"))
    PurchaseOrder.objects.filter(id=order.id).update(
        total=total, updated_at=timezone.now()
    )
    order.total = total
    return total


# ---------------------------------------------------------------------------
# The edit, the approval, the dispatch and the discard
# ---------------------------------------------------------------------------


def set_line_quantity(order, line, quantity: int):
    """Write `approved_quantity`. **It never writes `suggested_quantity`.**

    This is the single most important line in this module. Two columns, never
    one: the difference is the only honest measure of whether the model is
    trusted, and overwriting the proposal destroys that measurement permanently.
    """
    if order.status != PurchaseOrderStatus.SUGGESTED:
        raise Refused(
            "Esta orden ya fue aprobada, así que sus cantidades están "
            "congeladas. Cree una orden nueva si necesita pedir más."
        )
    if quantity < 0:
        raise Refused("Una cantidad de la orden no puede ser negativa.")
    PurchaseOrderLine.objects.filter(id=line.id).update(
        approved_quantity=int(quantity), updated_at=timezone.now()
    )
    line.approved_quantity = int(quantity)
    recompute_total(order)
    return line


def approve(order, *, actor):
    """Freeze the quantities, stamp the approver, and hand it to dispatch.

    **Idempotent**: a second call on an order past `suggested` returns it
    unchanged, because a double-pressed button must not enqueue a second email
    to a supplier who has already been sent the order.
    """
    if order.status != PurchaseOrderStatus.SUGGESTED:
        return order
    now = timezone.now()
    PurchaseOrder.objects.filter(id=order.id).update(
        status=PurchaseOrderStatus.APPROVED,
        approved_by=actor if getattr(actor, "id", None) else None,
        approved_by_name=getattr(actor, "name", "") or "",
        approved_at=now,
        updated_at=now,
    )
    order.status = PurchaseOrderStatus.APPROVED
    order.approved_by_name = getattr(actor, "name", "") or ""
    order.approved_at = now
    return order


def mark_sent(order, *, at=None, error=""):
    """Record dispatch. Terminal for the sending half of the order's life."""
    now = at or timezone.now()
    PurchaseOrder.objects.filter(id=order.id).update(
        status=PurchaseOrderStatus.SENT,
        sent_at=now,
        last_dispatch_error=error,
        updated_at=now,
    )
    order.status = PurchaseOrderStatus.SENT
    order.sent_at = now
    order.last_dispatch_error = error
    return order


def record_dispatch_failure(order, reason: str):
    """Keep what the region-scope error has to name: the reason and the count."""
    PurchaseOrder.objects.filter(id=order.id).update(
        dispatch_attempts=(order.dispatch_attempts or 0) + 1,
        last_dispatch_error=str(reason)[:500],
        updated_at=timezone.now(),
    )


def discard(order):
    """Terminal, and **not a failure**: discarding a suggestion is the product
    working, which is why §B.7.4 colours the badge neutral."""
    if order.status not in (
        PurchaseOrderStatus.SUGGESTED,
        PurchaseOrderStatus.APPROVED,
    ):
        raise Refused(
            "Solo se puede descartar una orden que todavía no salió al proveedor."
        )
    now = timezone.now()
    PurchaseOrder.objects.filter(id=order.id).update(
        status=PurchaseOrderStatus.DISCARDED, discarded_at=now, updated_at=now
    )
    order.status = PurchaseOrderStatus.DISCARDED
    order.discarded_at = now
    return order


def settle(order):
    """Where an order lands once a receipt has been confirmed against it.

    `received` when every line is met or exceeded, `partially_received` when the
    supplier shorted something. A line the buyer approved at zero is met by
    definition and never holds an order open.
    """
    lines = list(PurchaseOrderLine.objects.filter(purchase_order=order))
    short = [line for line in lines if line.received_quantity < line.approved_quantity]
    status = (
        PurchaseOrderStatus.PARTIALLY_RECEIVED
        if short
        else PurchaseOrderStatus.RECEIVED
    )
    PurchaseOrder.objects.filter(id=order.id).update(
        status=status, updated_at=timezone.now()
    )
    order.status = status
    return order


def counterfactual(tenant_id, location_id, order, *, options) -> Decimal | None:
    """`Recortes vs. pedido manual` -- what a flat rule would have ordered.

    **The comparison is defined here so the tile is defensible**: the "pedido
    manual" is the order the group's `target_coverage_days` produces applied to
    every reference on this order with no forecast and no censoring, and the
    tile is this order's value minus that one's. Returns `None` where there is
    no projection to apply it to -- on a parametric order the flat rule and the
    order in front of you are the same rule, and a difference of zero would read
    as *the model saved you nothing* rather than *there is nothing here to
    compare* (§B.9.2 tier 3).
    """
    lines = list(
        PurchaseOrderLine.objects.filter(purchase_order=order).select_related("item")
    )
    measured = [line for line in lines if line.basis != ForecastBasis.PARAMETRIC]
    if not measured:
        return None

    from core.models import DemandForecast

    weekly = dict(
        DemandForecast.objects.filter(
            tenant_id=tenant_id,
            location_id=location_id,
            item_id__in=[line.item_id for line in measured],
        ).values_list("item_id", "weekly_sales")
    )
    held = forecast.on_hand_at(tenant_id, location_id)
    target_days = Decimal(options["target_coverage_days"])
    flat = Decimal("0")
    for line in measured:
        units = weekly.get(line.item_id)
        if units is None:
            continue
        target = units * target_days / Decimal(7)
        quantity = max(Decimal("0"), target - Decimal(held.get(line.item_id, 0)))
        flat += quantity * (line.unit_cost or Decimal("0"))
    if flat <= 0:
        return None
    return (order.total - flat).quantize(Decimal("0.01"))


def projected_daily_cost(tenant_id, location_id) -> Decimal:
    """What this **sede** is projected to consume in a day, at cost.

    The denominator of `cubre 34 días de venta proyectada`, and it is the sede's
    figure rather than the order's: an order covering thirty references at a
    sede that sells four thousand does not cover the sede for as long as its own
    lines would suggest, and a tile that divided by its own numerator would
    always read the same number.

    Cost is the preferred supplier's, per base unit. A reference nobody has
    priced contributes nothing rather than a zero-cost projection.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT coalesce(sum(
                f.weekly_sales / 7 * (si.cost / greatest(i.units_per_pack, 1))
            ), 0)
            FROM demand_forecasts f
            JOIN items i ON i.id = f.item_id
            JOIN LATERAL (
                SELECT s.cost
                FROM supplier_items s
                WHERE s.tenant_id = f.tenant_id AND s.item_id = f.item_id
                  AND s.cost IS NOT NULL
                ORDER BY s.is_preferred DESC, s.cost
                LIMIT 1
            ) si ON true
            WHERE f.tenant_id = %s AND f.location_id = %s
              AND f.weekly_sales IS NOT NULL AND f.weekly_sales > 0
            """,
            [str(tenant_id), str(location_id)],
        )
        return Decimal(cursor.fetchone()[0] or 0)


def receivable(order) -> bool:
    return order.status in RECEIVABLE_STATUSES
