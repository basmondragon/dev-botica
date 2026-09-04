"""The v1 demand forecast, in three regimes, stated so it can be debugged (§1).

**What this is not is a black box.** The method is here, the three regimes are
here, the per-item thresholds between them are here, and what a suggestion means
when there is no history at all is here -- so when a suggestion is wrong on a
Monday, the person looking at it can find out why before lunch.

Three regimes, decided **per item and per sede and never per tenant**. A
fast-moving analgesic at Chapinero earns a real forecast weeks before a slow
dermocosmetic at Usme does, so the regime is decided for each `(item, location)`
pair on that pair's own signal and recomputed on every refresh. A tenant-wide
"eight weeks of history" switch would promote both on the same morning and be
wrong about one of them.

| basis        | when                                                    |
|--------------|----------------------------------------------------------|
| `parametric` | no usable week of sales for that item at that sede        |
| `learning`   | at least one, but the estimate has not earned promotion   |
| `learned`    | `learned_min_weeks` usable weeks and the relative standard error at or below `learned_max_rse` |

**A usable week is a week the item sold in and did not stand empty.** Selling
nothing while having nothing is not demand information, and counting it as a
zero is the classic way a forecasting system talks itself out of ever restocking
the thing that keeps running out -- so a week the shelf stood at or below zero
for more than `STOCKOUT_DAYS` days is dropped. The *mean* runs over every
uncensored week, zeros included, because a week in stock with no sale is a real
observation of no demand; the *regime* counts the weeks that actually sold,
because those are the observations an estimate is made of.

**Nothing here writes a `stock_moves` row and nothing here reads a sale without
naming `source`.** Both queries below state `source` explicitly: the forecast is
one of exactly two consumers that read `imported` history at all, and it says so
rather than inheriting it (*Data*).
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.utils import timezone

from core.models import (
    ForecastBasis,
    Item,
    Location,
    PolicySource,
    StockPolicy,
    Tenant,
)
from core.purchasing import settings as purchasing_settings

logger = logging.getLogger(__name__)

#: The algorithm this module implements. It travels onto every
#: `demand_forecasts` row and onto every `purchase_order_lines` row at
#: generation, together with the run's own date -- so a regime that changed is
#: attributable to a dated run rather than to "some time last month".
ALGORITHM = "forecast-v1"

#: The window every estimate is made over. Half a year: long enough for a
#: seasonal reading of a category and short enough that a reference whose
#: rotation changed in March is not still being planned against January.
WINDOW_WEEKS = 26

#: How many days at or below zero censor a week. Two, because a shelf that was
#: empty over a weekend did not have the week's demand available to it.
STOCKOUT_DAYS = 2

#: The exponential mean's half-life, in weeks. Four weeks: the last month counts
#: for twice what the month before it does.
HALF_LIFE_WEEKS = 4

#: Where each week is clipped before it enters the mean, as a percentile of that
#: item's own weekly history. One wholesale-sized ticket must not become the
#: baseline for the next six months.
WINSOR_PERCENTILE = 95

#: How many usable weeks count as a full mark for the history half of
#: `confidence`. A quarter of clean observations is as much as this model can
#: use; more weeks past that do not make the estimate better, they make it
#: older.
CONFIDENCE_FULL_WEEKS = 12

#: The three rendered bands (§B.7.3, *Data*).
BAND_HIGH = Decimal("0.70")
BAND_MEDIUM = Decimal("0.40")

#: The fixed pair the parametric regime carries, and it is **always Baja**: a
#: parameter a pharmacist typed is a deliberate instruction, not evidence.
PARAMETRIC_POLICY_CONFIDENCE = Decimal("0.20")
PARAMETRIC_CATEGORY_CONFIDENCE = Decimal("0.10")

#: The ceiling a `learning` item's confidence is capped at, so it never renders
#: **Alta**.
LEARNING_CONFIDENCE_CAP = Decimal("0.65")

#: Above this share of imported weeks the band drops one step: an imported week
#: cannot be censored for stockouts -- there are no `stock_moves` behind it --
#: and an uncensored stockout week is a mean that is quietly too low.
IMPORTED_MAJORITY = Decimal("0.5")

#: The category multiplier's own two guards. It needs a full year of history to
#: exist at all, and enough of the category observed in that week to be worth
#: reading -- a season inferred from two lines is not a season.
SEASONAL_MIN_DAYS = 364
SEASONAL_MIN_OBSERVATIONS = 5
SEASONAL_PEAK = Decimal("1.25")

#: `trend` beneath this in absolute value is a stable rotation; the coefficient
#: of variation beneath the second is a predictable chronic.
STABLE_TREND = Decimal("0.10")
CHRONIC_VARIATION = Decimal("0.25")

#: Beyond this many days of cover, capital is tied up rather than deployed. It
#: is design-system §B.7.4's own upper `Cobertura` band and is shared with the
#: colour the cell renders in, so the number and the colour cannot disagree.
OVERSTOCK_DAYS = Decimal("90")

#: The business calendar every day-grain figure in this module is evaluated in.
#: A week is a week the droguería had, not a week UTC had.
TIMEZONE = "America/Bogota"

#: The namespace a `demand_forecasts` row's id is derived from. **Deterministic
#: on purpose**, for the same reason the policy namespace below is: the row is
#: an upsert keyed on `(tenant, item, location)`, so a rewritten row keeps the
#: id it had -- and it is what lets the demo seed's guard enumerate exactly the
#: rows a refresh would write rather than reading the table back.
FORECAST_NAMESPACE = uuid.UUID("7c2e93b4-0a51-5d68-b4f2-6ea31d80c957")


def forecast_id(tenant_id, location_id, item_id) -> uuid.UUID:
    return uuid.uuid5(FORECAST_NAMESPACE, f"{tenant_id}:{location_id}:{item_id}")


#: The namespace a `source = model` policy row's id is derived from.
#: **Deterministic on purpose**: the row is an upsert keyed on
#: `(tenant, item, location)`, so giving it an id that is a pure function of that
#: key means a rewritten row keeps the id it had -- and it is what lets the demo
#: seed's guard enumerate exactly the rows this stage's refresh would write.
POLICY_NAMESPACE = uuid.UUID("b3a5f0c1-27d6-5e94-8f3b-1c9084ae62d7")


def policy_id(tenant_id, location_id, item_id) -> uuid.UUID:
    return uuid.uuid5(POLICY_NAMESPACE, f"{tenant_id}:{location_id}:{item_id}")


@dataclass
class Signal:
    """One `(item, location)` pair's measured demand, before any policy."""

    item_id: object
    #: Week start (Monday, business calendar) -> units sold.
    weeks: dict = field(default_factory=dict)
    #: The same, restricted to `source = 'imported'`.
    imported: dict = field(default_factory=dict)
    #: Week starts dropped for standing empty.
    censored: set = field(default_factory=set)


@dataclass
class Estimate:
    """What the model concluded about one `(item, location)` pair."""

    item_id: object
    basis: str
    weekly_sales: Decimal | None
    trend: Decimal | None
    coverage_days: Decimal | None
    reorder_point: int
    safety_stock: int
    confidence: Decimal
    usable_weeks: int | None
    variation: Decimal | None
    imported_share: Decimal | None
    #: Not stored: the generation job needs σ and the on-hand it was computed
    #: against, and reading them back off the row would be a second query for
    #: numbers this pass already had.
    sigma: Decimal = Decimal("0")
    on_hand: int = 0


def band(confidence) -> str:
    """**Alta**, **Media** or **Baja** -- the rendered reading of a stored 0–1."""
    value = Decimal(str(confidence or 0))
    if value >= BAND_HIGH:
        return "alta"
    if value >= BAND_MEDIUM:
        return "media"
    return "baja"


# ---------------------------------------------------------------------------
# Reading the window
# ---------------------------------------------------------------------------


def window_start(today: date) -> date:
    """The Monday `WINDOW_WEEKS` **complete** weeks back.

    The week in progress is never one of them, so the window opens a full
    `WINDOW_WEEKS` before this Monday rather than one short of it.
    """
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(weeks=WINDOW_WEEKS)


def _weekly_sales(tenant_id, location_id, start, end) -> dict:
    """Units per item per week at one sede, with the imported half beside it.

    **`source` is named explicitly**, and both values are asked for on purpose:
    the forecast is one of exactly two consumers in the product that read
    `imported` history at all, and a query over `sales` that does not name
    `source` is a defect (*Data*).
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT sl.item_id,
                   (date_trunc('week', s.occurred_at AT TIME ZONE %s))::date,
                   sum(sl.quantity)::int,
                   sum(CASE WHEN s.source = 'imported' THEN sl.quantity ELSE 0 END)::int
            FROM sale_lines sl
            JOIN sales s ON s.id = sl.sale_id
            WHERE sl.tenant_id = %s
              AND sl.location_id = %s
              AND s.status = 'closed'
              AND s.source IN ('counter', 'imported')
              AND s.occurred_at >= %s
              AND s.occurred_at < %s
            GROUP BY 1, 2
            """,
            [TIMEZONE, str(tenant_id), str(location_id), start, end],
        )
        signals: dict = {}
        for item_id, week, units, imported in cursor.fetchall():
            signal = signals.setdefault(item_id, Signal(item_id=item_id))
            signal.weeks[week] = int(units or 0)
            if imported:
                signal.imported[week] = int(imported)
        return signals


def on_hand_at(tenant_id, location_id) -> dict:
    """Units on the shelf per item at one sede, summed across lots."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT item_id, sum(quantity)::int FROM stock_on_hand "
            "WHERE tenant_id = %s AND location_id = %s GROUP BY 1",
            [str(tenant_id), str(location_id)],
        )
        return {row[0]: int(row[1] or 0) for row in cursor.fetchall()}


def _daily_moves(tenant_id, location_id, start) -> dict:
    """Net movement per item per day at one sede since the window opened.

    Read on `recorded_at`, which is the accounting clock rule 8 fixes and the
    clock the inherited `stock_moves (tenant_id, location_id, item_id,
    recorded_at)` index orders by. The demand weeks above bucket on
    `occurred_at`; the two diverge only for events a till recorded while it was
    offline, and nobody has ruled on whether they should (*Gated on*).
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT item_id, (recorded_at AT TIME ZONE %s)::date, sum(quantity)::int "
            "FROM stock_moves WHERE tenant_id = %s AND location_id = %s "
            "AND recorded_at >= %s GROUP BY 1, 2",
            [TIMEZONE, str(tenant_id), str(location_id), start],
        )
        moves: dict = {}
        for item_id, day, quantity in cursor.fetchall():
            moves.setdefault(item_id, {})[day] = int(quantity or 0)
        return moves


def _censor(signal, on_hand, moves, start, today):
    """Drop the weeks this item stood empty at this sede.

    The balance is walked **forwards** from the window's opening position, which
    is the current projection less everything that has moved since -- there is
    no stored history of a shelf level, and reconstructing it from the ledger is
    the only honest way to know what was on it in March.
    """
    total_delta = sum(moves.values())
    balance = on_hand - total_delta
    empty: dict = {}
    day = start
    while day <= today:
        balance += moves.get(day, 0)
        if balance <= 0:
            week = day - timedelta(days=day.weekday())
            empty[week] = empty.get(week, 0) + 1
        day += timedelta(days=1)
    signal.censored = {week for week, days in empty.items() if days > STOCKOUT_DAYS}


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------


def _percentile(values, percentile):
    """The nearest-rank percentile of a series. No numpy for one number."""
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    rank = max(1, int(round(percentile / 100 * len(ordered))))
    return Decimal(str(ordered[min(rank, len(ordered)) - 1]))


def _weighted_mean(series):
    """The exponentially weighted mean, newest week weighted 1.

    `series` is `[(age_in_weeks, units)]`. A four-week half-life makes the last
    month count for twice what the month before it does, which is what keeps an
    estimate responsive to a rotation that changed without letting one week
    rewrite it.
    """
    numerator = Decimal("0")
    denominator = Decimal("0")
    for age, units in series:
        weight = Decimal(str(0.5 ** (age / HALF_LIFE_WEEKS)))
        numerator += weight * Decimal(str(units))
        denominator += weight
    if denominator == 0:
        return Decimal("0")
    return (numerator / denominator).quantize(Decimal("0.001"))


def _sigma(values):
    """The sample standard deviation of a weekly series."""
    if len(values) < 2:
        return Decimal("0")
    mean = sum(Decimal(str(one)) for one in values) / Decimal(len(values))
    variance = sum((Decimal(str(one)) - mean) ** 2 for one in values) / Decimal(
        len(values) - 1
    )
    return Decimal(str(float(variance) ** 0.5)).quantize(Decimal("0.001"))


def _confidence(*, usable_weeks, variation, imported_share, basis):
    """Three inputs, one number, and the band it renders in.

    Weeks of usable history after censoring, the coefficient of variation of
    weekly demand, and the share of the window that is imported rather than
    Botica's own. The first two are averaged; the third does not enter the
    arithmetic at all -- it **drops the band**, which is a statement about how
    much the reading can be trusted rather than a penalty on the estimate.
    """
    weeks_score = min(
        Decimal("1"), Decimal(usable_weeks) / Decimal(CONFIDENCE_FULL_WEEKS)
    )
    noise_score = max(Decimal("0"), min(Decimal("1"), Decimal("1") - variation))
    value = ((weeks_score + noise_score) / 2).quantize(Decimal("0.001"))

    if basis == ForecastBasis.LEARNING:
        value = min(value, LEARNING_CONFIDENCE_CAP)

    if imported_share is not None and imported_share > IMPORTED_MAJORITY:
        # One band down, at the top of the band below rather than by a fixed
        # subtraction: the bands are what a person reads, so the drop has to be
        # expressed in them or an **Alta** at 0,71 would still render **Alta**.
        if value >= BAND_HIGH:
            value = BAND_HIGH - Decimal("0.01")
        elif value >= BAND_MEDIUM:
            value = BAND_MEDIUM - Decimal("0.01")
    return value


def estimate(signal, *, on_hand, options, lead_time_days, today) -> Estimate | None:
    """One pair's estimate from its censored weekly series, or None where the
    pair has no usable week at all and belongs to the parametric regime."""
    current = today - timedelta(days=today.weekday())
    usable = {
        week: units
        for week, units in signal.weeks.items()
        if week not in signal.censored and week < current
    }
    if not usable:
        return None

    # **The week in progress is excluded, and it has to be.** Monday's refresh
    # sees one day of this week; letting it in at full weight would drag the
    # mean down by whatever fraction of the week has not happened yet, and the
    # same partial week would sit in the numerator of `trend` and read as a
    # collapse in demand every Monday morning.
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=1)
    start = window_start(today)

    # Every uncensored complete week of the window, zeros included: a week in
    # stock with no sale is a real observation of no demand.
    series: list[tuple[int, int]] = []
    week = start
    while week <= monday:
        if week not in signal.censored:
            series.append((int((monday - week).days / 7), signal.weeks.get(week, 0)))
        week += timedelta(weeks=1)

    cap = _percentile([units for _, units in series], WINSOR_PERCENTILE)
    winsorised = [(age, min(Decimal(str(units)), cap)) for age, units in series]

    weekly = _weighted_mean(winsorised)
    values = [units for _, units in winsorised]
    sigma = _sigma(values)

    # `trend` is the last four weeks over the prior eight, minus one, clipped to
    # ±1. **It drives reason codes and nothing else** -- it does not scale a
    # quantity, because a trend read off eleven observations is a direction and
    # not a multiplier.
    recent = [units for age, units in winsorised if age < 4]
    prior = [units for age, units in winsorised if 4 <= age < 12]
    trend = Decimal("0")
    if recent and prior:
        prior_mean = sum(prior) / Decimal(len(prior))
        if prior_mean > 0:
            recent_mean = sum(recent) / Decimal(len(recent))
            trend = max(
                Decimal("-1"),
                min(Decimal("1"), (recent_mean / prior_mean) - Decimal("1")),
            )
    trend = trend.quantize(Decimal("0.001"))

    sold_weeks = [week for week, units in usable.items() if units > 0]
    variation = sigma / weekly if weekly > 0 else Decimal("99")
    count = len(sold_weeks)
    rse = (
        variation / Decimal(str(count**0.5))
        if weekly > 0 and count > 0
        else Decimal("99")
    )

    imported_units = sum(
        units
        for week, units in signal.imported.items()
        if week not in signal.censored and week < current
    )
    total_units = sum(usable.values())
    imported_share = (
        (Decimal(imported_units) / Decimal(total_units)).quantize(Decimal("0.001"))
        if total_units > 0
        else Decimal("0")
    )

    promoted = count >= int(options["learned_min_weeks"]) and rse <= Decimal(
        str(options["learned_max_rse"])
    )
    basis = ForecastBasis.LEARNED if promoted else ForecastBasis.LEARNING

    confidence = _confidence(
        usable_weeks=count,
        variation=min(variation, Decimal("1")),
        imported_share=imported_share,
        basis=basis,
    )

    z = purchasing_settings.z_for(options["service_level"])
    lead_weeks = Decimal(str(lead_time_days)) / Decimal(7)
    safety = (z * sigma * Decimal(str(float(lead_weeks) ** 0.5))).quantize(Decimal("1"))
    reorder = (weekly * lead_weeks + safety).quantize(Decimal("1"))

    coverage = (
        (Decimal(on_hand) / (weekly / Decimal(7))).quantize(Decimal("0.1"))
        if weekly > 0
        else None
    )

    return Estimate(
        item_id=signal.item_id,
        basis=basis,
        weekly_sales=weekly,
        trend=trend,
        coverage_days=coverage,
        reorder_point=int(max(Decimal("0"), reorder)),
        safety_stock=int(max(Decimal("0"), safety)),
        confidence=confidence,
        usable_weeks=count,
        variation=variation.quantize(Decimal("0.001")),
        imported_share=imported_share,
        sigma=sigma,
        on_hand=on_hand,
    )


# ---------------------------------------------------------------------------
# Demotion, with hysteresis
# ---------------------------------------------------------------------------


def _hold_regime(previous, fresh, options):
    """Keep a `learned` item there until it is *clearly* worse (*Data*).

    An item promoted at 0,35 and demoted at anything above it changes basis on
    alternate mornings, and the `Por qué` column flickering between a learned
    claim and a parametric one is how an administrator learns to distrust both.
    So a demotion needs the relative standard error to pass `learned_demote_rse`
    and not merely to fail `learned_max_rse`.
    """
    if previous != ForecastBasis.LEARNED or fresh.basis == ForecastBasis.LEARNED:
        return fresh
    if fresh.weekly_sales is None or fresh.weekly_sales <= 0:
        return fresh
    count = fresh.usable_weeks or 0
    if count < 1:
        return fresh
    rse = (fresh.variation or Decimal("99")) / Decimal(str(count**0.5))
    if rse > Decimal(str(options["learned_demote_rse"])):
        return fresh
    fresh.basis = ForecastBasis.LEARNED
    fresh.confidence = _confidence(
        usable_weeks=count,
        variation=min(fresh.variation or Decimal("1"), Decimal("1")),
        imported_share=fresh.imported_share,
        basis=ForecastBasis.LEARNED,
    )
    return fresh


# ---------------------------------------------------------------------------
# Seasonality: a year-ago category multiplier, and no fitted model (*Data*)
# ---------------------------------------------------------------------------


def category_multipliers(tenant_id, today) -> dict:
    """`category_id -> ratio` for the same ISO week one year ago.

    **v1 fits no seasonal model and says so.** What it does instead is read what
    a category actually did in this week last year against its own annual mean.
    On a tenant with less than a year of history the answer is empty, and the
    two seasonal reason codes are then unreachable -- which is the point: a
    model that invents a pollen season out of eleven weeks of data is worse than
    one that says `Rotación estable`.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT min(occurred_at), max(occurred_at) FROM sales "
            "WHERE tenant_id = %s AND status = 'closed' "
            "AND source IN ('counter', 'imported')",
            [str(tenant_id)],
        )
        span = cursor.fetchone()
        if not span or span[0] is None:
            return {}
        if (span[1] - span[0]).days < SEASONAL_MIN_DAYS:
            return {}

        monday = today - timedelta(days=today.weekday())
        target = monday - timedelta(weeks=52)
        cursor.execute(
            """
            SELECT i.category_id,
                   sum(CASE WHEN (date_trunc('week', s.occurred_at AT TIME ZONE %s))::date = %s
                            THEN sl.quantity ELSE 0 END)::int,
                   count(DISTINCT CASE WHEN (date_trunc('week', s.occurred_at AT TIME ZONE %s))::date = %s
                            THEN sl.item_id END)::int,
                   sum(sl.quantity)::int,
                   count(DISTINCT (date_trunc('week', s.occurred_at AT TIME ZONE %s))::date)::int
            FROM sale_lines sl
            JOIN sales s ON s.id = sl.sale_id
            JOIN items i ON i.id = sl.item_id
            WHERE sl.tenant_id = %s
              AND s.status = 'closed'
              AND s.source IN ('counter', 'imported')
              AND i.category_id IS NOT NULL
            GROUP BY 1
            """,
            [TIMEZONE, target, TIMEZONE, target, TIMEZONE, str(tenant_id)],
        )
        multipliers = {}
        for (
            category_id,
            week_units,
            observations,
            total_units,
            weeks,
        ) in cursor.fetchall():
            if observations < SEASONAL_MIN_OBSERVATIONS or not weeks or not total_units:
                continue
            mean = Decimal(total_units) / Decimal(weeks)
            if mean <= 0:
                continue
            multipliers[category_id] = (Decimal(week_units) / mean).quantize(
                Decimal("0.001")
            )
        return multipliers


def category_medians(tenant_id) -> dict:
    """`category_id -> (median weekly_sales, item count)` over measured rows.

    Parametric path 2's whole input. It reads `demand_forecasts` rather than
    sales, because what path 2 borrows is the *model's* reading of a category
    and not a raw total -- and it counts only `learning` and `learned` rows,
    which is what makes `category_default_min_items` a threshold on measured
    items rather than on rows.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT i.category_id,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY f.weekly_sales),
                   count(DISTINCT f.item_id)::int
            FROM demand_forecasts f
            JOIN items i ON i.id = f.item_id
            WHERE f.tenant_id = %s
              AND f.basis IN ('learning', 'learned')
              AND f.weekly_sales IS NOT NULL
              AND i.category_id IS NOT NULL
            GROUP BY 1
            """,
            [str(tenant_id)],
        )
        return {
            row[0]: (Decimal(str(row[1] or 0)), int(row[2]))
            for row in cursor.fetchall()
        }


# ---------------------------------------------------------------------------
# The refresh
# ---------------------------------------------------------------------------


def stocked_items(tenant_id, location_id) -> dict:
    """The references this sede actually holds or replenishes.

    A `stock_on_hand` row or a `stock_policies` row is what makes a reference
    one of this sede's: the first says the shelf has held it, the second says a
    pharmacist decided it should. A reference the sede neither stocks nor
    replenishes is a reference this sede has no opinion about, and inventing one
    for it would put four thousand rows of nothing on every forecast.
    """
    held: set = set()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT item_id FROM stock_on_hand "
            "WHERE tenant_id = %s AND location_id = %s",
            [str(tenant_id), str(location_id)],
        )
        held.update(row[0] for row in cursor.fetchall())
        cursor.execute(
            "SELECT DISTINCT item_id FROM stock_policies "
            "WHERE tenant_id = %s AND (location_id = %s OR location_id IS NULL)",
            [str(tenant_id), str(location_id)],
        )
        held.update(row[0] for row in cursor.fetchall())
    return {
        item.id: item
        for item in Item.objects.filter(
            tenant_id=tenant_id, id__in=list(held), active=True, tracks_stock=True
        )
    }


def policies_at(tenant_id, location_id) -> dict:
    """`item_id -> the policy row that governs it here`, sede first.

    **The sede's own row wins over the network-wide one, whole**, which is the
    precedence S3's `Estado` derivation already uses. Two answers to one
    question is how a screen and an order come to disagree about a threshold.
    """
    rows: dict = {}
    for policy in StockPolicy.objects.filter(tenant_id=tenant_id).filter(
        location_id__in=[location_id, None]
    ):
        held = rows.get(policy.item_id)
        if held is None or (held.location_id is None and policy.location_id):
            rows[policy.item_id] = policy
    return rows


def refresh(tenant_id, location_id, *, today=None) -> dict:
    """Recompute every forecast row for one sede, and report what moved.

    Writes `demand_forecasts` and, where the group allows it, `stock_policies`
    rows at `source = model`. **It writes no `stock_moves` row and moves no
    quantity**: a forecast is a reading of a shelf, not a change to one.
    """
    from core.models import DemandForecast

    today = today or timezone.localdate()
    tenant = Tenant.objects.filter(id=tenant_id).first()
    location = Location.objects.filter(id=location_id).first()
    if tenant is None or location is None:
        return {"written": 0}

    options = purchasing_settings.read(tenant)
    start = window_start(today)
    end = today + timedelta(days=1)

    items = stocked_items(tenant_id, location_id)
    on_hand = on_hand_at(tenant_id, location_id)
    signals = _weekly_sales(tenant_id, location_id, start, end)
    moves = _daily_moves(tenant_id, location_id, start)
    for item_id, signal in signals.items():
        _censor(signal, on_hand.get(item_id, 0), moves.get(item_id, {}), start, today)

    leads = lead_times(tenant_id, options)
    policies = policies_at(tenant_id, location_id)
    previous = dict(
        DemandForecast.objects.filter(
            tenant_id=tenant_id, location_id=location_id
        ).values_list("item_id", "basis")
    )

    model_version = f"{ALGORITHM}:{today.isoformat()}"
    stamped = timezone.now()
    estimates: dict = {}
    rows: list[DemandForecast] = []

    for item_id, item in items.items():
        signal = signals.get(item_id)
        held = on_hand.get(item_id, 0)
        computed = None
        if signal is not None:
            computed = estimate(
                signal,
                on_hand=held,
                options=options,
                lead_time_days=leads.get(item_id, options["default_lead_time_days"]),
                today=today,
            )
        if computed is not None:
            computed = _hold_regime(previous.get(item_id), computed, options)
        else:
            # **The parametric regime writes a row too**, with `weekly_sales`,
            # `trend` and `coverage_days` null: a row that says it has no demand
            # estimate is what stops a consumer reading a zero as one (*Jobs*).
            policy = policies.get(item_id)
            computed = Estimate(
                item_id=item_id,
                basis=ForecastBasis.PARAMETRIC,
                weekly_sales=None,
                trend=None,
                coverage_days=None,
                reorder_point=int(policy.reorder_point or 0) if policy else 0,
                safety_stock=0,
                confidence=(
                    PARAMETRIC_POLICY_CONFIDENCE
                    if policy is not None and policy.source == PolicySource.MANUAL
                    else PARAMETRIC_CATEGORY_CONFIDENCE
                ),
                usable_weeks=None,
                variation=None,
                imported_share=None,
                on_hand=held,
            )
        estimates[item_id] = computed
        rows.append(
            DemandForecast(
                id=forecast_id(tenant_id, location_id, item_id),
                tenant_id=tenant_id,
                item_id=item_id,
                location_id=location_id,
                weekly_sales=computed.weekly_sales,
                trend=computed.trend,
                coverage_days=computed.coverage_days,
                reorder_point=computed.reorder_point,
                safety_stock=computed.safety_stock,
                computed_at=stamped,
                model_version=model_version,
                basis=computed.basis,
                confidence=computed.confidence,
                usable_weeks=computed.usable_weeks,
                variation=computed.variation,
                imported_share=computed.imported_share,
            )
        )

    DemandForecast.objects.bulk_create(
        rows,
        batch_size=1000,
        update_conflicts=True,
        update_fields=[
            "weekly_sales",
            "trend",
            "coverage_days",
            "reorder_point",
            "safety_stock",
            "computed_at",
            "model_version",
            "basis",
            "confidence",
            "usable_weeks",
            "variation",
            "imported_share",
            "updated_at",
        ],
        unique_fields=["tenant", "item", "location"],
    )

    written_policies = _write_model_policies(
        tenant_id, location_id, estimates, policies, options
    )
    logger.info(
        "forecast %s at %s: %s rows, %s model policies",
        model_version,
        location.code,
        len(rows),
        written_policies,
    )
    return {
        "written": len(rows),
        "policies": written_policies,
        "model_version": model_version,
    }


def lead_times(tenant_id, options) -> dict:
    """`item_id -> the observed lead time of the supplier that will fill it`.

    The preferred supplier's, falling back to the group's default where that
    supplier has no observation yet. `suppliers.lead_time_days` is network-wide
    at v1 -- a supplier slower to Usme than to Chapinero is averaged into one
    number, which is accepted and named in *Gated on*.
    """
    default = int(options["default_lead_time_days"])
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (si.item_id) si.item_id, s.lead_time_days
            FROM supplier_items si
            JOIN suppliers s ON s.id = si.supplier_id
            WHERE si.tenant_id = %s
            ORDER BY si.item_id, si.is_preferred DESC, s.name
            """,
            [str(tenant_id)],
        )
        return {row[0]: int(row[1]) if row[1] else default for row in cursor.fetchall()}


def _write_model_policies(tenant_id, location_id, estimates, policies, options) -> int:
    """Upsert `stock_policies` at `source = model`, erasing no pharmacist.

    **A row whose `source` is `manual` is left exactly as it stands** -- not
    merged, not partially updated, not overwritten in one column and kept in
    another -- and **a `parametric` forecast writes no row at all**, because a
    parametric reorder point *is* the pharmacist's reorder point and writing it
    back under `source = model` would launder a human parameter into a model
    output and read it back the next morning as though the model had learned it.

    The precedence in `policies_at` is what makes the first half true at both
    scopes: a network-wide manual row governs this sede, so a sede-scoped model
    row written beside it would win on S3's screen and silently replace a
    threshold nobody touched.
    """
    if not options["write_model_stock_policies"]:
        return 0

    target_days = int(options["target_coverage_days"])
    fresh: list[StockPolicy] = []
    #: References where a pharmacist's threshold now governs this sede. A model
    #: row written here on an earlier run would still be sede-scoped, and S3's
    #: `Estado` derivation takes the sede's own row over a network-wide one --
    #: so skipping the write is not enough: a manual row created *after* the
    #: model row would stay shadowed by it until somebody noticed. The model
    #: row is removed instead, which is the only way `source` keeps meaning
    #: what it means on Existencias.
    superseded: list = []
    for item_id, computed in estimates.items():
        standing = policies.get(item_id)
        manual = standing is not None and standing.source == PolicySource.MANUAL
        if manual or computed.basis == ForecastBasis.PARAMETRIC:
            superseded.append(policy_id(tenant_id, location_id, item_id))
        if computed.basis == ForecastBasis.PARAMETRIC:
            continue
        if manual:
            continue
        fresh.append(
            StockPolicy(
                id=policy_id(tenant_id, location_id, item_id),
                tenant_id=tenant_id,
                item_id=item_id,
                location_id=location_id,
                reorder_point=computed.reorder_point,
                target_coverage_days=target_days,
                source=PolicySource.MODEL,
            )
        )
    if superseded:
        StockPolicy.objects.filter(
            tenant_id=tenant_id, id__in=superseded, source=PolicySource.MODEL
        ).delete()
    if not fresh:
        return 0
    StockPolicy.objects.bulk_create(
        fresh,
        batch_size=1000,
        update_conflicts=True,
        update_fields=["reorder_point", "target_coverage_days", "source", "updated_at"],
        unique_fields=["tenant", "item", "location"],
    )
    return len(fresh)


# ---------------------------------------------------------------------------
# The provenance line (§B.8.5, *UI*)
# ---------------------------------------------------------------------------


def training_window(tenant_id, location_id) -> dict:
    """How much sales history the refresh actually read for one sede.

    **Computed, never transcribed.** The drawn `18 meses` is what the handoff's
    own tenant would have produced; on a tenant carrying six months it must read
    `6 meses`, and where the two disagree the drawing yields to the true number.
    Telling a prospect the model trained on eighteen months of their sales on a
    tenant that has none of them is the cheapest possible way to lose a pilot.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT min(occurred_at), max(occurred_at) FROM sales "
            "WHERE tenant_id = %s AND location_id = %s AND status = 'closed' "
            "AND source IN ('counter', 'imported')",
            [str(tenant_id), str(location_id)],
        )
        row = cursor.fetchone()
    if not row or row[0] is None:
        return {"days": 0, "label": ""}
    days = max(1, (row[1] - row[0]).days + 1)
    weeks = max(1, round(days / 7))
    if weeks > 8:
        months = max(1, round(days / 30))
        label = f"{months} mes" if months == 1 else f"{months} meses"
    else:
        label = f"{weeks} semana" if weeks == 1 else f"{weeks} semanas"
    return {"days": days, "label": label}
