"""The elasticity estimator, and its limits.

A pricing model does its damage through its assumptions, and an assumption
nobody wrote down is an assumption nobody can review -- so the method is fixed
here rather than left to a caller.

**It withholds rather than guessing.** An elasticity does not degrade gracefully
the way a demand forecast does: an item whose price has never moved does not
have a small elasticity, it has **none**, because there is no variation to
regress on, and waiting does not fix it -- what is missing is not observations
but variation. So the estimator writes a row with `elasticity = NULL`, a
`status` saying which floor it missed, and the counts that show why, and hands
the reference to the margin rule. Nothing downstream may coalesce that null to a
number: a zero is a claim of perfect inelasticity, which says *raise the price as
far as you like and sell exactly as many*, and on a catalog where most
references have never moved it turns every one of them into a maximum-step rise.

**The grain is the week, and `observations` counts weeks.** Every floor in this
stage is stated in weeks -- eight to write an estimate, twelve to offer a
proposal, twenty for `high` confidence -- and the panel renders `sobre 22
semanas`. So the network fit is one row per surviving week, with units summed
across sedes and the price the units-weighted figure customers actually paid.
The stage document describes that pooling as *a location fixed effect where two
or more sedes contributed*, and with one network price it is the same estimate:
`price_proposals` carries no `location_id`, every sede faces the same number in
the same week, so location dummies are orthogonal to `ln(price)` and move β by
nothing while costing the regression its honest observation count. A **per-sede**
estimate is a separate fit at that sede's own grain, written only where that sede
clears every floor on its own, and it flags heterogeneity in the record panel --
never prices a sede differently, which v1 cannot express.
"""

import logging
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo

from django.db import connection

from core.models import Confidence, ElasticityStatus

logger = logging.getLogger(__name__)

#: The specification this module implements. Changing it bumps the version, and
#: a run never mixes versions -- so a fit made under an older method is
#: identifiable afterwards rather than silently averaged with a newer one.
MODEL_VERSION = "elasticity-loglog-v1"

#: The window the estimator asks for. `observations` is what survived it, and
#: the two are read separately: on a tenant three months old the window is still
#: `26w` and the observation count is what says so.
WINDOW_WEEKS = 26
WINDOW_LABEL = "26w"

#: The business calendar every week boundary is cut on. A week is a week the
#: droguería had, not a week UTC had.
TIMEZONE = "America/Bogota"

# -- the floors, and every one of them is a week count --------------------

#: Below this many surviving weeks nothing is written but a status.
MIN_WEEKS = 8
#: The two price points a fit needs, and how many weeks each must hold.
MIN_DISTINCT_PRICES = 2
MIN_WEEKS_PER_PRICE = 3
#: The standard deviation of `ln(price)` beneath which the variation is noise.
MIN_PRICE_DISPERSION = Decimal("0.02")

#: What an **offer** needs, over and above what an estimate needs.
MIN_OFFER_OBSERVATIONS = 12
MIN_OFFER_R2 = Decimal("0.30")

#: `high` -- a fit worth calling measured.
HIGH_OBSERVATIONS = 20
HIGH_R2 = Decimal("0.50")
HIGH_INTERVAL_WIDTH = Decimal("0.6")

#: **A demand curve slopes down.** A fitted `β` at or above zero is not an own-
#: price elasticity -- it is the trend, a season or a stock-out wearing one --
#: and a magnitude past this ceiling is not a droguería reference either: a
#: `β` of −8 says a 3% rise loses a fifth of the volume, which no pharmacy has
#: ever measured on a box of acetaminofén. Both are **withheld**, not clamped:
#: the whole design of this estimator is that it says nothing rather than
#: something weak, and a clamped coefficient is a fabricated one.
MAX_PLAUSIBLE_ELASTICITY = Decimal("5")

#: Above this share of imported weeks the band is capped at `medium`: those rows
#: record a price the previous system charged and a cost we did not pay.
IMPORTED_CAP_SHARE = Decimal("0.5")

#: A week in which more than this share of the item's units carried a discount
#: is a different demand regime, and mixing it in overstates responsiveness.
PROMO_UNIT_SHARE = Decimal("0.30")

#: How many surviving weeks each estimated parameter has to be able to pay for
#: before it is estimated at all. Five is the ordinary rule of thumb, and on a
#: 26-week window it is what keeps the month effects from eating the price
#: coefficient they were added to protect.
OBSERVATIONS_PER_PARAMETER = 5

#: Where `elasticity_band` splits. `unit` is a band rather than the point 1,0,
#: because no fit lands on exactly one and a reader asking whether a reference
#: is elastic is asking about a neighbourhood.
UNIT_BAND = (Decimal("0.95"), Decimal("1.05"))


def per_location(series, locations=None) -> dict:
    """One item's window split back out by sede.

    The network fit pools the sedes; a **per-sede** fit is a separate estimate at
    that sede's own grain, written only where that sede clears every floor on
    its own. It flags heterogeneity in the record panel and **never prices a
    sede differently**, which v1 cannot express: `price_proposals` carries no
    `location_id`.
    """
    split: dict = {}
    for (location_id, week), units in series.by_location.items():
        if locations is not None and location_id not in locations:
            continue
        one = split.get(location_id)
        if one is None:
            one = split[location_id] = WeekSeries(series.item_id)
            one.locations.add(location_id)
        one.units[week] = one.units.get(week, 0) + units
        one.paid[week] = one.paid.get(week, Decimal("0")) + series.paid_by_location.get(
            (location_id, week), Decimal("0")
        )
        one.discounted_units[week] = series.discounted_by_location.get(
            (location_id, week), 0
        )
        one.imported_units[week] = series.imported_by_location.get(
            (location_id, week), 0
        )
    return split


class WeekSeries:
    """One item's window, as the estimator sees it before any floor is applied.

    A plain object rather than a tuple because eight things travel together and
    a caller reading `series[4]` is a caller who will read `series[5]` by
    mistake.
    """

    __slots__ = (
        "item_id",
        "units",
        "paid",
        "discounted_units",
        "imported_units",
        "locations",
        "by_location",
        "paid_by_location",
        "discounted_by_location",
        "imported_by_location",
    )

    def __init__(self, item_id):
        self.item_id = item_id
        #: week (a Monday) -> units
        self.units: dict[date, int] = {}
        #: week -> Σ (unit_price × quantity − discount), the money actually taken
        self.paid: dict[date, Decimal] = {}
        self.discounted_units: dict[date, int] = {}
        self.imported_units: dict[date, int] = {}
        #: The sedes that contributed anything at all, which is what makes a
        #: stock-out at a sede that never sold this reference irrelevant.
        self.locations: set = set()
        #: The same four, kept `(location, week)` as well as pooled, so a
        #: per-sede fit costs no second query.
        self.by_location: dict = {}
        self.paid_by_location: dict = {}
        self.discounted_by_location: dict = {}
        self.imported_by_location: dict = {}


def business_bound(day: date) -> datetime:
    """A window edge as an instant, **in the pharmacy's own clock**.

    The week buckets below are cut `AT TIME ZONE 'America/Bogota'`, and the
    bounds have to be cut the same way or they disagree with them: a bare `date`
    compared against a `timestamptz` is resolved in the session's timezone,
    which is UTC, so a window that *looks* like 26 whole weeks would open and
    close five hours late -- admitting a sliver of a 27th week at one end and
    truncating the last week at the other. Five hours of a Monday is a Sunday
    evening's takings landing in the wrong week, on the one grain every floor in
    this stage is stated in.
    """
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(TIMEZONE))


def window_start(today: date) -> date:
    """The Monday `WINDOW_WEEKS` **complete** weeks back.

    The week in progress is never one of them: it is a partial week whose units
    are low for a reason that has nothing to do with price, and a partial week
    at the end of a log-log fit is a fabricated observation at the point the
    trend term leans on hardest.
    """
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(weeks=WINDOW_WEEKS)


def read_window(tenant_id, start: date, end: date) -> dict:
    """Every item's weekly aggregates over the window, in one query.

    Cut on **`recorded_at`**, never `occurred_at` (ledger rule 8): every window,
    rollup and exclusion in this stage reads the server's clock, because a till
    that was offline for an afternoon must not move a week boundary.

    `status = 'closed'` is what drops voided sales, and returns live in their
    own table and are not demand. `source` is named explicitly and both values
    are asked for on purpose: imported rows carry a quantity and a price, which
    is all β needs, and `imported_share` records what fraction of the window
    they are.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT sl.item_id,
                   sl.location_id,
                   (date_trunc('week', s.recorded_at AT TIME ZONE %s))::date,
                   sum(sl.quantity)::int,
                   sum(sl.unit_price * sl.quantity - sl.discount),
                   sum(CASE WHEN sl.discount > 0 THEN sl.quantity ELSE 0 END)::int,
                   sum(CASE WHEN s.source = 'imported' THEN sl.quantity ELSE 0 END)::int
            FROM sale_lines sl
            JOIN sales s ON s.id = sl.sale_id
            WHERE sl.tenant_id = %s
              AND s.status = 'closed'
              AND s.source IN ('counter', 'imported')
              AND s.recorded_at >= %s
              AND s.recorded_at < %s
            GROUP BY 1, 2, 3
            """,
            [TIMEZONE, str(tenant_id), business_bound(start), business_bound(end)],
        )
        series: dict = {}
        for item_id, location_id, week, units, paid, discounted, imported in cursor:
            one = series.setdefault(item_id, WeekSeries(item_id))
            one.units[week] = one.units.get(week, 0) + int(units or 0)
            one.paid[week] = one.paid.get(week, Decimal("0")) + Decimal(paid or 0)
            one.discounted_units[week] = one.discounted_units.get(week, 0) + int(
                discounted or 0
            )
            one.imported_units[week] = one.imported_units.get(week, 0) + int(
                imported or 0
            )
            if units:
                one.locations.add(location_id)
                key = (location_id, week)
                one.by_location[key] = int(units)
                one.paid_by_location[key] = Decimal(paid or 0)
                one.discounted_by_location[key] = int(discounted or 0)
                one.imported_by_location[key] = int(imported or 0)
        return series


def read_returns(tenant_id, start: date, end: date) -> dict:
    """Units credited back per item per week, so a return is netted out.

    **Returns are not demand** (*Exclusions*), and a credited line is still a
    `sale_lines` row: `sale_lines.quantity` carries a `> 0` CHECK and a return
    lives in its own table, so a fully refunded sale would otherwise enter the
    fit at its full quantity and its full price. The subtraction is bucketed on
    the **return's own** `recorded_at`, because that is when the units came
    back, and it is the same clock rule as everything else here.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT srl.item_id,
                   (date_trunc('week', sr.recorded_at AT TIME ZONE %s))::date,
                   sum(srl.quantity)::int
            FROM sale_return_lines srl
            JOIN sale_returns sr ON sr.id = srl.sale_return_id
            WHERE srl.tenant_id = %s
              AND sr.recorded_at >= %s
              AND sr.recorded_at < %s
            GROUP BY 1, 2
            """,
            [TIMEZONE, str(tenant_id), business_bound(start), business_bound(end)],
        )
        credited: dict = {}
        for item_id, week, units in cursor:
            credited.setdefault(item_id, {})[week] = int(units or 0)
        return credited


def net_returns(series: dict, credited: dict) -> None:
    """Subtract the credited units from the weeks they were credited in.

    A week that nets to zero or below is left at zero rather than negative: the
    fit takes the log of a unit count, and a week the shop refunded more than it
    sold is a week with no demand to measure rather than one with negative
    demand.
    """
    for item_id, weeks in credited.items():
        one = series.get(item_id)
        if one is None:
            continue
        for week, units in weeks.items():
            if week in one.units:
                one.units[week] = max(0, one.units[week] - units)


def read_price_windows(tenant_id) -> dict:
    """Every item's **network-wide** price windows, newest last.

    The list price is what says how many *price points* the window held --
    acceptance 2's "priced at two levels" is a statement about what the pharmacy
    set, not about the weighted average of what a week's tickets happened to
    carry. The regression's own `x` is the paid price; these decide the
    variation floor and which weeks the price list covers at all.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT item_id, effective_from, effective_to, price "
            "FROM item_prices WHERE tenant_id = %s AND location_id IS NULL "
            "ORDER BY item_id, effective_from",
            [str(tenant_id)],
        )
        windows: dict = defaultdict(list)
        for item_id, opens, closes, price in cursor:
            windows[item_id].append((opens, closes, Decimal(price)))
        return windows


def price_point(windows, week: date) -> Decimal | None:
    """The list price that covered most of one week, or `None` if none did.

    The modal day rather than the Monday's price: a repricing that lands on a
    Wednesday would otherwise label the whole week with the price it carried for
    two days, and that mislabelling lands precisely on the weeks the estimator
    most depends on.
    """
    tally: dict[Decimal, int] = {}
    for offset in range(7):
        day = week + timedelta(days=offset)
        for opens, closes, price in windows:
            if opens <= day and (closes is None or closes > day):
                tally[price] = tally.get(price, 0) + 1
                break
    if not tally:
        return None
    return max(tally.items(), key=lambda pair: (pair[1], pair[0]))[0]


# ---------------------------------------------------------------------------
# The stock-out exclusion
# ---------------------------------------------------------------------------


def read_stockout_weeks(tenant_id, item_ids, start: date, today: date) -> dict:
    """The weeks each item stood empty across the network, replayed from moves.

    **A stock-out reads as a demand collapse at an unchanged price**, which
    biases β toward zero -- the item looks more inelastic than it is -- and this
    engine raises the price of inelastic items. Left in, the model's confident
    recommendation is to raise the price of the product the sede could not
    supply. It is the most damaging failure mode in the stage, and it is why
    S3's append-only ledger is a dependency rather than a nicety: no other table
    in the system can answer *was this out of stock on the 14th*.

    **The reading is the network's, not one sede's, and that is a deliberate
    refinement of "at a contributing location".** The fit this exclusion
    protects is at the network grain, where units are summed across sedes: one
    sede of six empty for an afternoon moves that sum by a fraction, and
    dropping the whole week for it would throw away most of a 26-week window on
    any reference with a thin sede -- withholding an estimate for a reason that
    has nothing to do with price. A week is excluded when **every contributing
    sede** was at or below zero on the same day, which is the condition under
    which the network genuinely could not supply the demand it was asked for.
    The per-sede fits below apply the per-sede reading, where it is the right
    one.

    The balance is walked **forwards** from the window's opening position -- the
    current projection less everything that has moved since -- because there is
    no stored history of a shelf level and reconstructing it from the ledger is
    the only honest way to know what was on it in March.
    """
    if not item_ids:
        return {}
    ids = [str(one) for one in item_ids]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT item_id, location_id, sum(quantity)::int FROM stock_on_hand "
            "WHERE tenant_id = %s AND item_id = ANY(%s) GROUP BY 1, 2",
            [str(tenant_id), ids],
        )
        on_hand = {(row[0], row[1]): int(row[2] or 0) for row in cursor}

        cursor.execute(
            "SELECT item_id, location_id, (recorded_at AT TIME ZONE %s)::date, "
            "       sum(quantity)::int "
            "FROM stock_moves "
            "WHERE tenant_id = %s AND item_id = ANY(%s) AND recorded_at >= %s "
            "GROUP BY 1, 2, 3",
            [TIMEZONE, str(tenant_id), ids, business_bound(start)],
        )
        moves: dict = defaultdict(dict)
        for item_id, location_id, day, quantity in cursor:
            moves[(item_id, location_id)][day] = int(quantity or 0)

    empty_days: dict = defaultdict(lambda: defaultdict(set))
    for key, per_day in moves.items():
        item_id, location_id = key
        balance = on_hand.get(key, 0) - sum(per_day.values())
        day = start
        while day <= today:
            balance += per_day.get(day, 0)
            if balance <= 0:
                empty_days[item_id][location_id].add(day)
            day += timedelta(days=1)
    return empty_days


def stockout_weeks(empty_by_location, contributing, start: date, today: date) -> set:
    """The weeks in which every contributing sede was empty on the same day."""
    if not contributing:
        return set()
    sets = [empty_by_location.get(one, set()) for one in contributing]
    if any(not one for one in sets):
        # A contributing sede that was never empty means the network was never
        # wholly out, whatever the others did.
        return set()
    shared = set.intersection(*sets)
    return {day - timedelta(days=day.weekday()) for day in shared}


# ---------------------------------------------------------------------------
# The fit
# ---------------------------------------------------------------------------


class Fit:
    """What one regression answered. Null `beta` means it was not run."""

    __slots__ = ("beta", "r2", "std_error", "ci_low", "ci_high")

    def __init__(self, beta=None, r2=None, std_error=None, ci_low=None, ci_high=None):
        self.beta = beta
        self.r2 = r2
        self.std_error = std_error
        self.ci_low = ci_low
        self.ci_high = ci_high


#: Student's *t* at 90% two-sided, by degrees of freedom. A short table rather
#: than a `scipy` dependency for one number, and the normal 1,645 past 30 -- the
#: difference beyond that is smaller than the third decimal of a coefficient
#: nobody reads to three places.
_T90 = {
    1: 6.314,
    2: 2.920,
    3: 2.353,
    4: 2.132,
    5: 2.015,
    6: 1.943,
    7: 1.895,
    8: 1.860,
    9: 1.833,
    10: 1.812,
    11: 1.796,
    12: 1.782,
    13: 1.771,
    14: 1.761,
    15: 1.753,
    16: 1.746,
    17: 1.740,
    18: 1.734,
    19: 1.729,
    20: 1.725,
    22: 1.717,
    24: 1.711,
    26: 1.706,
    28: 1.701,
    30: 1.697,
}


def t90(df: int) -> float:
    if df <= 0:
        return 0.0
    if df in _T90:
        return _T90[df]
    if df > 30:
        return 1.645
    return _T90[min(key for key in _T90 if key > df)]


def solve(matrix, vector):
    """Gaussian elimination with partial pivoting. `None` where it is singular.

    Small and explicit rather than a linear-algebra dependency: the design
    matrix here is at most fifteen columns wide -- an intercept, `ln(price)`, a
    trend and eleven month effects -- and a 15×15 solve is arithmetic somebody
    can read.
    """
    size = len(vector)
    rows = [list(matrix[index]) + [vector[index]] for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        for below in range(column + 1, size):
            factor = rows[below][column] / divisor
            if factor == 0:
                continue
            for across in range(column, size + 1):
                rows[below][across] -= factor * rows[column][across]
    answer = [0.0] * size
    for column in range(size - 1, -1, -1):
        total = rows[column][size] - sum(
            rows[column][across] * answer[across] for across in range(column + 1, size)
        )
        answer[column] = total / rows[column][column]
    return answer


def least_squares(design, response, *, beta_column=1) -> Fit:
    """OLS by the normal equations, with the one coefficient this stage reads.

    `r2`, the standard error of β and its 90% interval come back with it,
    because a coefficient without them is a number an owner is asked to trust
    rather than to check -- and the record panel is built to show all four.
    """
    rows = len(response)
    columns = len(design[0])
    if rows <= columns:
        return Fit()

    gram = [
        [
            sum(design[row][left] * design[row][right] for row in range(rows))
            for right in range(columns)
        ]
        for left in range(columns)
    ]
    moment = [
        sum(design[row][column] * response[row] for row in range(rows))
        for column in range(columns)
    ]
    coefficients = solve(gram, moment)
    if coefficients is None:
        return Fit()

    fitted = [
        sum(design[row][column] * coefficients[column] for column in range(columns))
        for row in range(rows)
    ]
    mean = sum(response) / rows
    residual_sum = sum((response[row] - fitted[row]) ** 2 for row in range(rows))
    total_sum = sum((value - mean) ** 2 for value in response)
    if total_sum <= 0:
        return Fit()

    df = rows - columns
    variance = residual_sum / df if df > 0 else 0.0
    unit = [1.0 if index == beta_column else 0.0 for index in range(columns)]
    inverse_column = solve(gram, unit)
    if inverse_column is None:
        return Fit()
    beta_variance = variance * inverse_column[beta_column]
    std_error = math.sqrt(beta_variance) if beta_variance > 0 else 0.0
    half = t90(df) * std_error
    beta = coefficients[beta_column]
    return Fit(
        beta=beta,
        r2=max(0.0, 1.0 - residual_sum / total_sum),
        std_error=std_error,
        ci_low=beta - half,
        ci_high=beta + half,
    )


def design_matrix(weeks, prices, first_week: date):
    """`[1, ln(price), t, month effects]`, one row per surviving week.

    The linear trend absorbs slow drift. **Month effects are included only when
    the surviving weeks span three or more distinct months** -- with two months
    a month dummy is a second intercept for half the sample, and it eats the
    trend rather than a season.

    **Seasonality is only partly handled, and this says so.** A week-of-year
    term is not estimable from 26 weeks and this module does not pretend
    otherwise. The bias that leaves points at *withholding*: a price rise that
    coincided with a seasonal fall reads as elastic, which suppresses a raise
    rather than producing a bad one. A second year of history is what fixes it,
    and nothing in the schema changes when it arrives.
    """
    months = sorted({week.month for week in weeks})
    # **Month effects have to be afforded, not merely justified.** Twenty-six
    # weeks span six or seven months, so "three or more distinct months" is true
    # of almost every window -- and five month dummies plus an intercept and a
    # trend on eighteen weekly observations leaves `ln(price)` with almost no
    # variation of its own. The standard error on β then runs to tens, the
    # interval covers zero by a mile, and the fit is arithmetic rather than
    # evidence. So they are included only where the window can pay for them at
    # `OBSERVATIONS_PER_PARAMETER` weeks apiece; where it cannot, the linear
    # trend carries the drift alone and the seasonality this leaves unmodelled
    # is stated rather than pretended away (see below).
    dummies = months[1:] if len(months) >= 3 else []
    while dummies and len(weeks) < OBSERVATIONS_PER_PARAMETER * (3 + len(dummies)):
        dummies = dummies[:-1]
    design = []
    for week in weeks:
        row = [
            1.0,
            math.log(float(prices[week])),
            float((week - first_week).days) / 7.0,
        ]
        row.extend(1.0 if week.month == month else 0.0 for month in dummies)
        design.append(row)
    return design


# ---------------------------------------------------------------------------
# The estimate, floor by floor
# ---------------------------------------------------------------------------


class Estimate:
    """One item's answer at one grain: the fit, or the floor it missed."""

    __slots__ = (
        "status",
        "elasticity",
        "r2",
        "observations",
        "std_error",
        "ci_low",
        "ci_high",
        "distinct_prices",
        "price_dispersion",
        "weeks_excluded_stockout",
        "weeks_excluded_promo",
        "imported_share",
        "confidence",
        "trailing_units",
    )

    def __init__(self, status, **fields):
        self.status = status
        self.elasticity = None
        self.r2 = None
        self.observations = 0
        self.std_error = None
        self.ci_low = None
        self.ci_high = None
        self.distinct_prices = 0
        self.price_dispersion = None
        self.weeks_excluded_stockout = None
        self.weeks_excluded_promo = None
        self.imported_share = None
        self.confidence = ""
        self.trailing_units = None
        for key, value in fields.items():
            setattr(self, key, value)

    @property
    def qualifies(self) -> bool:
        """Whether this estimate may **price** the reference rather than only
        describe it. A `low` estimate never becomes an elasticity proposal, so
        an elasticity-backed suggestion is only ever `high` or `medium`."""
        return self.elasticity is not None and self.confidence in (
            Confidence.HIGH,
            Confidence.MEDIUM,
        )

    @property
    def reads_elastic(self) -> bool:
        """Point `β ≤ −1`. Weak evidence may **stop** a move and may not start
        one, which is what `elastic_veto` exists to say."""
        return self.elasticity is not None and Decimal(str(self.elasticity)) <= -1


def could_clear_floors(series: WeekSeries, windows) -> bool:
    """Whether this item is worth the stock-out replay at all.

    **Exact, not a heuristic.** Every exclusion only ever *removes* weeks, so a
    reference short of the eight-week floor before them is short of it after,
    and one carrying a single price point cannot gain a second by losing weeks.
    Pre-filtering on those two facts is what keeps the replay off four thousand
    references that could never have been estimated -- and it is why
    `weeks_excluded_stockout` is null rather than zero on the ones it skips: the
    replay did not run, which is not the same as finding nothing.
    """
    weeks = [week for week, units in series.units.items() if units > 0]
    if len(weeks) < MIN_WEEKS:
        return False
    points = {price_point(windows, week) for week in weeks}
    points.discard(None)
    return len(points) >= MIN_DISTINCT_PRICES


def estimate(series, windows, *, excluded_weeks, first_week: date) -> Estimate:
    """One grain's estimate: the exclusions, the floors, then the fit.

    The exclusions are applied **in the order the stage document states them**,
    and the counts they produce are stored and shown -- so a reader can see how
    much of the window survived rather than being told a number and asked to
    accept it.
    """
    weeks_with_sales = sorted(week for week, units in series.units.items() if units > 0)
    if not weeks_with_sales:
        return Estimate(ElasticityStatus.NO_SALES)

    stockout = sorted(week for week in weeks_with_sales if week in excluded_weeks)
    surviving = [week for week in weeks_with_sales if week not in excluded_weeks]

    promo = []
    kept = []
    for week in surviving:
        units = series.units[week]
        discounted = series.discounted_units.get(week, 0)
        if units and Decimal(discounted) / Decimal(units) > PROMO_UNIT_SHARE:
            promo.append(week)
        else:
            kept.append(week)

    # A week the price list does not cover has an unknown price, and an imputed
    # one is an invented observation.
    points = {}
    covered = []
    for week in kept:
        point = price_point(windows, week)
        if point is None:
            continue
        points[week] = point
        covered.append(week)

    counts = {
        "weeks_excluded_stockout": len(stockout),
        "weeks_excluded_promo": len(promo),
    }
    observed = {
        week: (series.paid[week] / Decimal(series.units[week])) for week in covered
    }
    imported = sum(series.imported_units.get(week, 0) for week in covered)
    total_units = sum(series.units[week] for week in covered)
    share = (
        (Decimal(imported) / Decimal(total_units)).quantize(Decimal("0.001"))
        if total_units
        else None
    )

    if len(covered) < MIN_WEEKS:
        return Estimate(
            ElasticityStatus.INSUFFICIENT_OBSERVATIONS,
            observations=len(covered),
            distinct_prices=len(set(points.values())),
            imported_share=share,
            **counts,
        )

    tally: dict[Decimal, int] = {}
    for week in covered:
        tally[points[week]] = tally.get(points[week], 0) + 1
    ranked = sorted(tally.values(), reverse=True)
    dispersion = _dispersion([observed[week] for week in covered])
    enough_variation = (
        len(tally) >= MIN_DISTINCT_PRICES
        and len(ranked) >= 2
        and ranked[0] >= MIN_WEEKS_PER_PRICE
        and ranked[1] >= MIN_WEEKS_PER_PRICE
        and dispersion is not None
        and dispersion >= MIN_PRICE_DISPERSION
    )
    if not enough_variation:
        return Estimate(
            ElasticityStatus.INSUFFICIENT_VARIATION,
            observations=len(covered),
            distinct_prices=len(tally),
            price_dispersion=dispersion,
            imported_share=share,
            **counts,
        )

    ordered = sorted(covered)
    fit = least_squares(
        design_matrix(ordered, observed, first_week),
        [math.log(float(series.units[week])) for week in ordered],
    )
    if fit.beta is None or not _is_a_demand_response(fit.beta):
        # A singular design; a coefficient of exactly zero, which is a claim of
        # perfect inelasticity and is the one number this table must never hold;
        # or a coefficient that is not a demand curve at all. The variation that
        # exists is confounded with something this fit cannot separate it from,
        # and `insufficient_variation` is the honest reading of that -- the
        # window held prices, and none of what they explain is price.
        return Estimate(
            ElasticityStatus.INSUFFICIENT_VARIATION,
            observations=len(covered),
            distinct_prices=len(tally),
            price_dispersion=dispersion,
            imported_share=share,
            **counts,
        )

    r2 = Decimal(str(fit.r2)).quantize(Decimal("0.0001"))
    observations = len(covered)
    width = Decimal(str(fit.ci_high - fit.ci_low)).quantize(Decimal("0.0001"))
    return Estimate(
        ElasticityStatus.ESTIMATED,
        elasticity=Decimal(str(fit.beta)).quantize(Decimal("0.0001")),
        r2=r2,
        observations=observations,
        std_error=Decimal(str(fit.std_error)).quantize(Decimal("0.0001")),
        ci_low=Decimal(str(fit.ci_low)).quantize(Decimal("0.0001")),
        ci_high=Decimal(str(fit.ci_high)).quantize(Decimal("0.0001")),
        distinct_prices=len(tally),
        price_dispersion=dispersion,
        imported_share=share,
        confidence=band(
            observations=observations,
            r2=r2,
            interval_width=width,
            imported_share=share,
            ci_high=fit.ci_high,
        ),
        **counts,
    )


def _is_a_demand_response(beta: float) -> bool:
    value = Decimal(str(beta))
    return value < 0 and abs(value) <= MAX_PLAUSIBLE_ELASTICITY


def band(*, observations, r2, interval_width, imported_share, ci_high) -> str:
    """`high`, `medium` or `low`, and the band never travels without its inputs.

    The record panel renders `observations` and `r2` beside the band always: a
    band on its own is how a model launders its uncertainty.
    """
    del ci_high  # `high` reads the interval's *width*, below; `medium` does not
    if (
        observations >= HIGH_OBSERVATIONS
        and r2 >= HIGH_R2
        and interval_width <= HIGH_INTERVAL_WIDTH
    ):
        reading = Confidence.HIGH
    elif observations >= MIN_OFFER_OBSERVATIONS and r2 >= MIN_OFFER_R2:
        reading = Confidence.MEDIUM
    else:
        reading = Confidence.LOW
    if (
        reading == Confidence.HIGH
        and imported_share is not None
        and imported_share > IMPORTED_CAP_SHARE
    ):
        return Confidence.MEDIUM
    return reading


def elasticity_band(elasticity) -> str:
    """`inelastic`, `unit`, `elastic` or `unknown` -- the reading kept on
    `items.custom.pricing` so a later surface need not re-derive it."""
    if elasticity is None:
        return "unknown"
    magnitude = abs(Decimal(str(elasticity)))
    low, high = UNIT_BAND
    if magnitude < low:
        return "inelastic"
    if magnitude <= high:
        return "unit"
    return "elastic"


def _dispersion(prices) -> Decimal | None:
    """The standard deviation of `ln(price)` over the surviving weeks."""
    logs = [math.log(float(one)) for one in prices if one and one > 0]
    if len(logs) < 2:
        return None
    mean = sum(logs) / len(logs)
    variance = sum((value - mean) ** 2 for value in logs) / (len(logs) - 1)
    return Decimal(str(math.sqrt(variance))).quantize(Decimal("0.00001"))
