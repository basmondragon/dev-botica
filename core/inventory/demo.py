"""The `stock` fixture, registered with **S0's** `seed_demo_tenant`.

S3 ships no seed command. It registers one fixture, declares S1's catalog as a
dependency, and writes its own stage's tables. **It contributes no
`stock_on_hand` rows at all**: the projection is a projection, and this fixture
moves stock through the ledger service exactly as production does. What breaks
if that is wrong is the whole point of the stage -- acceptance 3 would pass
against fixtures that never went through the ledger, and the seed would be the
one code path in the product that changes a quantity in place.

**Existencias is why the fixtures have to be good.** It is the most data-dense
screen in the product, the handoff draws it fully populated at the network's
real scale, and it is the surface where an absent denominator, a state nobody
produced or a clause with nothing to name is visible at a glance. So the plan
below is specified by **what the screen must render** rather than by what the
tables must contain: 4.284 rows at `(item, location, lot)` grain, exactly 312 of
them requiring action, all seven states a dozen rows deep, both dot styles, all
four badge families, a bar where a policy exists and none where it does not, and
a `Quiebre` clause with a sede to name.

**Every offset is computed from the tenant's own horizons and relative to the
run date.** Hard-coded dates would turn a `Vence en 5 meses` row into a
`Vencido` one six months from now and quietly change what the screen
demonstrates; a pilot that moves a window would empty a tier.

**Every random draw is seeded from a fixed constant**, so two runs produce the
same screen. A demo whose numbers move between runs cannot be compared against a
drawing, and a defect naming a row nobody can reproduce is a defect nobody acts
on.
"""

import hashlib
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache

from django.utils import timezone

from core.catalog import demo as catalog_demo
from core.demo import identity
from core.demo.registry import register
from core.inventory import ledger, settings as inventory_settings, states
from core.models import (
    CountScope,
    CountStatus,
    Item,
    Location,
    Lot,
    PolicySource,
    Role,
    StockCount,
    StockCountLine,
    StockMoveType,
    StockPolicy,
    Tenant,
    Transfer,
    TransferLine,
    TransferStatus,
    User,
)

#: The network's whole grid, and the figure the footer reads. **A different
#: measure from S2's per-sede production sizing**, and it coincides on the
#: number only because the seed does not stock every item at every sede.
TARGET_ROWS = 4284

#: What `Estado · Requiere acción` narrows to, and what the footer annotates.
#: The four action states, and they add to exactly this.
TARGET_ACTION = 312

#: How many rows each state gets under `default`. Every one of the seven is a
#: dozen rows deep or more, so acceptance 14 is satisfied from the seed rather
#: than from rows somebody built by hand for the test: both dot styles, all four
#: badge families and the precedence of the derivation are visible on one
#: screen.
#:
#: The four action states sum to 312. `sufficient` is the remainder and is never
#: stated, because a literal there would be one more number to keep in step.
STATE_PLAN = {
    states.EXPIRED: 24,
    states.STOCKOUT: 96,
    states.EXPIRING_URGENT: 84,
    states.EXPIRING: 60,
    states.REORDER_POINT: 108,
    states.OVERSTOCK: 72,
}

#: Where each state's first row lands in the default display order, and it is
#: the one thing in this file tuned to a screenshot: **the five states the
#: handoff draws all appear inside the first page**, with no filter touched and
#: no sort pressed, while `Vencido` and the second expiry tier are one press of
#: the `Estado` sort away (acceptance 26).
#:
#: The strides that follow are the row count over the state's own count, so
#: every state is spread evenly through the grid rather than clumped at the
#: front -- a reviewer paging to 40 sees the same mixture as a reviewer on
#: page 1.
FIRST_ROW = {
    states.STOCKOUT: 5,
    states.EXPIRING_URGENT: 9,
    states.REORDER_POINT: 13,
    states.OVERSTOCK: 17,
    states.EXPIRING: 33,
    states.EXPIRED: 41,
}

#: The handoff's own lot codes, on the handoff's own products. These fifteen are
#: also the fixture's **shared** lots -- one lot across every sede that stocks
#: the product, rather than one per sede -- which is what gives the recall answer
#: a lot that several locations hold and the reverse lookup something to find.
DRAWN_LOTS = {
    "Acetaminofén 500 mg × 100": "A-2291",
    "Sales de rehidratación oral": "R-0148",
    "Losartán 50 mg × 30": "L-7730",
    "Amoxicilina 500 mg × 20": "M-3312",
    "Omeprazol 20 mg × 30": "O-5027",
    "Ibuprofeno 400 mg × 50": "I-9004",
    "Metformina 850 mg × 30": "F-1180",
    "Loratadina 10 mg × 10": "T-4419",
    "Enalapril 20 mg × 30": "E-6602",
    "Atorvastatina 20 mg × 30": "V-2075",
    "Suero fisiológico 500 ml": "S-8891",
    "Naproxeno 500 mg × 20": "N-1136",
    "Dipirona 500 mg × 10": "D-3308",
    "Salbutamol inhalador 100 mcg": "B-7741",
    "Hidroclorotiazida 25 mg × 30": "H-9928",
}

#: The lot whose history runs past the record panel's fifty-move cap, so the cap
#: is exercised rather than assumed.
DEEP_HISTORY_ITEM = "Acetaminofén 500 mg × 100"
DEEP_HISTORY_MOVES = 60

#: **How far back a shelf's life reaches, and where it stops.**
#:
#: Every seeded move used to be stamped at the instant the seed ran, which left
#: `(recorded_at, id)` sorting a lot's whole life by uuid -- so the lot trace
#: showed a merma before the entrada that made it possible and a running balance
#: that dipped to −47 on a shelf that was never short. The final balance was
#: right and every individual row was right, which is exactly the kind of wrong
#: nobody catches by reading the code.
#:
#: History ends `HISTORY_SETTLES_DAYS` back so the documents that come after it
#: -- a dispatch at 3 days, a receipt at 1, a count closed at 2 -- land after
#: every move they depend on rather than interleaved with them.
HISTORY_WINDOW_DAYS = 240
HISTORY_SETTLES_DAYS = 12

#: One row in twelve carries **no policy at all**, so `Sin política definida` in
#: the record panel is a state somebody has actually seen, the in-cell bar is
#: correctly absent where there is no denominator, and states 5 and 6 are
#: provably unreachable without one.
UNMANAGED_EVERY = 12

#: What each profile builds. `young` and `cold` take the same rows as `default`:
#: twelve days instead of 180 narrows a *sales* history, and this stage registers
#: none -- `cold` is in practice the profile S3 is always built against, because
#: there are no sales at S3 under any profile.
PROFILES: dict[str, dict] = {
    "default": {"rows": TARGET_ROWS, "products": None, "everywhere": False},
    "young": {"rows": TARGET_ROWS, "products": None, "everywhere": False},
    "cold": {"rows": TARGET_ROWS, "products": None, "everywhere": False},
    # **The only profile that reaches the 2.000-row cap** on the other-location
    # set, and therefore the only one the registry amendment can be measured
    # against. Six hundred references at every one of twenty sedes, and a
    # deliberately higher share of them in trouble: the set a till pulls is
    # *its own sede's problems* × *the sedes that hold them*, so at nineteen
    # other locations it takes about a hundred and five troubled references to
    # reach two thousand rows.
    #
    # The share is higher here than under `default` on purpose, and it is the
    # one number in this file chosen to make a check bite rather than to look
    # like a droguería. **A stress profile whose stress never arrives measures
    # nothing**: with `default`'s five per cent the cap is never approached at
    # any network size, so a missing cap would pass every run and fail at a
    # pilot.
    "scale": {
        "rows": 12000,
        "products": 600,
        "everywhere": True,
        "state_plan": {
            states.EXPIRED: 90,
            states.STOCKOUT: 1000,
            states.EXPIRING_URGENT: 240,
            states.EXPIRING: 180,
            states.REORDER_POINT: 1400,
            states.OVERSTOCK: 240,
        },
    },
    # One sede's worth of stock, enough for Existencias to render and for the
    # tenant-isolation check to have a second tenant to be isolated from. **With
    # one sede there is no other location to name**, so a `Quiebre` row carries
    # no `hay N en <sede>` clause -- that is the derivation rendering correctly
    # on a one-sede network, not a missing fixture.
    "minimal": {"rows": 24, "products": None, "everywhere": True},
}


def stable_int(*parts) -> int:
    """A deterministic integer from a natural key.

    `hash()` is salted per process, so a fixture built on it produces a
    different screen on every run -- which is the one thing *Demo seed* forbids
    outright.
    """
    digest = hashlib.blake2b(
        "|".join(str(one) for one in parts).encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big")


# ---------------------------------------------------------------------------
# The plan: every row this fixture would write, computed without touching the
# database, so that `owned_ids` and `build` cannot disagree about what it owns.
# ---------------------------------------------------------------------------


def _products(profile):
    """The catalog rows that can hold stock: products, never services (A7)."""
    rows = [row for row in catalog_demo.item_plan(profile) if row.get("tracks_stock")]
    limit = PROFILES[profile]["products"]
    return rows[:limit] if limit else rows


def _sede_codes(profile):
    return [code for code, *_rest in identity.sedes(profile)]


@lru_cache(maxsize=8)
def stock_plan(profile):
    """Every `(item, sede, lot)` row, in the grid's own default order, with the
    state it must render, the quantity behind it and the policy that gives it a
    denominator.

    The order is `(item name, sede name, lot code)` -- the grid's default sort --
    because the state schedule is positional: a state placed at index 5 has to be
    on the first page a reviewer opens, and the only ordering that makes that
    true is the one the screen itself uses.
    """
    shape = PROFILES[profile]
    products = _products(profile)
    codes = _sede_codes(profile)
    if not products or not codes:
        return []

    sede_names = {code: name for code, name, *_rest in identity.sedes(profile)}

    rows = []
    for product in products:
        key = f"{product['name']}|{product['presentation']}"
        seed = stable_int("stocked", key)
        # How many sedes hold this reference, and which. A network stocks its
        # fast movers everywhere and its long tail in one or two sedes, which is
        # what makes the `Sede` chip worth having and what makes a `Quiebre`
        # clause have a sede to name. `scale` and `minimal` hold everything
        # everywhere, each for its own reason: the first so the other-location
        # set has enough to reach its cap from, the second because there is only
        # one sede to hold anything in.
        held = (
            len(codes)
            if shape["everywhere"] or len(codes) <= 2
            else 1 + seed % len(codes)
        )
        chosen = sorted(codes, key=lambda code: stable_int("sede", key, code))[:held]
        for code in chosen:
            rows.append(
                {
                    "item_key": key,
                    "item_name": product["name"],
                    "tracks_lots": product["tracks_lots"],
                    "tracks_expiry": product["tracks_expiry"],
                    "sede": code,
                    "sede_name": sede_names[code],
                    "shared_lot": product["name"] in DRAWN_LOTS,
                }
            )

    rows.sort(key=lambda row: (row["item_name"], row["sede_name"], row["item_key"]))
    rows = rows[: shape["rows"]]
    for index, row in enumerate(rows):
        row["index"] = index
        row["lot_code"] = _lot_code(row)
    _unique_lot_codes(rows)
    _assign_states(rows, profile, len(codes))
    _assign_quantities(rows)
    _mark_deep_history(rows)
    return rows


def _mark_deep_history(rows):
    """Name the one row whose lot runs past the record panel's fifty-move cap.

    The first row of the drawn product that carries a lot at all -- so the cap
    is exercised on a lot somebody can find by its code rather than on whichever
    row an index happened to land on.
    """
    for row in rows:
        if row["item_name"] == DEEP_HISTORY_ITEM and row["lot_code"]:
            row["deep_history"] = True
            return
    for row in rows:
        if row["lot_code"] and row["state"] == states.SUFFICIENT:
            row["deep_history"] = True
            return


def _lot_code(row):
    """The handoff's own code on a drawn product, and a derived one otherwise.

    A drawn product's lot is **shared across every sede that stocks it**, which
    is what makes the recall answer's reverse lookup return more than one
    location; every other row gets its own lot, which is how a sede's own
    expiry dates come to differ from its neighbour's.
    """
    if not row["tracks_lots"]:
        return ""
    if row["shared_lot"]:
        return DRAWN_LOTS[row["item_name"]]
    seed = stable_int("lot", row["item_key"], row["sede"])
    letter = "ABCDEFGHJKLMNPRSTVWZ"[seed % 20]
    return f"{letter}-{seed % 9000 + 1000}"


def _unique_lot_codes(rows):
    """`UNIQUE (tenant_id, item_id, lot_code)` is a real constraint, so two
    sedes' derived codes for one reference must not collide.

    A derived code is a hash of `(item, sede)` over twenty letters and nine
    thousand numbers, which collides within one reference about once in a
    catalog this size -- rarely enough to pass a first run and often enough to
    fail somebody else's. The bump is deterministic because the rows are walked
    in the plan's own fixed order.

    A **shared** lot is deliberately the same code at every sede that holds it:
    that is one lot in several places, which is what a recall has to be able to
    find, and bumping it would turn one lot into six.
    """
    used: dict[str, set] = {}
    for row in rows:
        code = row["lot_code"]
        if not code:
            continue
        seen = used.setdefault(row["item_key"], set())
        if row["shared_lot"]:
            seen.add(code)
            continue
        while code in seen:
            letter, _, number = code.partition("-")
            code = f"{letter}-{(int(number) - 1000 + 1) % 9000 + 1000}"
        seen.add(code)
        row["lot_code"] = code


def _assign_states(rows, profile, sedes):
    """Place each state's rows evenly, starting where `FIRST_ROW` says.

    A scheduled index that lands on a row that cannot carry the state -- an
    expiry state on a reference that tracks no expiry, a `Quiebre` on a
    reference only one sede holds, so there would be no sede to name -- walks
    forward to the next free row that can. Deterministic, and it is what keeps
    the counts exact rather than approximately right.
    """
    for row in rows:
        row["state"] = states.SUFFICIENT
    total = len(rows)
    if total == 0:
        return
    plan = PROFILES[profile].get("state_plan") or STATE_PLAN
    scale = total / TARGET_ROWS
    held: dict[str, int] = {}
    for row in rows:
        held[row["item_key"]] = held.get(row["item_key"], 0) + 1

    taken: set[int] = set()
    for state in (
        states.EXPIRED,
        states.STOCKOUT,
        states.EXPIRING_URGENT,
        states.REORDER_POINT,
        states.OVERSTOCK,
        states.EXPIRING,
    ):
        wanted = plan[state] if PROFILES[profile].get("state_plan") else None
        if wanted is None:
            wanted = max(1, round(plan[state] * scale)) if total > 60 else 2
        wanted = min(wanted, max(1, total // 3))
        for ordinal in range(wanted):
            # **The first row of each state is placed, the rest are scattered.**
            # The first is what puts the five drawn states on the page a
            # reviewer opens; every other one starts from a deterministic point
            # in the grid, because a fixed stride is a multiple of the number of
            # sedes often enough to land every row of a state on the same few
            # sedes -- which leaves a whole sede with no problems at all and the
            # other-location set with nothing to select.
            start = (
                int(FIRST_ROW[state] * scale)
                if ordinal == 0
                else stable_int("place", state, ordinal) % total
            )
            placed = _place(rows, taken, held, state, start, sedes)
            if placed is None:
                break


def _place(rows, taken, held, state, start, sedes):
    """The first free row at or after `start` that can carry this state."""
    total = len(rows)
    for step in range(total):
        index = (start + step) % total
        if index in taken:
            continue
        row = rows[index]
        if state in (states.EXPIRED, states.EXPIRING_URGENT, states.EXPIRING):
            if not row["tracks_expiry"]:
                continue
            # A shared lot's expiry is shared by every sede holding it, so an
            # expiry state on one of those rows would silently drag its siblings
            # into the same state and put the counts out. The fifteen drawn
            # products are the shared ones, and they carry their states through
            # quantity alone.
            if row["shared_lot"]:
                continue
        if state == states.STOCKOUT and sedes > 1 and held.get(row["item_key"], 0) < 2:
            # `Quiebre · hay 96 en Suba` needs a sede to name. A reference only
            # one sede holds would render the badge bare, which is correct on a
            # one-sede network -- and on one, the guard is skipped so `minimal`
            # still shows a quiebre, with no clause and nothing missing.
            continue
        taken.add(index)
        row["state"] = state
        return index
    return None


def _assign_quantities(rows):
    """A quantity, a policy and an expiry offset per row, each derived from the
    row's own key and its target state.

    **No two sedes hold the same quantity of the same reference.** The
    availability clause names the sede holding the most units with ties broken
    by name, and a tie would make the string depend on the ordering of a query
    -- so the last unique quantity per reference is bumped until it is unique,
    in the plan's own fixed order.
    """
    used: dict[str, set] = {}
    for row in rows:
        seed = stable_int("qty", row["item_key"], row["sede"])
        capacity = 60 + seed % 440
        reorder = max(4, capacity // 6)
        unmanaged = seed % UNMANAGED_EVERY == 0 and row["state"] in (states.SUFFICIENT,)
        row["policy"] = (
            None
            if unmanaged
            else {
                "min_quantity": max(1, reorder // 2),
                "max_quantity": capacity,
                "reorder_point": reorder,
                "target_coverage_days": 20 + seed % 40,
            }
        )
        row["quantity"] = _quantity(row, seed, capacity, reorder)
        row["expiry"] = _expiry_bucket(row)

        # Unique per reference, so the availability clause has one answer.
        seen = used.setdefault(row["item_key"], set())
        while row["quantity"] in seen and row["state"] != states.STOCKOUT:
            row["quantity"] += 1
        seen.add(row["quantity"])


def _quantity(row, seed, capacity, reorder):
    state = row["state"]
    if state == states.STOCKOUT:
        return 0
    if state == states.REORDER_POINT:
        return 1 + seed % max(1, reorder)
    if state == states.OVERSTOCK:
        return capacity + 1 + seed % 60
    # Every other state renders a healthy figure and takes its badge from the
    # lot's date: expiry outranks the reorder point, and a `Vence en 6 meses`
    # row sitting at a comfortable quantity is what makes that visible.
    span = max(1, capacity - reorder - 1)
    return reorder + 1 + seed % span


def _expiry_bucket(row):
    """Which horizon this row's lot falls in, as a name rather than a date.

    The dates themselves are computed in `build` from the tenant's own
    `expiry_alert_days` and `expiry_notice_days` and from the run date, so a
    pilot that moves a window does not empty a tier and the seed does not decay.
    """
    if not row["tracks_expiry"]:
        return None
    return {
        states.EXPIRED: "expired",
        states.EXPIRING_URGENT: "urgent",
        states.EXPIRING: "notice",
    }.get(row["state"], "far")


# ---------------------------------------------------------------------------
# The documents: four transfers and two counts, on rows chosen from the plan
# ---------------------------------------------------------------------------


def _document_rows(rows, profile):
    """The plan rows the transfers and the counts act on.

    They are chosen from the comfortable middle -- `sufficient`, a policy, a
    quantity well clear of both thresholds -- so that the moves a document
    writes cannot tip a row into a different badge and put the 312 out.
    """
    if len(rows) < 40 or len(set(row["sede"] for row in rows)) < 2:
        return {"transfers": [], "counts": []}
    eligible = [
        row
        for row in rows
        if row["state"] == states.SUFFICIENT
        and row["policy"]
        and row["quantity"] > row["policy"]["reorder_point"] + 80
        and row["quantity"] < row["policy"]["max_quantity"] - 20
    ]
    del profile
    pairs = _transfer_pairs(eligible)
    if len(pairs) < len(TRANSFER_PLAN):
        return {"transfers": [], "counts": []}
    transfers = pairs[: len(TRANSFER_PLAN)]

    # **A count is walked at one sede**, so its lines are that sede's rows and
    # nothing else. Lines drawn from four different sedes would produce a
    # document the product's own counting screen could never have created, and
    # the adjusting moves would land on shelves nobody counted.
    spent = {id(row) for pair in transfers for row in pair}
    at_a_sede: dict[str, list] = {}
    for row in eligible:
        if id(row) in spent:
            continue
        at_a_sede.setdefault(row["sede"], []).append(row)
    counted = max(at_a_sede.values(), key=len, default=[])
    if len(counted) < len(COUNT_DIFFERENCES) + 1:
        return {"transfers": transfers, "counts": []}
    return {
        "transfers": transfers,
        "counts": counted[: len(COUNT_DIFFERENCES) + 1],
    }


#: The four transfer states, and what each moves. `draft` moves nothing -- its
#: lines are a request and not a hold, because nothing in v1 reserves stock.
#: `partial` dispatches sixty and receives forty-eight, which is acceptance 9's
#: own arithmetic and leaves twelve units **En tránsito**, on no shelf and
#: visible as exactly that.
TRANSFER_PLAN = [
    (TransferStatus.DRAFT, 0, 0),
    (TransferStatus.DISPATCHED, 40, 0),
    (TransferStatus.RECEIVED, 25, 25),
    (TransferStatus.PARTIAL, 60, 48),
]

#: The closed count's differences, per line: what a shelf actually held against
#: what the record said. Two short and one over, because a count that only ever
#: finds less is a count nobody believes.
COUNT_DIFFERENCES = (-3, -7, 5)


def _document_effects(rows, profile):
    """The net delta each document applies per `(sede, item, lot)` key.

    The opening moves are written **short by exactly this**, so the projection
    lands on the plan after every document has been applied. Without it a
    transfer would silently move a row off its own badge and the footer would
    read 311.
    """
    picks = _document_rows(rows, profile)
    effects: dict[tuple, int] = {}
    for index, (_status, dispatched, received) in enumerate(TRANSFER_PLAN):
        origin, destination = _transfer_ends(picks["transfers"], index)
        if origin is None or destination is None:
            continue
        if dispatched:
            _add(effects, origin, -dispatched)
        if received:
            _add(effects, destination, received)
    for line, difference in zip(picks["counts"][1:], COUNT_DIFFERENCES):
        _add(effects, line, difference)
    return effects, picks


def stocked_days_back(row) -> int:
    """How many days back this row's shelf was actually stocked.

    **Public because S4's fixture sells from these shelves and must not date a
    sale before the receipt that made it possible.** It is the first stamp
    `_stamps` produces for the row, and it is not the same for every row: a row
    with one opening move gets it at `newest` -- twelve to nineteen days back,
    because a single move standing for a shelf's whole history reads as the day
    it was loaded -- while a row with a real history gets it at `oldest`, five
    to eight months back. Deriving it here rather than restating the arithmetic
    one module over is what keeps the two from drifting apart silently.
    """
    seed = stable_int("history", row["item_key"], row["sede"])
    if _history_depth(row) > 1:
        return HISTORY_WINDOW_DAYS - seed % 90
    return HISTORY_SETTLES_DAYS + seed % 8


def documented_keys(profile) -> set:
    """The `(sede, item_key, lot_code)` rows this fixture's own documents move.

    **Public because S4's fixture must not sell from them.** A row a transfer or
    a count later credits carries *less* than its planned quantity for most of
    the window -- the opening moves are written short by exactly the document's
    delta, so the projection lands on the plan only once the document has been
    applied. A sale against such a row inside that window is a lot trace that
    dips below zero on a shelf that was never short, which is the defect
    `test_a_seeded_lot_reads_as_a_history_and_never_dips_below_zero` exists to
    catch.
    """
    effects, _picks = _document_effects(stock_plan(profile), profile)
    return set(effects)


def _transfer_ends(picked, index):
    """One transfer's two ends: **the same reference at two sedes.**

    A transfer that took one product out of the origin and credited a different
    one at the destination is not a transfer -- it is two unrelated movements
    wearing one document number, and the seeded `partial` would then show
    `En tránsito 12` on one reference while the 48 received units landed on
    another. The pairing is by `item_key` for exactly that reason, and a
    reference the plan holds at only one sede cannot be transferred at all.
    """
    if index >= len(picked):
        return None, None
    origin, destination = picked[index]
    return origin, destination


def _transfer_pairs(eligible):
    """Rows paired into `(origin, destination)` on one reference.

    Walked in the plan's own fixed order, so the pairing is deterministic; a
    reference is used once, so two transfers never fight over the same shelf.
    """
    by_item: dict[str, list] = {}
    for row in eligible:
        by_item.setdefault(row["item_key"], []).append(row)
    pairs = []
    for rows in by_item.values():
        if len(rows) >= 2 and rows[0]["sede"] != rows[1]["sede"]:
            pairs.append((rows[0], rows[1]))
    return pairs


def _add(effects, row, delta):
    effects[_key(row)] = effects.get(_key(row), 0) + delta


def _key(row):
    return (row["sede"], row["item_key"], row["lot_code"])


# ---------------------------------------------------------------------------
# Ids
# ---------------------------------------------------------------------------


def _lot_key(row):
    """A shared lot is one row in `lots`; every other lot is one per sede."""
    if row["shared_lot"]:
        return f"{row['item_key']}|{row['lot_code']}"
    return f"{row['item_key']}|{row['sede']}|{row['lot_code']}"


def _move_keys(profile):
    """Every move this fixture appends, as a stable key per move."""
    rows = stock_plan(profile)
    effects, picks = _document_effects(rows, profile)
    keys = []
    for row in rows:
        for ordinal in range(_history_depth(row)):
            keys.append(f"open:{_key(row)}:{ordinal}")
    for index, (_status, dispatched, received) in enumerate(TRANSFER_PLAN):
        origin, destination = _transfer_ends(picks["transfers"], index)
        if origin is None:
            continue
        if dispatched:
            keys.append(f"tout:{index}")
        if received:
            keys.append(f"tin:{index}")
    for ordinal, _line in enumerate(picks["counts"][1:]):
        keys.append(f"count:{ordinal}")
    del effects
    return keys


def _history_depth(row):
    """How many moves stand behind one row.

    One opening move on most, two where a shrinkage or a second receipt makes
    the record panel worth opening, and sixty on the one row that exercises the
    panel's fifty-move cap. **Every row has at least one**, so a panel opened on
    whichever row a reviewer happens to click is never the empty state.

    The deep row is named by a flag the plan sets, not by an arithmetic
    coincidence between the item's name and its position: a modulus over an
    index that moves whenever the catalog does is a fixture that silently stops
    covering the thing it was written for, which is exactly what happened here.
    """
    if row.get("deep_history"):
        return DEEP_HISTORY_MOVES
    if row["state"] == states.STOCKOUT:
        return 2
    return 2 if stable_int("depth", row["item_key"], row["sede"]) % 5 == 0 else 1


def owned_ids(context):
    """Exactly the rows this fixture writes in its guard tables.

    `stock_on_hand` is **not** among them, and that is the point: this fixture
    writes no projection row, so it owns none. The projection is whatever the
    ledger produced, and the rebuild is what proves it.
    """
    profile = context.profile
    rows = stock_plan(profile)
    _effects, picks = _document_effects(rows, profile)
    lots = {context.uid("lots", _lot_key(row)) for row in rows if row["lot_code"]}
    policies = {
        context.uid("stock_policies", f"{row['sede']}|{row['item_key']}")
        for row in rows
        if row["policy"]
    }
    moves = {context.uid("stock_moves", key) for key in _move_keys(profile)}
    transfers = {
        context.uid("transfers", str(index)) for index in range(len(TRANSFER_PLAN))
    }
    lines = {
        context.uid("transfer_lines", str(index)) for index in range(len(TRANSFER_PLAN))
    }
    counts = {context.uid("stock_counts", name) for name in ("open", "closed")}
    count_lines = {
        context.uid("stock_count_lines", f"{name}:{ordinal}")
        for name in ("open", "closed")
        for ordinal in range(len(picks["counts"]))
    }
    return {
        "lots": lots,
        "stock_policies": policies,
        "stock_moves": moves,
        "transfers": transfers,
        "transfer_lines": lines,
        "stock_counts": counts,
        "stock_count_lines": count_lines,
    }


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def _tills(tenant_id):
    """One till per sede, so a seeded move names the equipment it happened on.

    S2's fixture puts `Caja 1` at every sede and a second at the busiest; the
    first by label is the one a movement is attributed to. A sede with no till
    -- which no profile builds, but a partially seeded database could -- leaves
    the column null rather than borrowing another sede's equipment.
    """
    from core.models import Device

    by_location: dict = {}
    for device in Device.objects.filter(tenant_id=tenant_id).order_by("label"):
        by_location.setdefault(device.location_id, device)
    return by_location


def build(context):
    """Write the stock, inside the pin the command already opened."""
    profile = context.profile
    rows = stock_plan(profile)
    if not rows:
        return

    tenant = Tenant.objects.get(id=context.tenant_id)
    options = inventory_settings.read(tenant)
    today = timezone.localdate()
    locations = {
        location.code: location
        for location in Location.objects.filter(tenant_id=context.tenant_id)
    }
    items = {
        f"{item.name}|{item.presentation}": item
        for item in Item.objects.filter(tenant_id=context.tenant_id)
    }
    actor = (
        User.objects.filter(tenant_id=context.tenant_id, role=Role.ADMIN).first()
        or User.objects.filter(tenant_id=context.tenant_id, role=Role.OWNER).first()
    )

    tills = _tills(context.tenant_id)
    lots = _write_lots(context, rows, items, options, today)
    _write_policies(context, rows, items, locations)
    effects, picks = _document_effects(rows, profile)
    _write_history(context, rows, items, locations, lots, effects, actor, today, tills)
    _write_transfers(context, picks, items, locations, lots, actor, tills)
    _write_counts(context, picks, items, locations, lots, actor, tills)


def _write_lots(context, rows, items, options, today):
    """One `lots` row per lot in the plan, its expiry taken from the tenant's own
    horizons and from today."""
    alert = int(options["expiry_alert_days"])
    notice = int(options["expiry_notice_days"])
    held = set(
        Lot.objects.filter(tenant_id=context.tenant_id).values_list("id", flat=True)
    )
    fresh: list[Lot] = []
    seen: dict[str, object] = {}
    for row in rows:
        if not row["lot_code"]:
            continue
        key = _lot_key(row)
        row_id = context.uid("lots", key)
        seen[key] = row_id
        if row_id in held or row_id in {one.id for one in fresh}:
            continue
        item = items.get(row["item_key"])
        if item is None:
            continue
        seed = stable_int("expiry", key)
        fresh.append(
            Lot(
                id=row_id,
                tenant_id=context.tenant_id,
                item=item,
                lot_code=row["lot_code"],
                expires_at=_expiry_date(row["expiry"], seed, today, alert, notice),
                unit_cost=_unit_cost(seed),
                supplier=None,
                invima_registration="",
            )
        )
    if fresh:
        Lot.objects.bulk_create(fresh, batch_size=1000)
    context.wrote("lots", len(seen))
    return seen


def _expiry_date(bucket, seed, today, alert, notice):
    """Every offset relative to the run date and to the tenant's own windows.

    A hard-coded date would turn a `Vence en 5 meses` row into a `Vencido` one
    six months from now, and a pilot that widened `expiry_alert_days` would find
    a tier empty. Neither is a fixture anybody would notice had gone wrong.
    """
    if bucket is None:
        return None
    if bucket == "expired":
        return today - timedelta(days=20 + seed % 160)
    if bucket == "urgent":
        # Inside the alert window, and spread across it so the valuation
        # horizon -- the 90 days the Panel's tile reads -- is populated too.
        return today + timedelta(days=10 + seed % max(1, alert - 20))
    if bucket == "notice":
        span = max(1, notice - alert - 20)
        return today + timedelta(days=alert + 10 + seed % span)
    return today + timedelta(days=notice + 60 + seed % 700)


def _unit_cost(seed):
    """What a unit cost to acquire. Every valuation in the product reads this
    and never a sale price."""
    return Decimal(1200 + seed % 38000).quantize(Decimal("0.01"))


def _write_policies(context, rows, items, locations):
    """`stock_policies` at `source = manual`, over most references.

    A deliberate minority carries none, so states 5 and 6 are provably
    unreachable without one, the bar is correctly absent where there is no
    denominator, and `Sin política definida` is a state somebody has seen.
    """
    held = set(
        StockPolicy.objects.filter(tenant_id=context.tenant_id).values_list(
            "id", flat=True
        )
    )
    fresh: list[StockPolicy] = []
    written = 0
    for row in rows:
        if not row["policy"]:
            continue
        written += 1
        row_id = context.uid("stock_policies", f"{row['sede']}|{row['item_key']}")
        if row_id in held:
            continue
        item = items.get(row["item_key"])
        location = locations.get(row["sede"])
        if item is None or location is None:
            continue
        fresh.append(
            StockPolicy(
                id=row_id,
                tenant_id=context.tenant_id,
                item=item,
                location=location,
                source=PolicySource.MANUAL,
                **row["policy"],
            )
        )
    if fresh:
        StockPolicy.objects.bulk_create(fresh, batch_size=1000)
    context.wrote("stock_policies", written)


#: What a shrinkage in the seeded history says happened. Three reasons rather
#: than one, so the record panel's history reads like a shop's and not like a
#: generator's.
SHRINKAGE_REASONS = ("damage", "theft", "loss")


def _write_history(context, rows, items, locations, lots, effects, actor, today, tills):
    """The moves behind every row, appended through the ledger service.

    Each row's history sums to its planned quantity **minus whatever the
    documents will later move**, so the projection lands exactly on the plan
    once the transfers and the counts have been applied.
    """
    del today
    now = timezone.now()
    batch = []
    written = 0
    for row in rows:
        item = items.get(row["item_key"])
        location = locations.get(row["sede"])
        if item is None or location is None:
            continue
        lot_id = lots.get(_lot_key(row)) if row["lot_code"] else None
        needed = row["quantity"] - effects.get(_key(row), 0)
        depth = _history_depth(row)
        seed = stable_int("history", row["item_key"], row["sede"])

        moves = _history(needed, depth, seed)
        clocks = _stamps(seed, len(moves), now)
        for ordinal, (quantity, kind, reason) in enumerate(moves):
            batch.append(
                ledger.Move(
                    id=context.uid("stock_moves", f"open:{_key(row)}:{ordinal}"),
                    location_id=location.id,
                    item_id=item.id,
                    lot_id=lot_id,
                    quantity=quantity,
                    type=kind,
                    reason=reason,
                    unit_cost=_unit_cost(stable_int("expiry", _lot_key(row)))
                    if quantity > 0
                    else None,
                    # Both clocks, and they are the same here: a shelf's own
                    # history is not an offline device catching up.
                    occurred_at=clocks[ordinal],
                    recorded_at=clocks[ordinal],
                    key=f"seed:open:{_key(row)}:{ordinal}",
                )
            )
            written += 1
        if len(batch) >= 400:
            _append(context, batch, actor, tills.get(location.id))
            batch = []
    if batch:
        _append(context, batch, actor, tills.get(location.id))
    context.wrote("stock_moves", written)


def _stamps(seed, depth, now):
    """One clock per move, oldest first and **strictly increasing**.

    Strictly, because equal timestamps put the tie-break back on `id` and the
    trace is sorted by `(recorded_at, id)` -- two moves at one instant is the
    whole defect this exists to close. The span is spread evenly and the start
    is jittered per row, so no two references share a history and the network's
    moves interleave the way a real week does.
    """
    oldest = timedelta(days=HISTORY_WINDOW_DAYS - seed % 90)
    newest = timedelta(days=HISTORY_SETTLES_DAYS + seed % 8)
    if depth <= 1:
        return [now - newest]
    span = (oldest - newest).total_seconds()
    return [
        now - oldest + timedelta(seconds=span * ordinal / (depth - 1))
        for ordinal in range(depth)
    ]


def _history(needed, depth, seed):
    """One row's movements, oldest first, summing to `needed`.

    A stockout row is received and then written off, because a reference that
    was never stocked has no projection row at all and would not appear on the
    grid -- `Quiebre` is a shelf that emptied, not a product nobody ever bought.
    """
    if depth >= DEEP_HISTORY_MOVES:
        # The lot that exercises the panel's fifty-move cap: a long run of small
        # receipts and write-offs that lands on the figure.
        moves = []
        running = 0
        for ordinal in range(depth - 1):
            step = 4 + (seed >> (ordinal % 32)) % 12
            if ordinal % 3 == 2 and running > step:
                moves.append((-step, StockMoveType.SHRINKAGE, "damage"))
                running -= step
            else:
                moves.append((step, StockMoveType.ADJUSTMENT, "standalone_receipt"))
                running += step
        moves.append(
            (needed - running, StockMoveType.ADJUSTMENT, "opening_stock")
            if needed - running > 0
            else (running - needed, StockMoveType.SHRINKAGE, "loss")
        )
        # The closing move's sign has to match its type, which the line above
        # gets right only when the two agree; normalise here rather than trust
        # the reader to check.
        last = moves[-1]
        if last[1] == StockMoveType.SHRINKAGE:
            moves[-1] = (-abs(last[0]), last[1], last[2])
        return [move for move in moves if move[0] != 0]

    if depth == 1:
        return [(needed, StockMoveType.ADJUSTMENT, "opening_stock")]

    extra = 5 + seed % 40
    opening = needed + extra
    if opening <= 0:
        return [(max(1, needed), StockMoveType.ADJUSTMENT, "opening_stock")]
    return [
        (opening, StockMoveType.ADJUSTMENT, "opening_stock"),
        (
            -extra,
            StockMoveType.SHRINKAGE,
            SHRINKAGE_REASONS[seed % len(SHRINKAGE_REASONS)],
        ),
    ]


def _append(context, batch, actor, device=None):
    """Every seeded move carries its document, its device, its user and both
    clocks -- which is what the record panel's history and the lot trace render,
    and what an INVIMA answer is made of. A device column empty on all five
    thousand rows is a trace nobody would accept."""
    ledger.append(
        batch,
        tenant_id=context.tenant_id,
        actor=actor,
        device=device,
        request_id="seed:inventory",
    )


def _write_transfers(context, picks, items, locations, lots, actor, tills):
    """Transfers in all four states, including a `partial` with units En
    tránsito, so the module's Traslados route renders as designed rather than on
    its empty state."""
    written_transfers = 0
    written_lines = 0
    for index, (status, dispatched, received) in enumerate(TRANSFER_PLAN):
        origin_row, destination_row = _transfer_ends(picks["transfers"], index)
        if origin_row is None or destination_row is None:
            continue
        origin = locations.get(origin_row["sede"])
        destination = locations.get(destination_row["sede"])
        item = items.get(origin_row["item_key"])
        if origin is None or destination is None or item is None:
            continue

        transfer_id = context.uid("transfers", str(index))
        transfer = Transfer.objects.filter(id=transfer_id).first()
        now = timezone.now()
        # **The move is stamped at the leg it belongs to**, not at the seed's
        # own instant: a `transfer_out` that reads as later than the receipt it
        # caused is a document nobody can follow, and both legs land after the
        # history that stocked the shelf (`HISTORY_SETTLES_DAYS`).
        left_at = now - timedelta(days=3)
        arrived_at = now - timedelta(days=1)
        if transfer is None:
            transfer = Transfer.objects.create(
                id=transfer_id,
                tenant_id=context.tenant_id,
                number=index + 1,
                origin_location=origin,
                destination_location=destination,
                status=status,
                note="",
                dispatched_at=left_at if dispatched else None,
                dispatched_by=actor if dispatched else None,
                dispatched_by_name=actor.name if actor and dispatched else "",
                received_at=arrived_at if received else None,
                received_by=actor if received else None,
                received_by_name=actor.name if actor and received else "",
            )
        written_transfers += 1

        line_id = context.uid("transfer_lines", str(index))
        if not TransferLine.objects.filter(id=line_id).exists():
            TransferLine.objects.create(
                id=line_id,
                tenant_id=context.tenant_id,
                transfer=transfer,
                item=item,
                lot_id=lots.get(_lot_key(origin_row))
                if origin_row["lot_code"]
                else None,
                quantity_requested=max(dispatched, 1),
                quantity_dispatched=dispatched,
                quantity_received=received,
                resolution="",
            )
        written_lines += 1

        moves = []
        if dispatched:
            moves.append(
                ledger.Move(
                    id=context.uid("stock_moves", f"tout:{index}"),
                    location_id=origin.id,
                    item_id=item.id,
                    lot_id=lots.get(_lot_key(origin_row))
                    if origin_row["lot_code"]
                    else None,
                    quantity=-dispatched,
                    type=StockMoveType.TRANSFER_OUT,
                    document_type="transfers",
                    document_id=transfer.id,
                    occurred_at=left_at,
                    recorded_at=left_at,
                    key=f"seed:tout:{index}",
                )
            )
        if received:
            moves.append(
                ledger.Move(
                    id=context.uid("stock_moves", f"tin:{index}"),
                    location_id=destination.id,
                    # The same reference as the outbound leg -- the pair is
                    # built on one `item_key`, and this reads it back rather
                    # than assuming it.
                    item_id=items[destination_row["item_key"]].id,
                    lot_id=lots.get(_lot_key(destination_row))
                    if destination_row["lot_code"]
                    else None,
                    quantity=received,
                    type=StockMoveType.TRANSFER_IN,
                    document_type="transfers",
                    document_id=transfer.id,
                    occurred_at=arrived_at,
                    recorded_at=arrived_at,
                    key=f"seed:tin:{index}",
                )
            )
        if moves:
            _append(context, moves, actor, tills.get(origin.id))
    context.wrote("transfers", written_transfers)
    context.wrote("transfer_lines", written_lines)


def _write_counts(context, picks, items, locations, lots, actor, tills):
    """One count open and one closed with differences, so Conteos renders as
    designed and the count arithmetic is visible on a real screen."""
    lines = picks["counts"]
    if not lines:
        return
    now = timezone.now()
    closed_at = now - timedelta(days=2)
    written_counts = 0
    written_lines = 0

    for name, status in (
        ("open", CountStatus.COUNTING),
        ("closed", CountStatus.CLOSED),
    ):
        location = locations.get(lines[0]["sede"])
        if location is None:
            continue
        count_id = context.uid("stock_counts", name)
        count = StockCount.objects.filter(id=count_id).first()
        if count is None:
            count = StockCount.objects.create(
                id=count_id,
                tenant_id=context.tenant_id,
                location=location,
                scope=CountScope.FULL,
                status=status,
                counted_by_user=actor,
                counted_by_name=actor.name if actor else "",
                closed_by_user=actor if status == CountStatus.CLOSED else None,
                closed_by_name=(
                    actor.name if actor and status == CountStatus.CLOSED else ""
                ),
                closed_at=closed_at if status == CountStatus.CLOSED else None,
                client_uuid=context.uid("stock_counts", f"{name}:client"),
                device=tills.get(location.id),
                occurred_at=closed_at,
                recorded_at=closed_at,
            )
        written_counts += 1

        moves = []
        for ordinal, row in enumerate(lines):
            item = items.get(row["item_key"])
            if item is None:
                continue
            difference = (
                COUNT_DIFFERENCES[ordinal - 1]
                if status == CountStatus.CLOSED
                and 1 <= ordinal <= len(COUNT_DIFFERENCES)
                else 0
            )
            expected = row["quantity"] - difference
            line_id = context.uid("stock_count_lines", f"{name}:{ordinal}")
            if not StockCountLine.objects.filter(id=line_id).exists():
                StockCountLine.objects.create(
                    id=line_id,
                    tenant_id=context.tenant_id,
                    count=count,
                    item=item,
                    lot_id=lots.get(_lot_key(row)) if row["lot_code"] else None,
                    expected_quantity=expected,
                    counted_quantity=expected + difference,
                    entered_at=closed_at,
                    client_uuid=context.uid(
                        "stock_count_lines", f"{name}:{ordinal}:client"
                    ),
                    device=tills.get(location.id),
                    occurred_at=closed_at,
                    recorded_at=closed_at,
                )
            written_lines += 1
            if status == CountStatus.CLOSED and difference:
                location_row = locations.get(row["sede"])
                if location_row is None:
                    continue
                moves.append(
                    ledger.Move(
                        id=context.uid("stock_moves", f"count:{ordinal - 1}"),
                        location_id=location_row.id,
                        item_id=item.id,
                        lot_id=lots.get(_lot_key(row)) if row["lot_code"] else None,
                        quantity=difference,
                        type=StockMoveType.COUNT,
                        reason="count_adjustment",
                        document_type="stock_counts",
                        document_id=count.id,
                        # The adjusting move happens when the count is closed,
                        # which is the moment the document already carries.
                        occurred_at=closed_at,
                        recorded_at=closed_at,
                        key=f"seed:count:{ordinal - 1}",
                    )
                )
        if moves:
            _append(context, moves, actor, tills.get(location.id))
    context.wrote("stock_counts", written_counts)
    context.wrote("stock_count_lines", written_lines)


register(
    "stock",
    tables=(
        "lots",
        "stock_moves",
        "stock_policies",
        "transfers",
        "transfer_lines",
        "stock_counts",
        "stock_count_lines",
    ),
    # `devices` too: every seeded move names the till it happened on, and S2's
    # fixture is what puts one at each sede.
    requires=("catalog", "devices"),
    build=build,
    owned_ids=owned_ids,
)
