"""The `Estado` derivation.

**`Estado` is not a column on any table.** It is computed per row from that
row's own quantity, its lot's expiry, its policy and -- when S6 exists -- its
forecast. design-system §B.7.4 fixes the seven states, their families, their
dots and their labels and calls itself their only definition; this module fixes
the rules, which it leaves open. **First match wins, top to bottom.**

It is computed in SQL rather than in Python because the screen it feeds is
server-paginated over 4.284 rows with a 400ms p95 budget (§4, §9): a state
computed on the page could not be sorted, could not be filtered and could not be
counted, and each of those three is a control the handoff draws.

**Why the precedence is in that order.** `expired` outranks everything because
units on a shelf past their date are a legal problem, not a supply problem.
`stockout` outranks expiry because finding the quiebres is what a droguería
opens this screen to do. **Expiry outranks the reorder point**, and the handoff
settles it: the Salbutamol row is at 7 units against a 4-day cover and still
renders `Vence en 6 meses` rather than `Punto de reorden`. A short-dated lot
changes what you buy, and a reorder badge that hides an expiry sends someone to
order more of something that will not sell before its date.
"""

from datetime import timedelta

from django.db.models import Case, F, IntegerField, OuterRef, Q, Subquery, Value, When

from core.models import StockPolicy

EXPIRED = "expired"
STOCKOUT = "stockout"
EXPIRING_URGENT = "expiring_urgent"
EXPIRING = "expiring"
REORDER_POINT = "reorder_point"
OVERSTOCK = "overstock"
SUFFICIENT = "sufficient"

#: The ordinal of each state, and the ordering the `Estado` column sorts by.
#: **Never alphabetical on the Spanish label**: that would place `Punto de
#: reorden` above `Quiebre`, which inverts the only ordering the column has and
#: sends someone to reorder a lot that will not sell before its date.
ORDINALS: dict[str, int] = {
    EXPIRED: 1,
    STOCKOUT: 2,
    EXPIRING_URGENT: 3,
    EXPIRING: 4,
    REORDER_POINT: 5,
    OVERSTOCK: 6,
    SUFFICIENT: 7,
}

STATES = tuple(ORDINALS)

BY_ORDINAL = {ordinal: state for state, ordinal in ORDINALS.items()}

#: `Estado · Requiere acción`, the chip the handoff draws active: expired,
#: quiebre, urgent expiry and reorder point.
#:
#: **Not `expiring`** -- a lot eleven months out is not a decision anyone takes
#: today -- and **not `overstock`**, which is capital and is Compras' screen.
#: These four are what the footer's `312 requieren acción` counts.
ACTION_STATES = (EXPIRED, STOCKOUT, EXPIRING_URGENT, REORDER_POINT)

#: What the `Vencimiento` chip offers, mapped to the valuation window and the
#: two alert horizons rather than to hand-typed numbers.
EXPIRY_FILTERS = ("expired", "valuation", "alert", "notice", "none")


def _policy(field):
    """One threshold, resolved per row.

    **A location-specific row wins over a network-wide one for the same item**,
    and it wins whole: `ORDER BY location_id DESC NULLS LAST` puts the sede's
    own row first, and the subquery takes one. Reading each column with its own
    `COALESCE` across the two rows would let a row inherit half of one policy
    and half of another, which is a threshold nobody set.
    """
    return Subquery(
        StockPolicy.objects.filter(
            tenant_id=OuterRef("tenant_id"), item_id=OuterRef("item_id")
        )
        .filter(Q(location_id=OuterRef("location_id")) | Q(location_id__isnull=True))
        .order_by(F("location_id").desc(nulls_last=True))
        .values(field)[:1]
    )


def with_policy(queryset):
    """Annotate the thresholds every state after the third reads."""
    return queryset.annotate(
        policy_min_quantity=_policy("min_quantity"),
        policy_max_quantity=_policy("max_quantity"),
        policy_reorder_point=_policy("reorder_point"),
        policy_coverage_days=_policy("target_coverage_days"),
        policy_source=_policy("source"),
    )


def annotate(queryset, *, today, alert_days, notice_days):
    """Add `state_ordinal` to a `stock_on_hand` queryset.

    **The ordinal is the state**, and the name is read off it in Python. One
    annotation rather than two: the ordinal is what the column sorts by, what
    the chip filters on and what the summary groups by, and a parallel text
    column would be a second encoding of the same rule that could drift from it.

    The two horizons are the tenant's own `inventory` settings, passed in rather
    than read here: a derivation that read its own settings would be a second
    read per query on a path with a 400ms budget, and a horizon written into a
    module is a horizon a pilot cannot move.
    """
    alert = today + timedelta(days=int(alert_days))
    notice = today + timedelta(days=int(notice_days))

    ordinal = Case(
        # 1 · past its date with units still on the shelf. A legal problem.
        When(Q(lot__expires_at__lt=today) & Q(quantity__gt=0), then=Value(1)),
        # 2 · nothing on the shelf. What a droguería opens this screen to find.
        When(Q(quantity__lte=0), then=Value(2)),
        # 3 and 4 · the two expiry windows. Both are reachable only on a row
        # that is neither expired nor at zero, which is what makes the bare
        # `<=` correct without a lower bound.
        When(Q(lot__expires_at__lte=alert), then=Value(3)),
        When(Q(lot__expires_at__lte=notice), then=Value(4)),
        # 5 and 6 · **a row with no policy cannot reach either**, because
        # `quantity <= NULL` is unknown and never true. The record panel says
        # `Sin política definida`, so a `Suficiente` badge on an unmanaged
        # reference is never mistaken for a judgement about it.
        When(
            Q(policy_reorder_point__isnull=False)
            & Q(quantity__lte=F("policy_reorder_point")),
            then=Value(5),
        ),
        When(
            Q(policy_max_quantity__isnull=False)
            & Q(quantity__gt=F("policy_max_quantity")),
            then=Value(6),
        ),
        default=Value(7),
        output_field=IntegerField(),
    )

    return with_policy(queryset).annotate(state_ordinal=ordinal)


def name_of(ordinal) -> str:
    """The state one ordinal stands for."""
    return BY_ORDINAL.get(int(ordinal), SUFFICIENT)


def expiry_window(kind, *, today, options):
    """The `Vencimiento` chip's four windows and its fifth answer.

    Each maps to a horizon the tenant configured rather than to a hand-typed
    number, so a pilot that moves a window moves the chip with it.
    """
    if kind == "expired":
        return Q(lot__expires_at__lt=today)
    if kind == "none":
        return Q(lot__expires_at__isnull=True)
    days = {
        "valuation": options["expiry_valuation_days"],
        "alert": options["expiry_alert_days"],
        "notice": options["expiry_notice_days"],
    }.get(kind)
    if days is None:
        return Q()
    return Q(
        lot__expires_at__gte=today,
        lot__expires_at__lte=today + timedelta(days=int(days)),
    )


def bar_percentage(quantity, capacity):
    """The in-cell bar's fill (§A.18.1, §B.12.3).

    `None` where no policy gives the row a capacity: **a bar with no denominator
    behind it is a bar measuring nothing**, so the figure stands alone instead.
    A zero draws no fill at all -- the track alone is the zero state, which
    overrules the prototype's 4% sliver, and the row already says `Quiebre`.
    """
    if capacity is None or capacity <= 0:
        return None
    return max(0, min(100, round(quantity * 100 / capacity)))
