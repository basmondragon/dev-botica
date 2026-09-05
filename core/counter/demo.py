"""The `counter` fixture, registered with **S0's** `seed_demo_tenant`.

S4 ships no seed command. It registers one fixture, declares S3's stock as a
dependency, and writes its own stage's tables -- and this one carries more than
its own stage. §1 makes a client's sales history an accelerant and never a
precondition, so where the export does not exist -- the demo, the pilot's first
week, the client whose fifteen-year-old system exports nothing usable --
**synthetic `sales` and `sale_lines` are the only history the product has**. S6's
demand forecast, S7's elasticity, S8's `cross_sell_rules` and every tile on S9's
Panel read those two tables; without this fixture four later stages render empty.

**Every seeded sale consumes stock through S3's ledger service, never around
it** (rule 7, A3), so `stock_on_hand` agrees with the sales that caused it. Two
consequences follow, and both are load-bearing:

*The draw is bounded so that S3's own screen survives.* Existencias is the most
data-dense surface in the product and S3's fixture tunes it to the drawn grid --
4.284 rows, exactly 312 requiring action, all seven states a dozen rows deep. A
fixture that sold from whatever it liked would move rows across state boundaries
and leave S3's screen looking like a bug in someone else's stage. So this one
draws **only from rows S3 planned as `sufficient` or `overstock`**, and only down
to a floor that keeps each row inside its own band. Every action state, every
expiry tier and every deliberate negative-stock exception is left exactly as S3
left it.

*And the draw starts after the shelf was stocked.* The lot trace is read as a
running balance that must never dip below zero (S3's own check), so a sale dated
before the receipt that made it possible is a trace that lies. Each row is
sellable only on days after its own opening move, and **S3 answers when that
was** (`stocked_days_back`) rather than S4 restating the arithmetic: a row whose
whole history is one opening move was stocked a fortnight ago and sells only in
the last fortnight, while a row with a real history sells across the whole
window. That is also what keeps the seed honest about which references have
enough observations for S6 to learn from.

**The volume is what the seed can build, not what the pilot rings.** §4's figure
is 600 tickets per sede per day and the stage document's own table is ≈82.000
sales; a seed of that size is 240.000 ledger appends, runs for many minutes, and
is re-run by every test in the suite that needs a tenant. *Demo seed* settles the
trade-off itself -- "the demo seed is sized to look right and to build in a
reasonable time" -- and `DAILY_TICKETS` below is the one number to raise when a
performance harness, rather than a screen, is what is being fed. **The shape is
not scaled down**: 180 days, the weekday and quincena rhythm, the per-sede
ranking the handoff draws, the cold sede, the long tail, real variances and
returns at every sede are all here, and every one of them is what S6, S7, S8 and
S9 are built on.

**Every random draw is seeded from a fixed constant**, so two runs produce the
same screen, and **every id is derived from a natural key**, so a rebuilt seed
keeps the ids it had.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from core.catalog import demo as catalog_demo
from core.counter import money, sales as sale_service
from core.demo import identity
from core.demo.registry import register
from core.inventory import demo as inventory_demo, ledger, states
from core.models import (
    Device,
    Item,
    ItemPrice,
    Location,
    Lot,
    Payment,
    PaymentMethod,
    Role,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    Shift,
    ShiftStatus,
    StockMoveType,
    User,
)

stable_int = inventory_demo.stable_int

# ---------------------------------------------------------------------------
# The shape
# ---------------------------------------------------------------------------

#: The window `default` and `scale` cover, and the figure acceptance 32 checks:
#: **S9's widest period compares 90 days against the previous 90**, and its
#: comparator renders only where that previous window is fully covered -- so a
#: seed of 120 days makes the Panel withhold its own comparison on the screen the
#: seed exists to demonstrate.
WINDOW_DAYS = 180

#: Usme's three weeks, and it is the only thing in the seed that demonstrates
#: the cold-start decision (§1). With every sede on 180 days every sede is
#: `learned`: S6's `Confianza del modelo` chip filters nothing, S8's card C is
#: populated everywhere, and the parametric path -- the thing that makes the
#: product demonstrable on a client with no history at all -- is never seen.
COLD_SEDE = "USM"
COLD_SEDE_DAYS = 21

#: §1 fixes `young` as twelve days of history, and it is a young **network**:
#: all six sedes carry the same twelve days and Usme is not cold inside it.
#: Twelve is the figure S9's withheld comparators and S7's estimator floor are
#: both checked against.
YOUNG_DAYS = 12

#: `minimal` is small and never zero: it is the tenant the isolation checks are
#: isolated *from*, and a cross-tenant `sales` count of zero proves nothing when
#: the other tenant has no sales either.
MINIMAL_DAYS = 6

#: The **revenue** weight per sede, in the order the handoff's `Venta por sede`
#: list draws them -- Chapinero at the
#: head, then Kennedy, Suba, Restrepo, Bosa, with Usme below all five. The
#: spread is roughly a factor of three across the five established sedes, which
#: is what gives the per-sede ranking something to rank and what stops S6's
#: forecast reading a six-way tie.
#:
#: **Every step is distinct, and that is load-bearing rather than tidy.** The
#: ordering acceptance 32 checks is over `sum(total)`, not over the ticket count,
#: so two sedes on the same base order by whichever happened to draw the more
#: expensive references -- which is a coin toss that fails a check on some runs
#: and passes on others.
DAILY_TICKETS = (9, 7, 5, 4, 3, 2)

#: What one weight point is worth in a trading day, in pesos, and **the one
#: number that sets the seed's absolute size**. It is set where it is because
#: the stage's own *Verification* reads a five-figure `rowCount` off the office's
#: Ventas list: at nine points and a ticket around the handoff's own `$28.700`,
#: the head sede rings roughly seventeen tickets a day and the network lands
#: just over ten thousand across the window.
#:
#: **Raise this, not the weights, to make the seed bigger** -- a weight is a
#: sede's share of the network's revenue and moving one moves the drawn ranking.
#: §4's own load figure is 600 tickets per sede per day and the stage document's
#: table is ≈82.000 sales; a seed of that size is a quarter of a million ledger
#: appends and several minutes per run, and *Demo seed* settles the trade-off
#: itself -- the seed is sized to look right and to build in a reasonable time,
#: and the performance harness is measured against §4's volumes separately.
REVENUE_UNIT = 62_000

#: Where the mover set is drawn from: the cheaper end of the catalog, because
#: that is what a droguería's fast movers are.
MOVER_PRICE_PERCENTILE = 30

#: `scale`'s nineteen `store` sedes, ranked with a head and a tail rather than
#: twenty sedes of equal size. **Nothing at all at the network's one
#: `warehouse`** -- a bodega rings none, and it is the location a per-sede list
#: has to render without dividing by zero.
SCALE_HEAD = 5
SCALE_TAIL = 1

MINIMAL_TICKETS = 4

#: How much of a row's headroom this fixture is allowed to sell. The rest is
#: what keeps the seeded quantities varied instead of every drawn row sitting
#: exactly on its own floor, which would read as a fixture rather than as a shop.
DRAW_SHARE = 40

#: How many references carry most of the volume. A long tail is what makes S6's
#: `Confianza del modelo` show more than one value: a few dozen movers with
#: enough observations to reach `learned`, and a majority too thin to leave
#: `parametric`. A seed where every item is equally well known hides the one
#: thing that chip exists to say.
MOVERS = 48
MOVER_SHARE = 7  # in ten lines

#: **The own-price elasticity this fixture gives each reference whose price
#: moves**, and the reason it exists at all is S7.
#:
#: A synthetic history in which demand never responds to price is a history no
#: estimator can learn anything from: the fit finds noise, the noise is large
#: because the price moved by twelve per cent and the units by a factor of two,
#: and the coefficient that comes back is a number like `+283` that S7's own
#: plausibility guard then withholds. A seed that could only ever demonstrate
#: the *withholding* half of that engine would demonstrate half the stage.
#:
#: So a line is **dropped** with the probability a demand curve says it should
#: be: never added, so no shelf is oversold and no headroom is exceeded, and
#: deterministic from the line's own key, so the same seed builds the same
#: history twice. The values are a droguería's own range -- mostly inelastic,
#: which is the deck's claim, with two elastic references so `elastic_reduce`
#: and `elastic_veto` have somewhere to land.
#: How many weeks a reference has to be able to sell in before it is worth
#: repricing for S7's benefit. It is S7's own eight-week floor plus room for the
#: weeks its exclusions take out.
WEEKS_A_FIT_NEEDS = 14

#: And how many units, so a week's count is a figure with a shape rather than a
#: coin toss: a reference selling one unit a week carries more noise in its own
#: log than any price move could put there.
UNITS_A_FIT_NEEDS = 150

DEMAND_ELASTICITY = (
    Decimal("-0.34"),
    Decimal("-0.52"),
    Decimal("-0.71"),
    Decimal("-0.28"),
    Decimal("-0.95"),
    Decimal("-1.42"),
    Decimal("-1.84"),
    Decimal("-0.63"),
)

#: Cash-dominant, because the turno's arithmetic is only meaningful when cash is
#: a share rather than the whole -- and `GET /api/shifts/{id}` has a breakdown to
#: render.
METHOD_MIX = (
    (PaymentMethod.CASH, 50),
    (PaymentMethod.DEBIT_CARD, 22),
    (PaymentMethod.CREDIT_CARD, 14),
    (PaymentMethod.TRANSFER, 9),
    (PaymentMethod.OTHER, 5),
)

#: An acquirer is optional at a counter, and a seed where every ticket carries
#: one is a seed that never renders the ordinary case.
CUSTOMER_SHARE = 18  # in a hundred tickets

#: The customer S1 reserves for this fixture, given a small, known set of closed
#: sales at Chapinero -- enough for a sale count worth printing and few enough to
#: compare row by row. **The erasure branch of the Ley 1581 deletion becomes
#: reachable the moment `sales` exists, which is here**, and the row it runs
#: against is seeded by name rather than attached on the spot: a check that
#: begins *first, hang a sale on a customer* is a check nobody runs twice.
RESERVED_SALES = 4

#: Variances, in thousands of pesos, cycled per shift. **Never uniformly zero**:
#: a seed where every drawer reconciles perfectly makes `variance` look like
#: decoration and hides the arithmetic error that would produce that same column
#: of zeros in production. Mostly zero, small in both directions, and a few
#: material shortfalls.
#: **The non-zero entries come early**, because a profile with only five closed
#: turnos would otherwise draw six zeros and read as a fixture whose `variance`
#: column is decoration.
VARIANCES = (0, 0, -3, 0, 2, 0, 0, -20, 0, 5, 0, 0, -5, 0, 12, 0, 0, -35, 0, 2)

#: A handful of sales carry a multi-hour gap between the two clocks, so the
#: offline path and rule 8's *account on `recorded_at`* are visible in the data
#: and not only in prose.
OFFLINE_EVERY = 37
OFFLINE_HOURS = 5

#: A few returns per sede across the window, partial, against the original lot,
#: refunded at the money stamped on the original line.
RETURNS_PER_SEDE = 3

#: How long after a sale a customer comes back. Inside S4's own seven-day
#: retention window, so a seeded return is one a till could still serve.
RETURN_AFTER_DAYS = 2

#: Tickets left `open` at the head sede, so `Mostrador 3` has a number to
#: report and a stranded ticket has somewhere to be seen (§B.8.2). **They carry
#: no lines**, and that is not an omission: a till pushes an open ticket's
#: header on the delta cadence and never its lines, so an open sale on the
#: server legitimately has none until it closes (§5).
OPEN_TICKETS = 3

OPENING_FLOAT = Decimal("150000")

#: How many movements go to the ledger service in one call. Large, because the
#: append's cost is a handful of statements whatever the batch holds -- and the
#: seed's whole cost is the number of calls.
APPEND_BATCH = 2000


def _profile(profile) -> dict | None:
    """The window and the per-sede volume, or `None` where the profile builds
    nothing at all."""
    if profile == "cold":
        # **No rows of any kind**, and the fixture returns empty explicitly
        # rather than omitting the case: a single seeded sale here makes S7's
        # sales-free path, S8's empty rule set and S6's parametric order all
        # unreachable, and each of the three is then reached by deleting rows by
        # hand -- the check §1 forbids.
        return None
    if profile == "young":
        return {"window": YOUNG_DAYS, "cold_sede": None, "quincena": True}
    if profile == "minimal":
        return {"window": MINIMAL_DAYS, "cold_sede": None, "quincena": False}
    if profile == "scale":
        return {"window": WINDOW_DAYS, "cold_sede": None, "quincena": False}
    return {
        "window": WINDOW_DAYS,
        "cold_sede": COLD_SEDE,
        "cold_window": COLD_SEDE_DAYS,
        "quincena": False,
    }


def _weight(profile, code, ordinal, total):
    """One sede's share of the network's revenue, before the day's own shape."""
    if profile == "minimal":
        return MINIMAL_TICKETS
    if profile == "scale":
        # The bodega rings none. Every other sede is ranked head to tail.
        if code == "BOD":
            return 0
        span = max(1, total - 2)
        step = (SCALE_HEAD - SCALE_TAIL) / span
        return max(SCALE_TAIL, round(SCALE_HEAD - step * ordinal))
    return DAILY_TICKETS[min(ordinal, len(DAILY_TICKETS) - 1)]


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------


def _quincena(day: date) -> bool:
    """The 15th and the last day of the month -- payday in Colombia, and the two
    bumps a flat series would hide."""
    return day.day == 15 or (day + timedelta(days=1)).day == 1


def _day_weight(day: date) -> float:
    """A weekday shape with a Saturday peak and a thin Sunday.

    **A flat series makes every model look broken**: uniform noise gives a
    forecast nothing to fit and gives a demo audience a histogram that looks
    like a rendering bug.
    """
    weekday = day.weekday()
    weight = 1.35 if weekday == 5 else 0.45 if weekday == 6 else 1.0
    return weight * (1.25 if _quincena(day) else 1.0)


def _window(profile, shape, today) -> list[date]:
    """The days the network traded, newest last."""
    span = shape["window"]
    if not shape.get("quincena"):
        return [today - timedelta(days=offset) for offset in range(span - 1, -1, -1)]
    # `young` places its window so **one quincena bump falls inside it**, which
    # is what gives twelve days of history a shape rather than a flat line. Any
    # twelve consecutive days can miss both the 15th and the month's end, so the
    # window walks back until it does not.
    for offset in range(0, 20):
        end = today - timedelta(days=offset)
        days = [end - timedelta(days=back) for back in range(span - 1, -1, -1)]
        if any(_quincena(day) for day in days):
            return days
    return [today - timedelta(days=offset) for offset in range(span - 1, -1, -1)]


#: Where the day's tickets fall, as a cumulative share of the trading day. A
#: droguería has a morning rush and a heavier one after work, and a flat spread
#: gives an hourly chart a rectangle and the turno's arithmetic nothing to look
#: like.
PEAKS = ((10.5, 0.42), (18.0, 0.58))


def _at(day: date, ordinal: int, per_day: int) -> datetime:
    """A time within the trading day, with a morning and a late-afternoon peak.

    The ordinal is placed inside whichever peak its share falls in and spread
    across that peak's own window, so two tickets at the same ordinal on
    different days do not share a clock time -- the day's own length decides
    where they land.
    """
    share = (ordinal + 0.5) / max(1, per_day)
    centre, weight = PEAKS[0] if share < PEAKS[0][1] else PEAKS[1]
    within = share / weight if share < PEAKS[0][1] else (share - weight) / (1 - weight)
    minutes = int((centre - 1.75 + 3.5 * min(1.0, max(0.0, within))) * 60)
    hour, minute = divmod(min(max(minutes, 8 * 60), 20 * 60 - 1), 60)
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


# ---------------------------------------------------------------------------
# The shelves this fixture may sell from
# ---------------------------------------------------------------------------


def _floor(row) -> int:
    """The quantity below which this row would change the state S3 planned for
    it, and therefore the quantity this fixture will not take it below."""
    policy = row["policy"]
    if row["state"] == states.OVERSTOCK:
        return int(policy["max_quantity"]) + 1 if policy else 1
    if policy:
        return int(policy["reorder_point"]) + 1
    # No policy means `reorder_point` and `max_quantity` are unknown, so states
    # 5 and 6 are unreachable for the row and only `stockout` is in play.
    return 1


#: `spread_keys` is a pure function of the profile and its inputs are two heavy
#: planners -- S3's whole stock plan and this fixture's own shelf rules. S1's
#: fixture asks for it once per seed and this one would recompute it, so the
#: answer is kept: a planner nobody caches is a planner every caller pays for.
_SPREAD: dict[str, list] = {}


def spread_keys(profile) -> list:
    """The references this fixture sells **across the whole window**, best first.

    **Published because S1's fixture reads it**, and the reason is S7: an
    elasticity is fitted on *weeks*, so it can only exist where a price moved on
    a reference that sold in many different weeks. Two fixtures choosing
    independently produced thirty repriced references of which six sold at all,
    and one estimable reference in four thousand -- a price history that
    demonstrated a column and taught the next stage nothing.

    The ranking is by how far back the reference has been on a shelf, then by
    how many units it may sell: a row stocked a fortnight ago sells only in the
    last fortnight, and S3 is the module that knows when that was.

    Computed from S3's stock plan and this fixture's own rules, before either
    has written a row, so asking disturbs no ordering between the two fixtures.
    """
    if profile in _SPREAD:
        return _SPREAD[profile]
    shape = _profile(profile)
    if shape is None:
        return []
    shelves = _shelves(profile)
    catalog = {
        f"{row['name']}|{row['presentation']}": row
        for row in catalog_demo.item_plan(profile)
    }
    reach: dict[str, tuple[int, int]] = {}
    for shelf in shelves:
        back, units = reach.get(shelf["item_key"], (0, 0))
        reach[shelf["item_key"]] = (
            max(back, int(shelf["earliest_back"])),
            units + int(shelf["units"]),
        )

    # **Ranked by weeks first and pesos second**, because S7 needs both of them
    # on the same reference and they pull in different directions. A regression
    # counts weeks, so a reference has to be on a shelf long enough to sell in
    # many of them; a suggestion has to clear a materiality floor stated in
    # pesos, so it also has to be worth something. The span is bounded both by
    # how long the row has been on a shelf and by how much of it there is -- a
    # row sells at most a unit or two a day.
    def rank(pair):
        key, (back, units) = pair
        price = float((catalog.get(key) or {}).get("price") or 0)
        # A reference sells at most a unit or two a day, so its plausible span
        # is bounded both by how long it has been on a shelf and by how much of
        # it there is. Everything that can reach the estimator's floors at all
        # comes first; inside that, the ones worth the most money.
        reaches_the_floors = (
            min(back // 7, units) >= WEEKS_A_FIT_NEEDS and units >= UNITS_A_FIT_NEEDS
        )
        # Inside that bucket, by **volume** rather than by revenue: the weekly
        # counts a regression reads are unit counts, and an expensive reference
        # selling two a week carries more noise in its own log than any price
        # move could put there. Price still matters to the impact figure, and
        # `units × price` was the first ranking tried -- it filled the window
        # with expensive, thin references and every fit came back too wide to
        # use.
        return (not reaches_the_floors, -units, -price, key)

    _SPREAD[profile] = [key for key, _reach in sorted(reach.items(), key=rank)]
    return _SPREAD[profile]


def _shelves(profile):
    """`(sede, item_key)` -> the units this fixture may sell, and from when.

    One entry per plan row. **Rows in any of the four action states are absent
    outright**, together with the one row whose deep history exercises the
    record panel's fifty-move cap -- a sale on that lot would put S4's moves in
    the middle of a sixty-move trace S3 wrote to be read -- and every row one of
    S3's own documents moves.
    """
    documented = inventory_demo.documented_keys(profile)
    shelves = []
    for row in inventory_demo.stock_plan(profile):
        if row.get("deep_history"):
            continue
        if row["state"] not in (states.SUFFICIENT, states.OVERSTOCK):
            continue
        # **A row one of S3's own documents moves is not sellable here.** Its
        # opening moves are written short by exactly the document's delta, so
        # for most of the window the shelf holds less than its planned quantity
        # -- and a sale against it inside that window is a trace that dips below
        # zero on a shelf that was never short.
        if (row["sede"], row["item_key"], row["lot_code"]) in documented:
            continue
        headroom = int(row["quantity"]) - _floor(row)
        units = max(0, headroom * DRAW_SHARE // 100)
        if units <= 0:
            continue
        shelves.append(
            {
                "sede": row["sede"],
                "item_key": row["item_key"],
                "item_name": row["item_name"],
                "lot_code": row["lot_code"] or None,
                "units": units,
                # **A sale is never dated before the receipt that made it
                # possible**, and S3 is the only module that knows when that
                # was. A row whose whole history is one opening move was stocked
                # a fortnight ago and sells only in the last fortnight; a row
                # with a real history sells across the window.
                "earliest_back": inventory_demo.stocked_days_back(row),
            }
        )
    return shelves


def _movers(shelves, catalog):
    """The few dozen references that carry most of the volume.

    **Drawn from the cheaper half of the catalog**, because that is what a
    droguería's movers are: acetaminofén and sales de rehidratación, not the
    dermatological line. A mover set chosen by hash alone puts a handful of very
    expensive references at the head of seventy per cent of the lines, and the
    demo then reads an average ticket several times what a pharmacy rings --
    which is the kind of wrong that passes every query and fails the screen.
    """
    keys = sorted({shelf["item_key"] for shelf in shelves})
    prices = sorted(float((catalog.get(key) or {}).get("price") or 0) for key in keys)
    if not prices:
        return set()
    ceiling = prices[int(len(prices) * MOVER_PRICE_PERCENTILE / 100)]
    cheap = [
        key
        for key in keys
        if float((catalog.get(key) or {}).get("price") or 0) <= ceiling
    ]
    ranked = sorted(cheap or keys, key=lambda key: stable_int("mover", key))
    return set(ranked[:MOVERS])


# ---------------------------------------------------------------------------
# The plan: every row this fixture would write, computed before it writes one,
# so `owned_ids` and `build` cannot disagree about what it owns.
# ---------------------------------------------------------------------------


def _tills(context):
    """`sede code -> [(label, device code)]`, ordered.

    Read from the database rather than re-derived: `devices` is S2's table and
    S2 alone writes it, and `sales.number` is composed from `devices.code` --
    which is what makes a seeded ticket read like the handoff's `Venta C3-4821`
    rather than like a row from a generator.
    """
    by_sede: dict[str, list] = {}
    rows = (
        Device.objects.filter(tenant_id=context.tenant_id)
        .select_related("location")
        .order_by("location__code", "label")
    )
    for device in rows:
        by_sede.setdefault(device.location.code, []).append(
            (device.label, device.code, device.id)
        )
    return by_sede


def _customers(profile):
    """The clientes a ticket may name.

    **Two of S1's three reserved rows are excluded by name**: the one no fixture
    in any stage may reference, and the one seeded already erased. Drawing
    either into the ordinary pool would break both halves of the Ley 1581 check
    at once -- the never-referenced row would gain a reference, and the erased
    row would gain a name.
    """
    excluded = {
        f"{catalog_demo.RESERVED_NEVER_REFERENCED[0]}:"
        f"{catalog_demo.RESERVED_NEVER_REFERENCED[1]}",
        catalog_demo.RESERVED_ERASED,
    }
    return [
        row["key"]
        for row in catalog_demo.customer_plan(profile)
        if row["key"] not in excluded
    ]


def _reserved_key(profile):
    if not catalog_demo.PROFILES[profile]["reserved_customers"]:
        return None
    return f"{catalog_demo.RESERVED_FOR_SALES[0]}:{catalog_demo.RESERVED_FOR_SALES[1]}"


def plan(context) -> list[dict]:
    """Every ticket, in the order it was rung.

    Deterministic and database-light: the tills come from S2's rows and
    everything else is derived from S3's plan and the profile, so calling it
    twice in one run -- once for `owned_ids`, once for `build` -- produces the
    same list.
    """
    profile = context.profile
    shape = _profile(profile)
    if shape is None:
        return []

    sedes = identity.sedes(profile)
    tills = _tills(context)
    if not tills:
        # `owned_ids` runs before any fixture writes, so on a first run there
        # are no devices yet and the plan is empty -- which is correct: on a
        # fresh tenant this fixture owns no rows to guard. On a re-run the tills
        # are there and the plan is exact, which is when the guard matters.
        return []

    shelves = _shelves(profile)
    catalog = {
        f"{row['name']}|{row['presentation']}": row
        for row in catalog_demo.item_plan(profile)
    }
    movers = _movers(shelves, catalog)
    customers = _customers(profile)
    reserved = _reserved_key(profile)

    today = timezone.localdate()
    days = _window(profile, shape, today)
    tickets: list[dict] = []
    sequences: dict[str, int] = {}

    pools = {code: _pool(shelves, code, movers, catalog) for code, *_rest in sedes}

    for ordinal, (code, *_rest) in enumerate(sedes):
        weight = _weight(profile, code, ordinal, len(sedes))
        if weight <= 0 or code not in tills:
            continue
        sede_days = days
        if shape.get("cold_sede") == code:
            sede_days = days[-min(shape["cold_window"], len(days)) :]
        tickets.extend(
            _sede_tickets(
                profile=profile,
                code=code,
                weight=weight,
                mean=_mean_ticket(pools[code], catalog),
                days=sede_days,
                today=today,
                tills=tills[code],
                pool=pools[code],
                catalog=catalog,
                customers=customers,
                sequences=sequences,
            )
        )

    _attach_reserved(tickets, reserved, sedes)
    _open_tickets(tickets, sedes, tills, sequences, today)
    _returns(tickets, profile, today)
    return tickets


def _pool(shelves, code, movers, catalog):
    """This sede's shelves, split into the movers and the tail, **each ordered
    cheapest first**.

    The ordering is what `_take`'s low-end bias reads: most of what crosses a
    droguería's counter is cheap, and a uniform draw over a catalog that runs
    from a `$650` tablet to a `$180.000` dermatological produces an average
    ticket several times what a pharmacy rings.
    """

    def price(shelf):
        return float((catalog.get(shelf["item_key"]) or {}).get("price") or 0)

    here = sorted(
        (shelf for shelf in shelves if shelf["sede"] == code),
        key=lambda shelf: (price(shelf), shelf["item_key"]),
    )
    return {
        "mover": [dict(shelf) for shelf in here if shelf["item_key"] in movers],
        "tail": [dict(shelf) for shelf in here if shelf["item_key"] not in movers],
    }


def _take(pool, seed, days_back):
    """One shelf with units left, whose stock was there on the day.

    The mover pool first, seven lines in ten, which is what makes a few dozen
    references reach `learned` while the majority stays `parametric`.
    """
    order = ("mover", "tail") if seed % 10 < MOVER_SHARE else ("tail", "mover")
    for name in order:
        rows = pool[name]
        if not rows:
            continue
        # **Biased to the cheap end**, cubically, over a pool sorted by price.
        # A pharmacy sells far more acetaminofén than dermatological cream, and
        # a uniform draw makes the demo's average ticket read like a clinic's
        # rather than a droguería's -- the handoff's own note is
        # `Ticket promedio del punto: $28.700`.
        span = len(rows)
        start = ((seed % span) ** 3) // (span * span)
        for step in range(span):
            shelf = rows[(start + step) % len(rows)]
            if shelf["units"] > 0 and shelf["earliest_back"] > days_back:
                return shelf
    return None


def _offset(today, day) -> str:
    """A day's key, as days back from the seed date rather than as a calendar
    date.

    **Every seeded id is derived from a natural key, and a key carrying today's
    date is not one.** With the date in it, every id in this fixture moves when
    the clock rolls past midnight -- and the next run's guard then finds a
    tenant full of rows it would not have written and refuses the whole thing.
    The offset keeps the ids a rebuilt seed had, which is the promise the whole
    id scheme makes.
    """
    return f"d{(today - day).days:04d}"


def _sede_tickets(
    *,
    profile,
    code,
    weight,
    mean,
    days,
    today,
    tills,
    pool,
    catalog,
    customers,
    sequences,
):
    """One sede's tickets, day by day, **against a revenue budget**.

    `Venta por sede` ranks by money and not by ticket count, and every sede
    holds a different slice of the catalog -- so a sede whose shelves are dearer
    rings proportionally fewer tickets to land where the handoff draws it. The
    budget is what makes the ordering a property of the fixture rather than a
    coincidence of which references S3's plan put where.
    """
    tickets = []
    expected = max(1, round(weight * REVENUE_UNIT / mean)) if mean else weight
    for day in days:
        days_back = (today - day).days
        shape = _day_weight(day)
        budget = weight * REVENUE_UNIT * shape
        spent = 0.0
        ordinal = 0
        ceiling = max(2, round(expected * shape * 3))
        while spent < budget and ordinal < ceiling:
            label, device_code, device_id = tills[ordinal % len(tills)]
            seed = stable_int("ticket", profile, code, _offset(today, day), ordinal)
            lines = _lines(pool, seed, days_back, catalog)
            if not lines:
                break
            spent += _value(lines, catalog)
            count = max(expected, ordinal + 1)
            ordinal += 1
            sequences[device_code] = sequences.get(device_code, 0) + 1
            key = f"{code}:{label}:{_offset(today, day)}:{ordinal - 1}"
            at = _at(day, ordinal - 1, count)
            offline = seed % OFFLINE_EVERY == 0
            tickets.append(
                {
                    "key": key,
                    "sede": code,
                    "label": label,
                    "device_code": device_code,
                    "device_id": device_id,
                    "day": day,
                    "occurred_at": at,
                    # **`occurred_at` precedes `recorded_at` by a plausible
                    # interval**, and a handful carry a multi-hour gap, so the
                    # offline path is visible in the data and not only in prose.
                    "recorded_at": at
                    + timedelta(hours=OFFLINE_HOURS if offline else 0, seconds=4),
                    "number": sale_service.compose_number(
                        device_code, sequences[device_code]
                    ),
                    "shift_key": f"{code}:{label}:{_offset(today, day)}",
                    "lines": lines,
                    "customer_key": (
                        customers[seed % len(customers)]
                        if customers and seed % 100 < CUSTOMER_SHARE
                        else None
                    ),
                    "status": SaleStatus.CLOSED,
                    "return": None,
                }
            )
    return tickets


def _value(lines, catalog) -> float:
    """What a ticket is worth, from the plan's own prices. An estimate, because
    the price actually stamped is the one in force on the sale's own day."""
    total = 0.0
    for line in lines:
        entry = catalog.get(line["item_key"]) or {}
        total += float(entry.get("price") or 0) * line["quantity"]
    return total


#: How many lines a ticket carries, as a distribution rather than a range.
#: **Most tickets are one item**: somebody comes in for acetaminofén and leaves.
#: The handoff draws a three-line ticket because that is the interesting case to
#: draw, not the common one -- and its own note reads
#: `Ticket promedio del punto: $28.700`, which a generator putting four lines of
#: three units on every ticket misses by a factor of five.
LINES_PER_TICKET = (1, 1, 1, 2, 2, 3)


def _line_count(seed) -> int:
    return LINES_PER_TICKET[seed % len(LINES_PER_TICKET)]


def _line_quantity(entry, seed) -> int:
    """Units on one line, in **base units** always.

    A `splittable` reference sells as a whole pack about one time in six --
    `units_per_pack` base units and not a second unit of measure (§3), because
    nothing about the line, the move or the document handed to an invoicing
    system distinguishes a box of twenty from twenty singles.
    """
    pack = int(entry.get("units_per_pack") or 1)
    if entry.get("splittable") and pack > 1 and seed % 8 == 0:
        return pack
    return 2 if (seed // 7) % 4 == 0 else 1


def _lines(pool, seed, days_back, catalog):
    """One ticket's lines, drawn from shelves that can supply them."""
    lines = []
    for position in range(_line_count(seed)):
        line_seed = stable_int("line", seed, position)
        shelf = _take(pool, line_seed, days_back)
        if shelf is None:
            break
        entry = catalog.get(shelf["item_key"]) or {}
        quantity = min(_line_quantity(entry, line_seed), shelf["units"])
        if quantity <= 0:
            break
        shelf["units"] -= quantity
        lines.append(
            {
                "position": position,
                "item_key": shelf["item_key"],
                "lot_code": shelf["lot_code"],
                "quantity": quantity,
            }
        )
    return lines


def _mean_ticket(pool, catalog) -> float:
    """What a ticket drawn from **this sede's own shelves** is worth, estimated
    from the plan alone.

    It exists because acceptance 32 ranks the sedes by `sum(total)` and not by
    ticket count, and every sede holds a different slice of the catalog: a sede
    whose pool happens to carry expensive references out-earns one that rings a
    quarter more tickets. Estimating the mean here and dividing the daily target
    by it is what makes the ranking a property of the fixture rather than a
    coincidence of which items S3's plan put where.
    """
    total = 0.0
    weighed = 0.0
    for name, share in (("mover", MOVER_SHARE), ("tail", 10 - MOVER_SHARE)):
        rows = pool[name]
        if not rows:
            continue
        value = 0.0
        for index, shelf in enumerate(rows):
            entry = catalog.get(shelf["item_key"]) or {}
            price = float(entry.get("price") or 0)
            value += price * _line_quantity(entry, stable_int("mean", index))
        total += share * (value / len(rows))
        weighed += share
    if weighed == 0:
        return 0.0
    return (sum(LINES_PER_TICKET) / len(LINES_PER_TICKET)) * total / weighed


def _attach_reserved(tickets, reserved, sedes):
    """Hang a known set of sales on the customer S1 reserved for this fixture.

    By name and in a fixed position rather than wherever the ordinary draw
    happened to land, so the Ley 1581 erasure check reads a sale count it can
    print and compare row by row.
    """
    if not reserved or not sedes:
        return
    head = sedes[0][0]
    candidates = [one for one in tickets if one["sede"] == head]
    for index in range(min(RESERVED_SALES, len(candidates))):
        candidates[index * max(1, len(candidates) // RESERVED_SALES)][
            "customer_key"
        ] = reserved


def _open_tickets(tickets, sedes, tills, sequences, today):
    """Tickets in progress at the head sede.

    **They carry no lines**, and that is the truth of the protocol rather than a
    shortcut: a till writes the `sales` row locally the moment its first line
    lands and pushes that header on the ordinary delta cadence, but it never
    pushes a line until the batch that closes the ticket (§5). An open sale on
    the server therefore has no lines, moves no stock, and is exactly what
    `Mostrador 3` counts.
    """
    if not sedes:
        return
    head = sedes[0][0]
    if head not in tills:
        return
    label, device_code, device_id = tills[head][0]
    at = timezone.make_aware(datetime.combine(today, time(19, 40)))
    for ordinal in range(OPEN_TICKETS):
        sequences[device_code] = sequences.get(device_code, 0) + 1
        tickets.append(
            {
                "key": f"{head}:{label}:{_offset(today, today)}:open:{ordinal}",
                "sede": head,
                "label": label,
                "device_code": device_code,
                "device_id": device_id,
                "day": today,
                "occurred_at": at + timedelta(minutes=ordinal),
                "recorded_at": at + timedelta(minutes=ordinal, seconds=3),
                "number": sale_service.compose_number(
                    device_code, sequences[device_code]
                ),
                "shift_key": f"{head}:{label}:{_offset(today, today)}",
                "lines": [],
                "customer_key": None,
                "status": SaleStatus.OPEN,
                "return": None,
            }
        )


def _returns(tickets, profile, today):
    """A few per sede across the window, partial, against the original lot.

    Seeded rather than left to a test, so the devoluciones list, the return path
    and S9's return figures all have rows -- and so the return-against-original-
    lot rule is visible in data a person can open.
    """
    by_sede: dict[str, list] = {}
    for ticket in tickets:
        if ticket["status"] != SaleStatus.CLOSED or not ticket["lines"]:
            continue
        by_sede.setdefault(ticket["sede"], []).append(ticket)
    for code, rows in by_sede.items():
        # Drawn from the middle of the window rather than its ends: a return
        # against the newest sale hides the seven-day retention window, and one
        # against the oldest is outside every till's local slice.
        usable = rows[len(rows) // 4 : -max(1, len(rows) // 20)]
        if not usable:
            continue
        step = max(1, len(usable) // RETURNS_PER_SEDE)
        for index in range(min(RETURNS_PER_SEDE, len(usable))):
            ticket = usable[index * step]
            line = ticket["lines"][0]
            quantity = max(1, line["quantity"] // 2)
            seed = stable_int("return", profile, code, index)
            # **Never after the seed date.** A return dated into the future
            # would create a turno later than every other one at its sede, and
            # `_write_shifts` would then leave *that* one open while the drawer
            # a cashier is actually standing at was closed and counted.
            day = min(ticket["day"] + timedelta(days=RETURN_AFTER_DAYS), today)
            ticket["return"] = {
                "sale_line_position": line["position"],
                "quantity": quantity,
                "day": day,
                "shift_key": (
                    f"{ticket['sede']}:{ticket['label']}:{_offset(today, day)}"
                ),
                "reason": RETURN_REASONS[seed % len(RETURN_REASONS)],
            }


RETURN_REASONS = (
    "El cliente compró la presentación equivocada.",
    "Caja sin abrir, el médico cambió la fórmula.",
    "Se facturó una unidad de más en el mostrador.",
)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def owned_ids(context):
    """Exactly the rows this fixture writes in its guard tables.

    `stock_on_hand` is **not** among them, for the reason S3 states on its own
    fixture: this one writes no projection row, so it owns none. What it does
    own in `stock_moves` is one row per sold line and one per returned line,
    each appended through the ledger service under a key derived from the line
    it is.
    """
    tickets = plan(context)
    shifts, sales, lines, payments, returns, return_lines, moves = (
        set(),
        set(),
        set(),
        set(),
        set(),
        set(),
        set(),
    )
    for ticket in tickets:
        shifts.add(context.uid("shifts", ticket["shift_key"]))
        sales.add(context.uid("sales", ticket["key"]))
        for line in ticket["lines"]:
            line_key = f"{ticket['key']}:{line['position']}"
            lines.add(context.uid("sale_lines", line_key))
            moves.add(context.uid("stock_moves", f"sale:{line_key}"))
        for index in range(_payment_count(ticket)):
            payments.add(context.uid("payments", f"{ticket['key']}:{index}"))
        if ticket["return"]:
            returns.add(context.uid("sale_returns", f"{ticket['key']}:return"))
            return_key = f"{ticket['key']}:return:0"
            return_lines.add(context.uid("sale_return_lines", return_key))
            moves.add(context.uid("stock_moves", f"return:{return_key}"))
            shifts.add(context.uid("shifts", ticket["return"]["shift_key"]))
    return {
        "shifts": shifts,
        "sales": sales,
        "sale_lines": lines,
        "payments": payments,
        "sale_returns": returns,
        "sale_return_lines": return_lines,
        "stock_moves": moves,
    }


def _payment_count(ticket) -> int:
    """One method on most tickets and two on a share of them, so the split
    payment the `Cobro` dialog exists for is in the data."""
    if ticket["status"] != SaleStatus.CLOSED or not ticket["lines"]:
        return 0
    return 2 if stable_int("split", ticket["key"]) % 6 == 0 else 1


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def build(context):
    """Write the counter's history, inside the pin the command already opened."""
    tickets = plan(context)
    if not tickets:
        # `cold` builds nothing and says so, rather than omitting the case.
        return

    _STAMP["ordinal"] = 0
    world = _world(context)
    shifts = _write_shifts(context, tickets, world)
    _write_sales(context, tickets, world, shifts)
    _write_returns(context, tickets, world, shifts)
    _count_drawers(context, shifts)


class _World:
    """Everything the build resolves per line, loaded once.

    A price and a cost per line over ten thousand lines is ten thousand queries;
    the catalog is a few thousand rows and fits in memory, which is what makes
    the fixture's cost the ledger appends rather than the lookups.
    """

    __slots__ = ("locations", "items", "lots", "prices", "users", "cashiers", "devices")

    def __init__(self):
        self.locations = {}
        self.items = {}
        self.lots = {}
        self.prices = {}
        self.users = {}
        self.cashiers = {}
        self.devices = {}


def _world(context) -> _World:
    world = _World()
    world.locations = {
        location.code: location
        for location in Location.objects.filter(tenant_id=context.tenant_id)
    }
    world.items = {
        f"{item.name}|{item.presentation}": item
        for item in Item.objects.filter(tenant_id=context.tenant_id)
    }
    by_item: dict = {}
    for lot in Lot.objects.filter(tenant_id=context.tenant_id):
        by_item.setdefault(lot.item_id, {})[lot.lot_code] = lot
    world.lots = by_item
    for row in ItemPrice.objects.filter(tenant_id=context.tenant_id).order_by(
        "effective_from"
    ):
        world.prices.setdefault(row.item_id, []).append(row)
    world.users = {
        user.id: user for user in User.objects.filter(tenant_id=context.tenant_id)
    }
    for user in world.users.values():
        if user.role == Role.CASHIER and user.location_id:
            world.cashiers.setdefault(user.location_id, user)
    owner = next((one for one in world.users.values() if one.role == Role.OWNER), None)
    world.cashiers["owner"] = owner
    world.devices = {
        device.id: device
        for device in Device.objects.filter(tenant_id=context.tenant_id)
    }
    return world


def _seller(world, location):
    """The sede's own cashier, and the network's single `owner` where there is
    none -- which is `minimal`, whose profile has no cashier at all."""
    return world.cashiers.get(location.id) or world.cashiers.get("owner")


def _demand_answers(world, item, price, key) -> bool:
    """Whether this line survives the price it was going to be sold at.

    `q ∝ p^β` with `β < 0`, expressed as a probability of keeping the line and
    normalised against the **lowest** price the reference carried in the window
    -- so the factor is 1 at the bottom of its own price ladder and below 1
    above it. Lines are only ever dropped, never added, which is what keeps
    every shelf inside the headroom S3's plan left it.

    A reference whose price never moved has no ladder and is left alone: there
    is nothing for a demand curve to respond to, and thinning it would only make
    the fixture sell less.
    """
    rows = [row for row in world.prices.get(item.id, []) if row.location_id is None]
    if len(rows) < 2:
        return True
    floor = min(row.price for row in rows)
    if floor <= 0 or price <= floor:
        return True
    beta = DEMAND_ELASTICITY[
        stable_int("elasticity", f"{item.name}|{item.presentation}")
        % len(DEMAND_ELASTICITY)
    ]
    keep = float(floor / price) ** float(abs(beta))
    return stable_int("demand", key) % 10_000 < keep * 10_000


def _price_on(world, item, location, day):
    """The price in force at this sede on the sale's own day.

    **The sale's own day, not today's row**, so margin has a trend instead of a
    flat line and S7 has real co-movement to read where a price actually moved.
    """
    best = None
    for row in world.prices.get(item.id, ()):
        if row.effective_from > day:
            continue
        if row.effective_to is not None and row.effective_to <= day:
            continue
        if row.location_id not in (None, location.id):
            continue
        if best is None or _price_wins(row, best):
            best = row
    return best.price if best is not None else None


def _price_wins(row, held) -> bool:
    scoped = row.location_id is not None
    held_scoped = held.location_id is not None
    if scoped != held_scoped:
        return scoped
    return row.effective_from > held.effective_from


def _write_shifts(context, tickets, world) -> dict:
    """One turno per till per trading day, and the last one at each sede's first
    till left **open** -- so a till opened on any sede finds its turno and
    sells, which is this stage's own completion test."""
    wanted: dict[str, dict] = {}
    for ticket in tickets:
        for key, day in _shift_keys(ticket):
            wanted.setdefault(
                key,
                {
                    "sede": ticket["sede"],
                    "label": ticket["label"],
                    "device_id": ticket["device_id"],
                    "day": day,
                },
            )
    # The one turno each sede leaves open: its **first** till's **latest** day.
    # One per sede and never two, because `one_open_shift_per_device` is per
    # device and a second open drawer at the same sede would be a till nobody
    # could sell from.
    tills_by_sede: dict[str, set] = {}
    for entry in wanted.values():
        tills_by_sede.setdefault(entry["sede"], set()).add(entry["label"])
    first_label = {sede: sorted(labels)[0] for sede, labels in tills_by_sede.items()}
    stays_open_key: dict[str, str] = {}
    for key, entry in sorted(wanted.items()):
        if entry["label"] != first_label[entry["sede"]]:
            continue
        held = stays_open_key.get(entry["sede"])
        if held is None or entry["day"] > wanted[held]["day"]:
            stays_open_key[entry["sede"]] = key

    rows = []
    shifts: dict[str, dict] = {}
    for key, entry in wanted.items():
        location = world.locations.get(entry["sede"])
        if location is None:
            continue
        stays_open = stays_open_key.get(entry["sede"]) == key
        actor = _seller(world, location)
        opened = timezone.make_aware(datetime.combine(entry["day"], time(8, 30)))
        # **The status is decided here and not after the sales are written.**
        # `one_open_shift_per_device` is a partial unique index, so a till's
        # hundred and eighty turnos cannot all be inserted `open` and closed
        # afterwards -- the second row of the batch collides with the first. The
        # count itself is filled in once the cash is known.
        row = Shift(
            id=context.uid("shifts", key),
            tenant_id=context.tenant_id,
            location=location,
            user=actor,
            user_name=getattr(actor, "name", "") or "",
            opened_at=opened,
            opening_float=OPENING_FLOAT,
            status=ShiftStatus.OPEN if stays_open else ShiftStatus.CLOSED,
            closed_at=None if stays_open else opened + timedelta(hours=12),
            client_uuid=context.uid("shift-key", key),
            device_id=entry["device_id"],
            occurred_at=opened,
            recorded_at=opened + timedelta(seconds=3),
        )
        rows.append(row)
        shifts[key] = {"row": row, "open": stays_open, "cash": money.ZERO}
    fresh = _insert(context, Shift, rows, "shifts")
    for key, entry in shifts.items():
        entry["fresh"] = entry["row"].id in fresh
        del key
    return shifts


def _shift_keys(ticket):
    yield ticket["shift_key"], ticket["day"]
    if ticket["return"]:
        yield ticket["return"]["shift_key"], ticket["return"]["day"]


def _write_sales(context, tickets, world, shifts):
    """The tickets, their lines and their payments -- and the `sale` moves they
    caused, appended through S3's ledger service in one batch per day."""
    sale_rows, line_rows, payment_rows = [], [], []
    pending: dict = {}
    held = 0
    heads = _fefo_heads(tickets, world)
    # Chronological, and batched by size rather than by day: the append's cost
    # is a handful of statements per call whatever the batch holds, so a hundred
    # and eighty calls is a hundred and eighty times the round trips of a dozen.
    for ticket in sorted(tickets, key=lambda one: (one["day"], one["key"])):
        built = _build_sale(context, ticket, world, shifts, heads)
        if built is None:
            continue
        sale, lines, payments, batch = built
        sale_rows.append(sale)
        line_rows.extend(lines)
        payment_rows.extend(payments)
        pending.setdefault(ticket["device_id"], []).extend(batch)
        held += len(batch)
        if held >= APPEND_BATCH:
            _append(context, world, pending)
            held = 0
    _append(context, world, pending)

    _insert(context, Sale, sale_rows, "sales")
    _insert(context, SaleLine, line_rows, "sale_lines")
    _insert(context, Payment, payment_rows, "payments")


def _fefo_heads(tickets, world):
    """`(sede, item_key)` -> the lot code FEFO would have offered.

    **Stated rather than derived by the ledger, and it is a correctness point as
    much as a speed one.** Left unstated, `append` reads the FEFO head per
    `(location, item)` per call -- twelve thousand extra queries over a hundred
    and eighty days -- and it reads it against the projection *as it stands now*
    rather than as it stood on the day of the sale, which is the recomputation
    §6 says is the wrong answer. The seed knows which lot it drew and which one
    was first out, so it says so, exactly as a till does.
    """
    heads: dict = {}
    for ticket in tickets:
        for line in ticket["lines"]:
            key = (ticket["sede"], line["item_key"])
            if key in heads or not line["lot_code"]:
                continue
            heads[key] = None
    for ticket in tickets:
        for line in ticket["lines"]:
            key = (ticket["sede"], line["item_key"])
            if key not in heads or not line["lot_code"]:
                continue
            item = world.items.get(line["item_key"])
            lot = world.lots.get(getattr(item, "id", None), {}).get(line["lot_code"])
            if lot is None:
                continue
            held = heads[key]
            if held is None or _expires_first(lot, held):
                heads[key] = lot
    return {key: getattr(lot, "lot_code", None) for key, lot in heads.items()}


def _expires_first(lot, held) -> bool:
    """First expired, first out, with an undated lot sorting last -- the same
    ordering `core.inventory.ledger.available_lots` applies."""
    if lot.expires_at is None:
        return False
    if held.expires_at is None:
        return True
    return lot.expires_at < held.expires_at


def _build_sale(context, ticket, world, shifts, heads):
    location = world.locations.get(ticket["sede"])
    entry = shifts.get(ticket["shift_key"])
    if location is None or entry is None:
        return None
    seller = _seller(world, location)
    sale = Sale(
        id=context.uid("sales", ticket["key"]),
        tenant_id=context.tenant_id,
        location=location,
        shift=entry["row"],
        number=ticket["number"],
        status=ticket["status"],
        customer_id=(
            context.uid("customers", ticket["customer_key"])
            if ticket["customer_key"]
            else None
        ),
        sold_by_user=seller,
        sold_by_name=getattr(seller, "name", "") or "",
        client_uuid=context.uid("sale-key", ticket["key"]),
        device_id=ticket["device_id"],
        occurred_at=ticket["occurred_at"],
        recorded_at=ticket["recorded_at"],
        closed_at=(
            ticket["recorded_at"] if ticket["status"] == SaleStatus.CLOSED else None
        ),
    )

    lines, moves = [], []
    for line in ticket["lines"]:
        built = _build_line(context, ticket, line, sale, world, heads)
        if built is None:
            continue
        row, move = built
        lines.append(row)
        if move is not None:
            moves.append(move)

    figures = money.totals(lines)
    sale.subtotal = figures.subtotal
    sale.discount = figures.discount
    sale.tax = figures.tax
    sale.total = figures.total

    payments = _build_payments(context, ticket, sale, entry)
    return sale, lines, payments, moves


#: A per-move offset, so **no two seeded movements share an instant**. The lot
#: trace is ordered by `(recorded_at, id)` and read as a running balance, so two
#: moves at one timestamp put the tie-break back on a uuid -- which is the exact
#: defect S3's own history check exists to catch, arriving from this stage by a
#: different door. Microseconds, so twelve thousand lines drift the clock by
#: twelve milliseconds and nothing on a screen moves.
_STAMP = {"ordinal": 0}


def _next_stamp() -> timedelta:
    _STAMP["ordinal"] += 1
    return timedelta(microseconds=_STAMP["ordinal"])


def _build_line(context, ticket, line, sale, world, heads):
    item = world.items.get(line["item_key"])
    if item is None:
        return None
    lot = (
        world.lots.get(item.id, {}).get(line["lot_code"]) if line["lot_code"] else None
    )
    price = _price_on(world, item, sale.location, ticket["day"])
    if price is None:
        return None
    key = f"{ticket['key']}:{line['position']}"
    # **A line a return points at is never dropped.** The returns are planned
    # against `(ticket, position)` before any price is resolved, so thinning one
    # away here would leave `sale_return_lines.sale_line_id` naming a row that
    # was never written -- which Django's own constraint check catches at
    # teardown and a pilot would catch as a refund against nothing.
    returned = ticket["return"]
    if not (returned and returned["sale_line_position"] == line["position"]):
        if not _demand_answers(world, item, price, key):
            return None
    unit_price = money.cents(price)
    row = SaleLine(
        id=context.uid("sale_lines", key),
        tenant_id=context.tenant_id,
        sale=sale,
        location=sale.location,
        position=line["position"],
        item=item,
        lot=lot,
        quantity=line["quantity"],
        unit_price=unit_price,
        discount=money.ZERO,
        vat_class=item.vat_class,
        tax_amount=money.line_tax(
            unit_price, line["quantity"], money.ZERO, item.vat_class
        ),
        # Stamped from the lot at the moment of sale, and from the item's own
        # standing cost where there is no lot (§3, ledger). Null where none
        # exists -- never zero, which would read as a 100% margin.
        unit_cost=lot.unit_cost if lot is not None else item.service_cost,
        client_uuid=context.uid("sale-line-key", key),
        device_id=ticket["device_id"],
        occurred_at=ticket["occurred_at"],
        recorded_at=ticket["recorded_at"],
    )
    move = None
    if item.tracks_stock:
        head = heads.get((ticket["sede"], line["item_key"]))
        move = ledger.Move(
            id=context.uid("stock_moves", f"sale:{key}"),
            location_id=sale.location_id,
            item_id=item.id,
            lot_id=lot.id if lot is not None else None,
            quantity=-line["quantity"],
            type=StockMoveType.SALE,
            document_type="sales",
            document_id=sale.id,
            unit_cost=row.unit_cost,
            occurred_at=ticket["occurred_at"],
            recorded_at=ticket["recorded_at"] + _next_stamp(),
            # The till's own observation, and the seed is a till here: it drew a
            # lot and it knows which one FEFO offered.
            fefo_override=bool(
                lot is not None and head is not None and lot.lot_code != head
            ),
            key=f"seed:sale:{key}",
        )
    return row, move


def _build_payments(context, ticket, sale, entry):
    count = _payment_count(ticket)
    if count == 0 or sale.total <= money.ZERO:
        return []
    seed = stable_int("pay", ticket["key"])
    first = _method(seed)
    if count == 1:
        amounts = [(first, sale.total)]
    else:
        # A split ticket, so the `Cobro` dialog's own case is in the data. The
        # second method takes a third, rounded to the peso, and the first takes
        # the remainder -- the two always sum to the total, which is the rule
        # the dialog will not commit without.
        part = money.cents(sale.total / 3)
        amounts = [(first, sale.total - part), (_method(seed >> 8), part)]
        if amounts[0][0] == amounts[1][0]:
            amounts = [(first, sale.total)]
    rows = []
    for index, (method, amount) in enumerate(amounts):
        if amount <= money.ZERO:
            continue
        key = f"{ticket['key']}:{index}"
        rows.append(
            Payment(
                id=context.uid("payments", key),
                tenant_id=context.tenant_id,
                sale=sale,
                location=sale.location,
                method=method,
                amount=amount,
                reference=""
                if method == PaymentMethod.CASH
                else f"REF{seed % 900000:06d}",
                client_uuid=context.uid("payment-key", key),
                device_id=ticket["device_id"],
                occurred_at=ticket["occurred_at"],
                recorded_at=ticket["recorded_at"],
            )
        )
        if method == PaymentMethod.CASH:
            entry["cash"] += amount
    return rows


def _method(seed):
    draw = seed % 100
    running = 0
    for method, share in METHOD_MIX:
        running += share
        if draw < running:
            return method
    return PaymentMethod.CASH


def _write_returns(context, tickets, world, shifts):
    """The devoluciones: stock back on the **original** lot, money stamped from
    the **original** line."""
    headers, lines = [], []
    moves: dict = {}
    numbers: dict[str, int] = {}
    for ticket in tickets:
        plan_row = ticket["return"]
        if not plan_row:
            continue
        location = world.locations.get(ticket["sede"])
        entry = shifts.get(plan_row["shift_key"]) or shifts.get(ticket["shift_key"])
        if location is None or entry is None:
            continue
        sale_id = context.uid("sales", ticket["key"])
        line_key = f"{ticket['key']}:{plan_row['sale_line_position']}"
        line_id = context.uid("sale_lines", line_key)
        source = ticket["lines"][0]
        item = world.items.get(source["item_key"])
        if item is None:
            continue
        lot = (
            world.lots.get(item.id, {}).get(source["lot_code"])
            if source["lot_code"]
            else None
        )
        price = _price_on(world, item, location, ticket["day"])
        if price is None:
            continue

        numbers[ticket["device_code"]] = numbers.get(ticket["device_code"], 0) + 1
        at = timezone.make_aware(datetime.combine(plan_row["day"], time(10, 20)))
        key = f"{ticket['key']}:return"
        header = SaleReturn(
            id=context.uid("sale_returns", key),
            tenant_id=context.tenant_id,
            sale_id=sale_id,
            location=location,
            shift=entry["row"],
            number=sale_service.compose_number(
                f"{ticket['device_code']}D", numbers[ticket["device_code"]]
            ),
            reason=plan_row["reason"],
            refund_method=PaymentMethod.CASH,
            returned_by_user=_seller(world, location),
            returned_by_name=getattr(_seller(world, location), "name", "") or "",
            client_uuid=context.uid("return-key", key),
            device_id=ticket["device_id"],
            occurred_at=at,
            recorded_at=at + timedelta(seconds=3),
        )
        unit_price = money.cents(price)
        quantity = plan_row["quantity"]
        line_key = f"{key}:0"
        row = SaleReturnLine(
            id=context.uid("sale_return_lines", line_key),
            tenant_id=context.tenant_id,
            sale_return=header,
            sale_line_id=line_id,
            location=location,
            item=item,
            lot=lot,
            quantity=quantity,
            unit_price=unit_price,
            discount=money.ZERO,
            vat_class=item.vat_class,
            tax_amount=money.line_tax(unit_price, quantity, money.ZERO, item.vat_class),
            unit_cost=lot.unit_cost if lot is not None else item.service_cost,
            client_uuid=context.uid("return-line-key", line_key),
            device_id=ticket["device_id"],
            occurred_at=at,
            recorded_at=at + timedelta(seconds=3),
        )
        figures = money.totals([row])
        header.total = figures.total
        header.tax = figures.tax
        headers.append(header)
        lines.append(row)
        # The refund comes out of the drawer that is open on the day of the
        # return, which is not the drawer the sale was rung in.
        entry["cash"] -= header.total
        if item.tracks_stock:
            moves.setdefault(ticket["device_id"], []).append(
                ledger.Move(
                    id=context.uid("stock_moves", f"return:{line_key}"),
                    location_id=location.id,
                    item_id=item.id,
                    lot_id=lot.id if lot is not None else None,
                    quantity=quantity,
                    type=StockMoveType.CUSTOMER_RETURN,
                    document_type="sale_returns",
                    document_id=header.id,
                    unit_cost=row.unit_cost,
                    occurred_at=at,
                    recorded_at=at + timedelta(seconds=3),
                    key=f"seed:return:{line_key}",
                )
            )
    _insert(context, SaleReturn, headers, "sale_returns")
    _insert(context, SaleReturnLine, lines, "sale_return_lines")
    _append(context, world, moves)


def _count_drawers(context, shifts):
    """The cash count on every closed turno, once the cash is known.

    The variance is computed from what this fixture actually rang rather than
    asserted, so the number on the screen is the arithmetic and not a literal --
    and it is **never uniformly zero**, because a column of zeros is exactly what
    an arithmetic error would produce too.
    """
    del context
    rows = []
    for index, (_key, entry) in enumerate(sorted(shifts.items())):
        # **Only the turnos this run actually created.** A re-run finds them
        # already counted, and rewriting a row with the value it already holds
        # is still a write -- which is the one thing *the seed run twice changes
        # nothing* is about.
        if entry["open"] or not entry.get("fresh"):
            continue
        shift = entry["row"]
        expected = money.cents(OPENING_FLOAT + entry["cash"])
        offset = Decimal(VARIANCES[index % len(VARIANCES)]) * 1000
        shift.declared_total = money.cents(expected + offset)
        shift.variance = money.cents(offset)
        rows.append(shift)
    Shift.objects.bulk_update(rows, ["declared_total", "variance"], batch_size=500)


def _insert(context, model, rows, table):
    """Create only the rows that are not there, so **a second run genuinely
    changes nothing** -- `updated_at` included.

    The same helper S1's fixture uses, and for the same reason: every seeded id
    is derived from a natural key, so a rebuilt seed keeps the ids it had and a
    re-run is a set of rows already present rather than a set of collisions.
    """
    held = set(
        model._default_manager.filter(
            tenant_id=context.tenant_id, id__in=[row.id for row in rows]
        ).values_list("id", flat=True)
    )
    missing = [row for row in rows if row.id not in held]
    if missing:
        model._default_manager.bulk_create(missing, batch_size=1000)
    context.wrote(table, len(rows))
    return {row.id for row in missing}


def _append(context, world, pending):
    """Movements through S3's service and never around it (rule 7).

    **Grouped by till, because a movement names the equipment it happened on and
    the person who made it** -- `append` stamps both per call, and a device
    column empty on twelve thousand rows is a trace nobody would accept. Within
    a till they are batched by size rather than by line: the append's cost is a
    handful of statements whatever the batch holds, and a per-line append over a
    hundred and eighty days of trading is the difference between a seed that
    runs before a demo and one that does not.

    **The entry point is the service's own**, which is the whole of the rule: a
    direct insert into `stock_moves` would leave the projection untouched and
    produce an Existencias screen contradicting the Panel.
    """
    for device_id, batch in sorted(pending.items(), key=lambda one: str(one[0])):
        if not batch:
            continue
        device = world.devices.get(device_id)
        actor = world.cashiers.get(
            getattr(device, "location_id", None)
        ) or world.cashiers.get("owner")
        ledger.append(
            batch,
            tenant_id=context.tenant_id,
            actor=actor,
            device=device,
            request_id="seed:counter",
        )
        context.wrote("stock_moves", len(batch))
    pending.clear()


register(
    "counter",
    tables=(
        "shifts",
        "sales",
        "sale_lines",
        "payments",
        "sale_returns",
        "sale_return_lines",
        # Declared because every seeded sale consumes stock the way a real one
        # does. S3 declares it too; the guard's union is what lets both write
        # through the one service that owns the table.
        "stock_moves",
    ),
    requires=("stock", "devices", "catalog"),
    build=build,
    owned_ids=owned_ids,
)
