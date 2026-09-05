"""The two engines, the precedence between them, and the run that writes both.

**The margin rule needs no history at all** (§1), which is why this screen is
useful on the first morning: a reference whose net-of-IVA margin sits below the
tenant's own goal is suggested a price that moves toward it, bounded by the
regulated cap and by the largest single step the owner permits. It works on day
one, for every reference that has a cost and a price, which on a freshly loaded
catalog is most of them.

**The elasticity engine appears per reference as that reference earns an
estimate**, and where it has one it decides. Every proposal states which engine
produced it and how much demand signal stood behind it, because *this rests on a
measured response* and *this rests on arithmetic about the margin you asked for*
are two different claims.

**Neither engine writes a price** (A11). Everything below produces rows in
`price_proposals` and `elasticity_estimates` and nothing else; the only column
of another stage's table this module writes is `items.custom.pricing`, and the
cap lives in `caps`.

**`item_prices.price` is the price the customer pays.** Architecture.md does not
state it; this stage does, because margin is not computable without it. The
shelf price is gross of IVA, so margin is computed **net of IVA**:
`net = price / (1 + rate)`, `margin = (net − cost) / net`. Elasticity is
estimated on the gross price, because that is the number the customer saw. Two
prices, two uses, and the record panel says which is which.
"""

import logging
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from core.models import (
    VAT_RATES,
    CapStatus,
    Confidence,
    CostSource,
    ElasticityEstimate,
    ElasticityStatus,
    Item,
    LIVE_PROPOSAL_STATUSES,
    NoProposalReason,
    PriceProposal,
    PriceProposalStatus,
    ProposalBasis,
)
from core.pricing import estimator, settings as pricing_settings

logger = logging.getLogger(__name__)

CENTAVO = Decimal("0.01")
HUNDRED = Decimal("100")

#: **A constant in the engine, and deliberately not a setting.** A dial that
#: controls how many proposals appear is a dial somebody turns until the screen
#: looks productive -- and a screen of 1.200 rows each worth six hundred pesos
#: is a screen nobody reviews, which is a model nobody uses.
MATERIALITY_PESOS = Decimal("50000")

#: The margin rule's materiality is a **margin gap**, not a peso impact, and the
#: reason is structural rather than stylistic: on day one there is no trailing
#: volume, so there is no peso figure to compare against a floor, and a rule
#: that demanded one would fire on nothing precisely when the screen most needs
#: to fire.
MATERIALITY_POINTS = Decimal("1.0")

#: How many complete weeks `q̂₃₀` is read over. Four, which is the month this
#: stage means: every figure it measures is weekly, and a rolling thirty days
#: would cut the last week in half.
TRAILING_WEEKS = 4

#: How far a candidate grid reaches, in steps of `rounding_unit`, before it is
#: cut off. The step cap is what actually binds; this only stops a rounding unit
#: of one peso producing a grid of thousands of candidates on a `$500.000`
#: reference.
MAX_CANDIDATES = 60


# ---------------------------------------------------------------------------
# Margin, net of IVA
# ---------------------------------------------------------------------------


def vat_rate(vat_class) -> Decimal:
    """The statutory rate as a share -- `0`, `0,05` or `0,19` (§3)."""
    return VAT_RATES[vat_class] / HUNDRED


def net_of_iva(gross, vat_class) -> Decimal:
    """What the pharmacy keeps of a shelf price before cost."""
    return Decimal(gross) / (Decimal("1") + vat_rate(vat_class))


def margin_pct(gross, cost, vat_class) -> Decimal | None:
    """`(net − cost) / net`, **in points**: `32.80` is 32,8%.

    Null where there is no cost, because a margin computed against a missing
    cost is a fiction, and where the net is zero, because nothing divides by it.
    """
    if gross is None or cost is None:
        return None
    net = net_of_iva(gross, vat_class)
    if net <= 0:
        return None
    return ((net - Decimal(cost)) / net * HUNDRED).quantize(Decimal("0.01"))


def goal_price(cost, vat_class, goal) -> Decimal:
    """`c · (1 + rate) / (1 − goal)` -- the gross price at which the net-of-IVA
    margin equals the goal."""
    share = Decimal(goal) / HUNDRED
    return Decimal(cost) * (Decimal("1") + vat_rate(vat_class)) / (Decimal("1") - share)


def round_toward(candidate, current, unit, step_cap_pct) -> Decimal:
    """Round to `unit`, **toward the current price, never away from it**.

    A 3% cap on `$12.500` at a `$50` unit yields `$12.850` (+2,8%), not `$12.900`
    (+3,2%). A rounding rule that can exceed a stated policy limit is a rounding
    rule that quietly rewrites the policy -- and the limit is the owner's, not
    ours.
    """
    unit = Decimal(unit)
    current = Decimal(current)
    candidate = Decimal(candidate)
    if unit <= 0:
        return candidate.quantize(CENTAVO)
    steps = candidate / unit
    down = (steps.to_integral_value(rounding="ROUND_FLOOR")) * unit
    up = (steps.to_integral_value(rounding="ROUND_CEILING")) * unit
    # **Toward the current price, and only toward it.** Rounding to whichever
    # multiple happens to be nearest would round a raise *up* past the price the
    # arithmetic actually asked for -- past the goal price on a margin
    # suggestion, and past the step cap on the reference the cap was set for.
    # A rounding rule that can exceed a stated policy limit is a rounding rule
    # that quietly rewrites the policy, and the limit is the owner's.
    rounded = down if candidate >= current else up
    limit = current * (Decimal("1") + Decimal(step_cap_pct) / HUNDRED)
    floor = current * (Decimal("1") - Decimal(step_cap_pct) / HUNDRED)
    if rounded <= 0 or not (floor <= rounded <= limit):
        return Decimal("0")
    return rounded


def rounding_step(item, unit) -> Decimal:
    """The rounding unit **per base unit**, which is not the same figure.

    `rounding_unit` is a peso amount for the price a customer pays, and for a
    fraccionable reference that price is the box: `item_prices.price` is per
    base unit and a box is `price × units_per_pack` (S1). A `$50` unit applied
    to a tablet at `$445` is a step no reference under about `$1.700` could ever
    clear -- the whole 3% budget is smaller than one unit -- so every
    fraccionable reference in the catalog would read `margin_gap_immaterial` for
    ever, which looks exactly like an engine with nothing to say.
    """
    per_pack = item.units_per_pack if item.splittable else 1
    return (Decimal(unit) / Decimal(max(1, per_pack))).quantize(CENTAVO)


def step_percentage(current, suggested) -> Decimal:
    return (
        (Decimal(suggested) - Decimal(current)) / Decimal(current) * HUNDRED
    ).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# What the run reads
# ---------------------------------------------------------------------------


def _only(item_ids):
    """The `AND item_id = ANY(...)` half of a read the grid narrows to a page.

    The run reads the whole tenant; the grid reads twenty-five rows and has a
    400ms p95 to meet on a four-thousand-reference catalog. One parameter, two
    callers, and the SQL is the same query either way.
    """
    if item_ids is None:
        return "", []
    return " AND {column} = ANY(%s)", [[str(one) for one in item_ids]]


def cost_bases(tenant_id, item_ids=None) -> dict:
    """`item_id -> (cost, source)`, and the cost basis is **current, never
    historical**.

    The quantity-weighted `lots.unit_cost` of the item's on-hand lots across the
    network, falling back to the preferred supplier's list price per base unit,
    and to a service's own standing cost. `sale_lines.unit_cost` is deliberately
    **not** used: it is stamped per sale from the lot, which is exactly right
    for historical margin and exactly wrong for projecting forward from today's
    replacement cost. The projection holds cost constant and the record panel
    says so -- a suggestion is an analysis of a price, not a forecast of a cost.
    """
    bases: dict = {}
    clause, narrowing = _only(item_ids)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT soh.item_id, "
            "       sum(soh.quantity * l.unit_cost), "
            "       sum(soh.quantity) "
            "FROM stock_on_hand soh JOIN lots l ON l.id = soh.lot_id "
            "WHERE soh.tenant_id = %s AND soh.quantity > 0 "
            "  AND l.unit_cost IS NOT NULL"
            + clause.format(column="soh.item_id")
            + " GROUP BY 1",
            [str(tenant_id), *narrowing],
        )
        for item_id, value, units in cursor:
            if units and value is not None and units > 0:
                bases[item_id] = (
                    (Decimal(value) / Decimal(units)).quantize(CENTAVO),
                    CostSource.LOTS,
                )

        cursor.execute(
            "SELECT si.item_id, si.cost, i.units_per_pack "
            "FROM supplier_items si JOIN items i ON i.id = si.item_id "
            "WHERE si.tenant_id = %s AND si.cost IS NOT NULL"
            + clause.format(column="si.item_id")
            + " ORDER BY si.is_preferred DESC, si.cost ASC",
            [str(tenant_id), *narrowing],
        )
        for item_id, cost, per_pack in cursor:
            if item_id in bases:
                continue
            per_unit = Decimal(cost) / Decimal(max(1, per_pack or 1))
            if per_unit > 0:
                bases[item_id] = (per_unit.quantize(CENTAVO), CostSource.SUPPLIER)

        # A service is a product with no cost of goods **unless one is entered**
        # (§3, A7), and `items.service_cost` is where S1 put the one that was.
        cursor.execute(
            "SELECT id, service_cost FROM items "
            "WHERE tenant_id = %s AND service_cost IS NOT NULL"
            + clause.format(column="id"),
            [str(tenant_id), *narrowing],
        )
        for item_id, cost in cursor:
            if item_id not in bases and cost is not None and Decimal(cost) > 0:
                bases[item_id] = (Decimal(cost).quantize(CENTAVO), CostSource.SUPPLIER)
    return bases


def current_prices(tenant_id, today: date, item_ids=None) -> dict:
    """`item_id -> price`, network-wide, by S1's own resolution rule.

    The latest `effective_from` whose window contains today, at the network
    scope -- which is the scope a proposal is written at, because
    `price_proposals` carries no `location_id`.
    """
    clause, narrowing = _only(item_ids)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT ON (item_id) item_id, price "
            "FROM item_prices "
            "WHERE tenant_id = %s "
            "  AND location_id IS NULL "
            "  AND effective_from <= %s "
            "  AND (effective_to IS NULL OR effective_to > %s)"
            + clause.format(column="item_id")
            + " ORDER BY item_id, effective_from DESC, id DESC",
            [str(tenant_id), today, today, *narrowing],
        )
        return {row[0]: Decimal(row[1]) for row in cursor}


def last_price_changes(tenant_id, today: date, item_ids=None) -> dict:
    """`item_id -> effective_from` of the item's most recent price **change**.

    **Whoever wrote it.** Keying the cooldown on any price change rather than on
    the model's own is the correct rule now that every price change is a
    person's (A11): a reference somebody repriced by hand on Tuesday should not
    be asked about again on Monday.

    **An item's first price row is not a change**, and that distinction is
    load-bearing rather than pedantic. A catalog loaded this morning has one
    `imported` row per reference dated this morning; reading that as a
    repricing would put the whole catalog into cooldown and the margin rule --
    the engine whose entire purpose is to be useful on the first morning --
    would propose nothing at all for thirty days. So the figure is the latest
    `effective_from` **strictly after the earliest one**, and an item with a
    single price row has never changed price.
    """
    clause, narrowing = _only(item_ids)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT item_id, max(effective_from), min(effective_from) "
            "FROM item_prices "
            "WHERE tenant_id = %s AND effective_from <= %s"
            + clause.format(column="item_id")
            + " GROUP BY 1",
            [str(tenant_id), today, *narrowing],
        )
        return {
            item_id: latest
            for item_id, latest, earliest in cursor
            if latest is not None and earliest is not None and latest > earliest
        }


def trailing_units(series, excluded, *, window_end: date) -> int:
    """`q̂₃₀` -- one item's trailing month of units across the network.

    **Read off the window the estimator already holds**, not fetched again. Two
    reasons, and the second is the better one:

      * the stage document asks for the trailing units *after the same
        exclusions*, and the weekly series is where those exclusions live -- a
        second query over `sale_lines` would count the units of a week the
        estimator threw out for a stock-out, which is exactly the volume the
        shelf could not supply;
      * it is one scan of a 26-week join per run instead of two.

    The last **four complete weeks** rather than a rolling thirty days, because
    the grain of everything this stage measures is the week, and a partial week
    at the end would understate the month by however far into it today is.
    """
    floor = window_end - timedelta(weeks=TRAILING_WEEKS)
    return int(
        sum(
            units
            for week, units in series.units.items()
            if floor <= week < window_end and week not in excluded
        )
    )


# ---------------------------------------------------------------------------
# The suggestion, per reference
# ---------------------------------------------------------------------------


class Context:
    """Everything one reference's decision reads, gathered once."""

    __slots__ = (
        "item",
        "price",
        "cost",
        "cost_source",
        "cap",
        "cap_status",
        "units",
        "last_change",
        "options",
        "goal",
        "today",
    )

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


class Suggestion:
    """What one engine concluded: a proposal's fields, or a reason there is none."""

    __slots__ = ("fields", "no_proposal_reason", "figures")

    def __init__(self, fields=None, no_proposal_reason=None, figures=None):
        self.fields = fields
        self.no_proposal_reason = no_proposal_reason
        self.figures = figures or {}


def gate_cap(context, direction) -> tuple[bool, str | None]:
    """The regulated cap, as a rule, and **it binds both engines**.

    A legal ceiling does not care which arithmetic reached it, so nothing here
    distinguishes a proposal by its basis. A null cap means *unknown*, never
    *uncapped*: an item at `unknown` may be proposed downward or held, and
    upward only when the tenant has deliberately lifted the default, on the
    record.
    """
    if direction <= 0:
        return True, None
    if context.cap is not None and context.cap_status == CapStatus.CAPPED:
        return True, None
    # **`not_regulated` is a claim somebody made**, and it is the whole point of
    # having three states rather than a nullable column: a source states this
    # reference is outside CNPMDM control, so there is no ceiling to respect and
    # a raise is ordinary. `unknown` is the absence of that claim, and it is
    # what the default blocks.
    if context.cap_status == CapStatus.NOT_REGULATED:
        return True, None
    if context.options["allow_raise_without_cap"]:
        return True, None
    return False, NoProposalReason.CAP_BLOCKS_RAISE


def apply_cap(candidate, context) -> Decimal:
    """A candidate above a **known** cap is proposed at the cap, not refused."""
    if context.cap is not None and candidate > context.cap:
        return Decimal(context.cap)
    return candidate


def margin_rule(context) -> Suggestion:
    """The parametric engine. It measures nothing, and says so everywhere.

    **It only ever proposes upward.** A reference already at or above the goal
    is not a defect to correct, and lowering a price in order to *reduce* a
    margin is not a thing this product does.

    **It reaches the goal in steps, not in one move.** A reference at 15,2%
    against a 22,0% goal needs an 8,7% rise and the step cap is 3,0, so the
    proposal is the capped step, `margin_gap_pp` records what remains, and the
    reference is proposed again after `min_days_between_changes`. One 8,7% jump
    onto a reference nobody has measured is the move that sends a customer to
    the droguería across the street, and no engine here has the evidence for it.
    """
    goal = context.goal
    if goal is None:
        # The day-one state, not an edge case: until an owner sets a goal the
        # margin rule suggests nothing, and the screen points at the field.
        return Suggestion(no_proposal_reason=None)

    current = margin_pct(context.price, context.cost, context.item.vat_class)
    if current is None:
        return Suggestion(no_proposal_reason=None)
    gap = goal - current
    figures = {"margin": current, "goal": goal}
    if gap <= 0:
        return Suggestion(
            no_proposal_reason=NoProposalReason.AT_MARGIN_GOAL, figures=figures
        )
    if gap < MATERIALITY_POINTS:
        return Suggestion(
            no_proposal_reason=NoProposalReason.MARGIN_GAP_IMMATERIAL, figures=figures
        )

    if context.cap is not None and context.price >= context.cap:
        return Suggestion(
            no_proposal_reason=NoProposalReason.CAP_AT_CURRENT,
            figures={**figures, "cap": context.cap},
        )

    allowed, refusal = gate_cap(context, 1)
    if not allowed:
        return Suggestion(no_proposal_reason=refusal, figures=figures)

    step_cap = Decimal(str(context.options["max_single_step_pct"]))
    unit = rounding_step(context.item, context.options["rounding_unit"])
    target = goal_price(context.cost, context.item.vat_class, goal)
    wanted = min(target, context.price * (Decimal("1") + step_cap / HUNDRED))

    # **An item whose optimal candidate exceeds the cap is proposed *at* the
    # cap**, and the cap is not rounded: a legal ceiling is a figure somebody
    # published, not one a droguería chooses, so rounding it down would leave
    # money on the table for tidiness and rounding it up would produce the one
    # number this stage must never produce.
    bound_by_cap = context.cap is not None and wanted > context.cap
    suggested = (
        Decimal(context.cap)
        if bound_by_cap
        else round_toward(wanted, context.price, unit, step_cap)
    )
    if suggested <= context.price or (
        not bound_by_cap and (suggested - context.price) < unit
    ):
        # The move is smaller than one rounding unit, so there is no price
        # between here and the goal that a droguería would put on a shelf.
        return Suggestion(
            no_proposal_reason=NoProposalReason.MARGIN_GAP_IMMATERIAL, figures=figures
        )

    projected = margin_pct(suggested, context.cost, context.item.vat_class)
    units = context.units
    impact = (
        (
            Decimal(units)
            * (
                net_of_iva(suggested, context.item.vat_class)
                - net_of_iva(context.price, context.item.vat_class)
            )
        ).quantize(CENTAVO)
        if units
        else None
    )
    return Suggestion(
        fields={
            "basis": ProposalBasis.MARGIN_RULE,
            "elasticity_estimate": None,
            "suggested_price": suggested,
            "current_margin": current,
            "projected_margin": projected,
            "margin_gap_pp": (goal - projected).quantize(Decimal("0.01")),
            "estimated_monthly_impact": impact,
            "trailing_monthly_units": units or None,
            "confidence": Confidence.LOW,
            "reason_code": "cap_bound_raise" if bound_by_cap else "margin_below_goal",
        }
    )


def elasticity_rule(context, estimate) -> Suggestion:
    """The measured engine, on a **bounded step** rather than an optimum.

    Under constant elasticity the margin-maximising price is `c·β/(1+β)`, which
    for an inelastic item (`|β| < 1`) is negative or unbounded -- the formula's
    answer for most of this catalog is *infinity*, which is obviously false and
    is the trap this method sets. So the engine scores candidate prices on a
    grid from `−max_single_step_pct` to `+max_single_step_pct`, each rounded to
    `rounding_unit`, and takes the best.

    **For an inelastic item the winner is always the largest permitted step**,
    which means the step cap *is* the pricing policy -- and that is exactly why
    it is a setting an owner sets and not a number the model chooses.
    """
    beta = Decimal(str(estimate.elasticity))
    step_cap = Decimal(str(context.options["max_single_step_pct"]))
    unit = rounding_step(context.item, context.options["rounding_unit"])
    cost = Decimal(context.cost)
    vat = context.item.vat_class
    units = Decimal(context.units or 0)

    best = None
    best_score = Decimal("0")
    base_margin = net_of_iva(context.price, vat) - cost
    reach = min(
        MAX_CANDIDATES,
        int(
            (context.price * step_cap / HUNDRED / unit).to_integral_value(
                "ROUND_CEILING"
            )
        )
        if unit > 0
        else 0,
    )
    anchor = (context.price / unit).to_integral_value(rounding="ROUND_HALF_UP") * unit
    grid = [anchor + steps * unit for steps in range(-reach, reach + 1)]
    # **The cap itself is a candidate.** It is rarely a multiple of the rounding
    # unit, and without it the best allowed price on a capped reference is the
    # largest multiple *below* the ceiling -- which leaves money on the table
    # and, worse, never renders `cap_bound_raise`.
    if context.cap is not None:
        grid.append(Decimal(context.cap))
    for candidate in grid:
        if candidate <= 0 or candidate == context.price:
            continue
        move = step_percentage(context.price, candidate)
        if abs(move) > step_cap:
            continue
        direction = 1 if candidate > context.price else -1
        allowed, _refusal = gate_cap(context, direction)
        if not allowed:
            continue
        if context.cap is not None and candidate > context.cap:
            continue
        ratio = candidate / context.price
        projected_units = units * _power(ratio, beta)
        score = (
            projected_units * (net_of_iva(candidate, vat) - cost) - units * base_margin
        )
        if best is None or score > best_score:
            best, best_score = candidate, score

    figures = {"r2": estimate.r2, "elasticity": estimate.elasticity}
    if best is None:
        # Every candidate was blocked -- by the cap, by an unknown cap with
        # raises off, or by a step cap smaller than one rounding unit.
        if context.cap is not None and context.price >= context.cap:
            return Suggestion(
                no_proposal_reason=NoProposalReason.CAP_AT_CURRENT,
                figures={**figures, "cap": context.cap},
            )
        return Suggestion(
            no_proposal_reason=NoProposalReason.CAP_BLOCKS_RAISE, figures=figures
        )

    impact = best_score.quantize(CENTAVO)
    # **Only an improvement is ever suggested.** `best_score` is Δprofit against
    # the price standing today, so the winning candidate is the one that earns
    # most -- and where that is not more than today, there is nothing to do.
    # Taking the absolute value here would propose the least-bad move on a
    # reference whose every candidate loses money.
    if impact < MATERIALITY_PESOS or abs(best - context.price) < unit:
        return Suggestion(
            no_proposal_reason=NoProposalReason.BELOW_MATERIALITY,
            figures={**figures, "impact": impact},
        )

    bound_by_cap = context.cap is not None and best >= context.cap
    return Suggestion(
        fields={
            "basis": ProposalBasis.ELASTICITY,
            "suggested_price": best,
            "current_margin": margin_pct(context.price, context.cost, vat),
            "projected_margin": margin_pct(best, context.cost, vat),
            "margin_gap_pp": None,
            "estimated_monthly_impact": impact,
            "trailing_monthly_units": context.units or None,
            "confidence": estimate.confidence,
            "reason_code": (
                "cap_bound_raise"
                if bound_by_cap
                else "inelastic_raise"
                if best > context.price
                else "elastic_reduce"
            ),
        }
    )


def _power(base: Decimal, exponent: Decimal) -> Decimal:
    """`base ** exponent`. Statistics, not money -- see `reasons._power`."""
    return Decimal(str(float(base) ** float(exponent)))


def above_cap_finding(item, price, cost, units) -> "Suggestion":
    """A reference whose **current** price already exceeds a loaded cap.

    Not a pricing opportunity at all: the till is charging above the legal
    maximum today, and this is a compliance finding. It carries `basis =
    margin_rule` because the column is not nullable and no engine produced it --
    the `Base` chip excludes it for the same reason, so the count under `Margen`
    stays a count of suggestions.

    **The cost may be missing and the finding still stands.** Where it is, the
    price stands in for it so the margin reads zero rather than the row being
    dropped -- the same choice the daily cap check makes.
    """
    basis_cost = Decimal(cost) if cost is not None else Decimal(price)
    cap = Decimal(item.regulated_max_price)
    return Suggestion(
        fields={
            "status": PriceProposalStatus.ABOVE_CAP,
            "basis": ProposalBasis.MARGIN_RULE,
            "suggested_price": cap,
            "current_margin": margin_pct(price, basis_cost, item.vat_class),
            "projected_margin": margin_pct(cap, basis_cost, item.vat_class),
            "margin_gap_pp": None,
            "estimated_monthly_impact": None,
            "trailing_monthly_units": units or None,
            "confidence": Confidence.LOW,
            "reason_code": "above_regulated_cap",
            "cost_basis": basis_cost,
            "cost_source": CostSource.SUPPLIER if cost is None else None,
        }
    )


def decide(context, estimate) -> Suggestion:
    """Which engine owns this reference, and what it concluded.

    The partial unique constraint permits one live proposal per reference, so
    precedence is decided here rather than blended:

      * a **qualifying** estimate prices it, whatever it concludes -- including
        concluding that nothing is worth doing. A measured response beats an
        assumption in both directions, and a reference the estimator priced or
        deliberately left alone is not re-proposed upward by the margin rule
        because a goal says it should earn more;
      * an estimate too weak to price on that reads **elastic** vetoes both.
        Weak evidence may stop a move and may not start one;
      * anything else -- weak and inelastic, or no estimate at all -- is the
        margin rule's.

    *If this precedence is wrong*, the reference is still in the grid with its
    estimate and its margin both in the panel, the owner sets whatever price
    they judge right in S1's editor, and the proposal records it as `modified`
    with the number they chose -- which is the signal arriving in the same
    column the measurement reads. The reverse error is not recoverable the same
    way: a margin rule permitted to override a measured elastic response would
    raise the price of exactly the references whose customers leave.
    """
    cooldown = int(context.options["min_days_between_changes"])
    changed = context.last_change
    if changed is not None and (context.today - changed).days < cooldown:
        return Suggestion(
            no_proposal_reason=NoProposalReason.COOLDOWN,
            figures={
                "days_since": (context.today - changed).days,
                "eligible_on": changed + timedelta(days=cooldown),
            },
        )

    if estimate.qualifies:
        return elasticity_rule(context, estimate)
    if estimate.elasticity is not None and estimate.reads_elastic:
        return Suggestion(
            no_proposal_reason=NoProposalReason.ELASTIC_VETO,
            figures={"elasticity": estimate.elasticity, "r2": estimate.r2},
        )
    if estimate.elasticity is not None:
        # A weak inelastic reading. The margin rule takes it, and the panel says
        # the estimate exists and why it was not priced on. Where the margin
        # rule has nothing of its own to say either -- no goal set, no margin
        # computable -- the weak fit is the honest reason nothing happened, and
        # `low_confidence` renders it with the r² that produced it.
        suggestion = margin_rule(context)
        if suggestion.fields is None and suggestion.no_proposal_reason is None:
            return Suggestion(
                no_proposal_reason=NoProposalReason.LOW_CONFIDENCE,
                figures={"r2": estimate.r2, "elasticity": estimate.elasticity},
            )
        return suggestion
    return margin_rule(context)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run(tenant_id, *, today=None) -> dict:
    """The estimator, the margin rule and the suggestion engine as **one job**.

    Aggregation and regression happen in memory; **the entire run's estimates
    and suggestions are written in one transaction at the end.** A long write
    transaction holding four thousand regressions open is the thing not to
    build, and a half-written run is the thing a screen must never read.

    **It never no-ops for want of sales.** With zero `sales` rows the estimator
    writes a `no_sales` row for every evaluated item and the margin rule still
    produces suggestions from cost and price alone. The day-one path is the
    ordinary path with one engine silent -- not a special case, not a separate
    command.
    """
    today = today or timezone.localdate()
    tenant = _tenant(tenant_id)
    options = pricing_settings.read(tenant)
    goal = pricing_settings.goal(options)
    computed_at = timezone.now()

    start = estimator.window_start(today)
    end = start + timedelta(weeks=estimator.WINDOW_WEEKS)

    series = estimator.read_window(tenant_id, start, end)
    estimator.net_returns(series, estimator.read_returns(tenant_id, start, end))
    windows = estimator.read_price_windows(tenant_id)
    prices = current_prices(tenant_id, today)
    costs = cost_bases(tenant_id)
    changes = last_price_changes(tenant_id, today)

    candidates = [
        item_id
        for item_id, one in series.items()
        if estimator.could_clear_floors(one, windows.get(item_id, []))
    ]
    empty = estimator.read_stockout_weeks(tenant_id, candidates, start, today)
    candidate_set = set(candidates)

    items = list(
        Item.objects.filter(tenant_id=tenant_id).only(
            "id",
            "name",
            "active",
            "vat_class",
            "regulated_max_price",
            "cap_status",
            "custom",
        )
    )

    estimates: list[ElasticityEstimate] = []
    proposals: list[PriceProposal] = []
    custom_updates: list[Item] = []
    counted: dict[str, int] = {
        "evaluated": 0,
        "estimated": 0,
        "proposals": 0,
        "margin_rule": 0,
        "elasticity": 0,
        "above_cap": 0,
    }
    by_status: dict[str, int] = defaultdict(int)
    by_reason: dict[str, int] = defaultdict(int)

    for item in items:
        counted["evaluated"] += 1
        cost, cost_source = costs.get(item.id, (None, None))
        price = prices.get(item.id)
        one = series.get(item.id)

        estimate, excluded_weeks = _estimate_for(
            item,
            one,
            cost,
            windows.get(item.id, []),
            empty.get(item.id, {}),
            candidate_set,
            start,
            today,
        )
        monthly = (
            trailing_units(one, excluded_weeks, window_end=end)
            if one is not None
            else 0
        )

        suggestion = Suggestion()
        if (
            price is not None
            and item.regulated_max_price is not None
            and (price > item.regulated_max_price)
        ):
            # **The compliance finding does not wait for a cost.** The till is
            # charging above the legal maximum today whether or not anybody has
            # loaded what the box cost, and a run that dropped the finding for
            # want of a cost basis would quietly undo the daily check that
            # raised it -- on exactly the references nobody is maintaining.
            suggestion = above_cap_finding(item, price, cost, monthly)
        elif item.active and cost is not None and price is not None:
            context = Context(
                item=item,
                price=price,
                cost=cost,
                cost_source=cost_source,
                cap=item.regulated_max_price,
                cap_status=item.cap_status or CapStatus.UNKNOWN,
                units=monthly,
                last_change=changes.get(item.id),
                options=options,
                goal=goal,
                today=today,
            )
            suggestion = decide(context, estimate)

        row = ElasticityEstimate(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            item_id=item.id,
            location_id=None,
            elasticity=estimate.elasticity,
            r2=estimate.r2,
            window=estimator.WINDOW_LABEL,
            observations=estimate.observations,
            computed_at=computed_at,
            model_version=estimator.MODEL_VERSION,
            status=estimate.status,
            no_proposal_reason=suggestion.no_proposal_reason,
            std_error=estimate.std_error,
            ci_low=estimate.ci_low,
            ci_high=estimate.ci_high,
            distinct_prices=estimate.distinct_prices,
            price_dispersion=estimate.price_dispersion,
            weeks_excluded_stockout=estimate.weeks_excluded_stockout,
            weeks_excluded_promo=estimate.weeks_excluded_promo,
            imported_share=estimate.imported_share,
            confidence=estimate.confidence,
        )
        estimates.append(row)
        # **The per-sede fits, where a sede cleared every floor on its own.**
        # They flag heterogeneity in the record panel and price nothing: a
        # proposal is network-grain, because `price_proposals` carries no
        # `location_id` and v1 cannot express two simultaneous prices for one
        # reference. Only a reference the network fit could estimate is split --
        # a sede cannot clear floors the whole network missed.
        if one is not None and estimate.status == ElasticityStatus.ESTIMATED:
            estimates.extend(
                _per_sede(
                    item,
                    one,
                    windows.get(item.id, []),
                    empty.get(item.id, {}),
                    start,
                    today,
                    tenant_id,
                    computed_at,
                )
            )
        by_status[estimate.status] += 1
        if estimate.status == ElasticityStatus.ESTIMATED:
            counted["estimated"] += 1
        if suggestion.no_proposal_reason:
            by_reason[suggestion.no_proposal_reason] += 1

        band = estimator.elasticity_band(estimate.elasticity)
        if _stamp_custom(item, band, changes.get(item.id)):
            custom_updates.append(item)

        if suggestion.fields is None or price is None:
            # A suggestion is only ever built inside a branch that required an
            # in-force price, so this narrows rather than guards -- and a guard
            # that can never fire is cheaper than a figure that could be null.
            continue
        fields = dict(suggestion.fields)
        status = fields.pop("status", PriceProposalStatus.PROPOSED)
        suggested = Decimal(fields.pop("suggested_price"))
        # A compliance finding brings its own cost basis, because it is raised
        # whether or not one is loaded.
        basis_cost = fields.pop("cost_basis", None) or cost
        basis_source = (
            fields.pop("cost_source", None) or cost_source or CostSource.SUPPLIER
        )
        proposals.append(
            PriceProposal(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                item_id=item.id,
                status=status,
                current_price=price,
                suggested_price=suggested,
                # `true` only where the cap is **known** -- a loaded ceiling
                # the price is at or under, or a stated absence of one. An
                # `unknown` cap yields `false`, because a boolean named
                # *respects the regulated cap* cannot be true against a cap
                # nobody holds.
                respects_regulated_cap=_respects_cap(item, suggested, status),
                cost_basis=basis_cost,
                cost_source=basis_source,
                step_pct=step_percentage(price, suggested),
                regulated_max_price_at_proposal=item.regulated_max_price,
                computed_at=computed_at,
                model_version=estimator.MODEL_VERSION,
                # By id, not by instance: `row` has not been inserted yet, and
                # Django refuses to save a relation to an unsaved object. The
                # estimates are bulk-created first inside the same transaction,
                # so the foreign key is satisfied by the time this row lands.
                elasticity_estimate_id=(
                    row.id if fields.get("basis") == ProposalBasis.ELASTICITY else None
                ),
                **{k: v for k, v in fields.items() if k != "elasticity_estimate"},
            )
        )
        counted["proposals"] += 1
        if status == PriceProposalStatus.ABOVE_CAP:
            counted["above_cap"] += 1
        elif fields["basis"] == ProposalBasis.ELASTICITY:
            counted["elasticity"] += 1
        else:
            counted["margin_rule"] += 1

    with transaction.atomic():
        # **Superseding happens in the same transaction**, so the screen never
        # sees two live suggestions for one reference and never sees none at
        # all. A resolved suggestion is never touched: `taken`, `modified` and
        # `dismissed` are S1's writes and they are the measurement record.
        superseded = PriceProposal.objects.filter(
            tenant_id=tenant_id, status__in=LIVE_PROPOSAL_STATUSES
        ).update(status=PriceProposalStatus.SUPERSEDED, updated_at=timezone.now())
        ElasticityEstimate.objects.bulk_create(estimates, batch_size=1000)
        PriceProposal.objects.bulk_create(proposals, batch_size=1000)
        if custom_updates:
            Item.objects.bulk_update(custom_updates, ["custom"], batch_size=500)
    summary: dict = {
        **counted,
        "superseded": superseded,
        "computed_at": computed_at,
        "by_status": dict(by_status),
        "by_reason": dict(by_reason),
    }
    logger.info("pricing run for %s: %s", tenant_id, summary)
    return summary


def _per_sede(item, series, windows, empty, start, today, tenant_id, computed_at):
    """One `elasticity_estimates` row per sede that cleared every floor alone.

    A sede that missed one is simply absent: the panel's heterogeneity block
    lists the sedes there is a measurement for, and a row saying *this sede
    could not be measured* would be four hundred rows of noise for the one
    reference somebody opened.
    """
    rows = []
    for location_id, one in estimator.per_location(series).items():
        excluded = frozenset(
            estimator.stockout_weeks(
                {location_id: empty.get(location_id, set())},
                {location_id},
                start,
                today,
            )
        )
        estimate = estimator.estimate(
            one, windows, excluded_weeks=excluded, first_week=start
        )
        if estimate.status != ElasticityStatus.ESTIMATED:
            continue
        rows.append(
            ElasticityEstimate(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                item_id=item.id,
                location_id=location_id,
                elasticity=estimate.elasticity,
                r2=estimate.r2,
                window=estimator.WINDOW_LABEL,
                observations=estimate.observations,
                computed_at=computed_at,
                model_version=estimator.MODEL_VERSION,
                status=estimate.status,
                std_error=estimate.std_error,
                ci_low=estimate.ci_low,
                ci_high=estimate.ci_high,
                distinct_prices=estimate.distinct_prices,
                price_dispersion=estimate.price_dispersion,
                weeks_excluded_stockout=estimate.weeks_excluded_stockout,
                weeks_excluded_promo=estimate.weeks_excluded_promo,
                imported_share=estimate.imported_share,
                confidence=estimate.confidence,
            )
        )
    return rows


def _respects_cap(item, suggested, status) -> bool:
    if status == PriceProposalStatus.ABOVE_CAP:
        return False
    if item.cap_status == CapStatus.NOT_REGULATED:
        return True
    cap = item.regulated_max_price
    return cap is not None and Decimal(suggested) <= cap


def _estimate_for(item, series, cost, windows, empty, candidates, start, today):
    """One item's estimate and the weeks it excluded, or the status that says why
    there is none.

    The excluded weeks travel back with it because `q̂₃₀` is read off the same
    window: a month's units that counted a week the shelf was empty would be the
    stock-out biasing the *impact* after the estimator had taken it out of the
    *fit*.

    The order is deliberate: *inactive* and *no cost loaded* are facts about the
    catalog and are checked before anything is read out of the window, because
    `Sin costo cargado. No se puede calcular margen.` and `Sin ventas en la
    ventana de 26 semanas.` send a reader to two different places and the first
    is the true one when both hold.
    """
    empty_set: frozenset = frozenset()
    if not item.active:
        return estimator.Estimate(ElasticityStatus.INACTIVE), empty_set
    if cost is None:
        return estimator.Estimate(ElasticityStatus.NO_COST), empty_set
    if series is None:
        return estimator.Estimate(ElasticityStatus.NO_SALES), empty_set
    if item.id not in candidates:
        # The replay was not run, and the two exclusion counts stay null rather
        # than reading zero: the reference could not have cleared the floors
        # with or without it.
        return (
            estimator.estimate(
                series, windows, excluded_weeks=empty_set, first_week=start
            ),
            empty_set,
        )
    excluded = frozenset(
        estimator.stockout_weeks(empty, series.locations, start, today)
    )
    return (
        estimator.estimate(series, windows, excluded_weeks=excluded, first_week=start),
        excluded,
    )


def _stamp_custom(item, band, last_change) -> bool:
    """S7's one key of `items.custom`, written without touching its neighbours.

    A whole-document write passes every check in this stage and silently deletes
    another stage's key, which is why this reads the document, replaces exactly
    `pricing`, and answers whether anything moved.
    """
    document = dict(item.custom or {})
    before = document.get("pricing")
    after = {
        "elasticity_band": band,
        "cap_status": item.cap_status or CapStatus.UNKNOWN,
        "last_price_change_at": last_change.isoformat() if last_change else None,
    }
    if before == after:
        return False
    document["pricing"] = after
    item.custom = document
    return True


def _tenant(tenant_id):
    from core.models import Tenant

    tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant is None:
        raise LookupError(
            f"No tenant row matched {tenant_id} inside this pin, so the pricing "
            "run had no settings group to read."
        )
    return tenant
