"""S7's endpoints, on the router `core.api` mounts.

Every path carries the `/api/` prefix and is English (§3), runs behind S0's
single permission dependency (§2) inside the pinned transaction (A1), honours
the grid contract (§9), and appends to `audit_log` through S0's path on every
elevated-role mutation (ledger).

**Read this module for what is missing.** There is no approve, no reject, no
override, no apply, no revert and no batch. **Not one route here writes
`item_prices`** -- the word does not appear in a write position anywhere below
this line -- which is the difference A11 draws between human approval as a
*policy*, which is a setting somebody can change, and a model with no write
path, which is a property of the schema and the grants.

`owner` and `admin` both read every surface and both may load caps; only an
`owner` triggers a run or writes the settings group. **Nobody approves anything,
at any role**, because this surface has nothing to approve: §2's price-approval
grant is exercised in S1's price editor, on a row that carries the person's name.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.middleware import request_id
from core.models import (
    AuditAction,
    CapStatus,
    Category,
    Confidence,
    ElasticityEstimate,
    Item,
    ItemPrice,
    LIVE_PROPOSAL_STATUSES,
    Manufacturer,
    PriceProposal,
    PriceProposalStatus,
    ProposalBasis,
    RESOLVED_PROPOSAL_STATUSES,
    Tenant,
)
from core.permissions import owner_only, owner_or_admin
from core.pricing import caps as cap_service, engine, estimator, jobs, reasons
from core.pricing import settings as pricing_settings

router = Router()

SortOrder = Literal["asc", "desc"]
BasisValue = Literal["margin_rule", "elasticity"]
ConfidenceValue = Literal["high", "medium", "low"]
CapStatusValue = Literal["capped", "not_regulated", "unknown"]
#: The six stored states and the two derived ones. `unevaluated` and
#: `no_proposal` are **derived**, in the sense §B.7.4 gives the stock state:
#: they are not `price_proposal_status` values and never reach the database.
RowStateValue = Literal[
    "unevaluated",
    "no_proposal",
    "proposed",
    "above_cap",
    "taken",
    "modified",
    "dismissed",
    "superseded",
]

#: What the `Estado` chip may ask for, which is the eight above **plus `live`**
#: -- the screen's own default, `Estado · Con propuesta` (§B.8.4·1). It means
#: *this reference has something live to say*, and it admits the compliance
#: finding: an `above_cap` row is the one a person most needs to see, and having
#: it behind a filter would be the opposite of what the tile beside it is for.
#:
#: It is a **filter** value and never a row's state, so it is deliberately not
#: on `PricingRow.state`: a response type that advertised a value the endpoint
#: never returns is a type that lies to its own client.
RowStateFilterValue = Literal[
    "live",
    "unevaluated",
    "no_proposal",
    "proposed",
    "above_cap",
    "taken",
    "modified",
    "dismissed",
    "superseded",
]

#: What the cost basis must move by before a suggestion's projected margin is
#: no longer the one the screen is showing.
STALE_COST_SHARE = Decimal("0.05")

GRID_SORTABLE = {
    "name": ["name"],
    "manufacturer": ["manufacturer__name", "name"],
}


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class PricingStaleOut(Schema):
    """Why a suggestion's figures are no longer about the price on the shelf.

    **A rendering rule rather than a refusal.** A run computed on Monday is read
    on Friday against a cost and a price that may both have moved, and
    `item_prices` is a table this stage no longer writes at all -- so the check
    moved to where it always belonged: read time, on screen, before anybody
    types anything.
    """

    reason: Literal["price_moved", "cost_moved", "cap_binds"]
    detail: str


class PricingProposalOut(Schema):
    id: uuid.UUID
    basis: BasisValue
    status: str
    suggested_price: Decimal
    current_price_at_proposal: Decimal
    current_margin: Decimal | None
    projected_margin: Decimal | None
    estimated_monthly_impact: Decimal | None
    trailing_monthly_units: int | None
    margin_gap_pp: Decimal | None
    step_pct: Decimal
    confidence: ConfidenceValue
    respects_regulated_cap: bool
    regulated_max_price_at_proposal: Decimal | None
    reason_code: str
    reason: str
    resolved_price: Decimal | None
    resolved_by_name: str
    resolved_at: datetime | None
    computed_at: datetime
    stale: PricingStaleOut | None


class PricingEstimateOut(Schema):
    status: str
    elasticity: Decimal | None
    r2: Decimal | None
    observations: int
    distinct_prices: int
    price_dispersion: Decimal | None
    std_error: Decimal | None
    ci_low: Decimal | None
    ci_high: Decimal | None
    weeks_excluded_stockout: int | None
    weeks_excluded_promo: int | None
    imported_share: Decimal | None
    confidence: str
    window: str
    model_version: str
    computed_at: datetime
    reason: str


class PricingRow(Schema):
    item_id: uuid.UUID
    name: str
    presentation: str
    manufacturer_name: str | None
    category_name: str | None
    unit: str
    active: bool
    cost_basis: Decimal | None
    cost_source: str | None
    current_price: Decimal | None
    current_margin: Decimal | None
    margin_gap_pp: Decimal | None
    regulated_max_price: Decimal | None
    cap_status: CapStatusValue
    #: The derived row state the `Estado` badge renders. `unevaluated` and
    #: `no_proposal` are two different readings and conflating them is the
    #: defect this whole stage is about.
    state: RowStateValue
    #: One sentence, composed from a code and this row's own figures. There is
    #: no free text and no generated prose on this surface.
    reason: str
    basis: BasisValue | None
    confidence: ConfidenceValue | None
    elasticity: Decimal | None
    observations: int
    proposal: PricingProposalOut | None


class PricingPriceRow(Schema):
    """One row of the item's price history, as the panel renders it."""

    id: uuid.UUID
    price: Decimal
    effective_from: date
    effective_to: date | None
    source: str
    location_name: str | None
    set_by_name: str
    proposal_id: uuid.UUID | None


class PricingLocationEstimate(Schema):
    """A per-sede fit, where that sede cleared every floor on its own. **It
    flags heterogeneity and never prices a sede differently**, which v1 cannot
    express: `price_proposals` carries no `location_id`."""

    location_id: uuid.UUID
    location_name: str
    elasticity: Decimal | None
    r2: Decimal | None
    observations: int
    confidence: str
    status: str
    reason: str


class PricingDetail(Schema):
    row: PricingRow
    estimate: PricingEstimateOut | None
    proposal: PricingProposalOut | None
    #: The per-sede estimates, where any sede cleared every floor on its own.
    #: They flag heterogeneity and **never price a sede differently**, which v1
    #: cannot express: `price_proposals` carries no `location_id`.
    location_estimates: list[PricingLocationEstimate]
    prices: list[PricingPriceRow]
    history: list[PricingProposalOut]
    cap_source: str


class PricingSummaryOut(Schema):
    """The four KPI tiles, plus the one figure that answers *how much of this
    screen is evidence yet*."""

    references_with_proposal: int
    estimated_monthly_impact: Decimal
    #: The units-weighted projected margin across the references with a live
    #: suggestion, against the goal it is measured on. Null where no goal is
    #: set, which is the only tile that changes in that state.
    projected_margin: Decimal | None
    current_margin: Decimal | None
    margin_goal_pct: Decimal | None
    above_cap: int
    evaluated: int
    estimable: int
    by_status: dict[str, int]
    by_reason: dict[str, int]
    by_basis: dict[str, int]
    computed_at: datetime | None
    model_version: str
    window: str
    #: **On the screen while it is on**, as one standing line in the filter bar
    #: -- stated once on the surface where it has effect, not as a banner on
    #: every screen (§B.9.2's rule against screen-level banners applies to the
    #: same instinct).
    allow_raise_without_cap: bool


class PricingAdoptionBasisOut(Schema):
    basis: BasisValue
    taken: int
    modified: int
    dismissed: int
    superseded: int
    #: The denominator beside every share -- a 100% take rate on three
    #: suggestions is not a finding.
    resolved: int
    proposed_ever: int
    #: `(resolved_price − suggested_price) / suggested_price`, in points. A gap
    #: near zero with a high `modified` share is an engine that is trusted and
    #: rounded; a large negative gap is one people are correcting downward,
    #: which should stop a rollout rather than tune a constant.
    median_signed_gap_pct: Decimal | None
    #: Of the dismissals, how many had their reference repriced by hand anyway
    #: inside the following month -- which is what tells *wrong* from *not now*.
    dismissed_then_repriced: int


class PricingAdoptionOut(Schema):
    since: date
    until: date
    by_basis: list[PricingAdoptionBasisOut]


class PricingRunOut(Schema):
    computed_at: datetime
    model_version: str
    estimates: int
    proposals: int
    by_basis: dict[str, int]


class PricingRunEnqueuedOut(Schema):
    queued: bool
    detail: str


class PricingCapOut(Schema):
    item_id: uuid.UUID
    name: str
    presentation: str
    regulated_max_price: Decimal | None
    cap_status: CapStatusValue
    source: str
    #: When the cap was last set, which is the other half of *with their source
    #: reference and date*: a ceiling from a circular three years old and one
    #: loaded this morning are not the same claim.
    set_at: datetime | None
    current_price: Decimal | None
    above_cap: bool


class PricingCapIn(Schema):
    regulated_max_price: Decimal | None = None
    cap_status: CapStatusValue
    source: str = ""


class PricingCapImportIn(Schema):
    csv: str


class PricingCapImportOut(Schema):
    loaded: int
    unmatched: list[dict]
    refused: list[dict]


class PricingSettingsOut(Schema):
    #: **Null until an owner sets it**, and every consumer renders its absence
    #: rather than a fallback number.
    margin_goal_pct: float | None
    max_single_step_pct: float
    allow_raise_without_cap: bool
    min_days_between_changes: int
    rounding_unit: int


class PricingSettingsIn(Schema):
    margin_goal_pct: float | None = None
    max_single_step_pct: float | None = None
    allow_raise_without_cap: bool | None = None
    min_days_between_changes: int | None = None
    rounding_unit: int | None = None
    #: Clearing the goal is a legal write and is how a tenant returns to the
    #: first-morning state, so it needs a way to say *null* that
    #: `exclude_unset` cannot confuse with *not sent*.
    clear_margin_goal: bool = False


# ---------------------------------------------------------------------------
# Reading the latest run
# ---------------------------------------------------------------------------


def _tenant(request) -> Tenant:
    return get_object_or_404(Tenant, id=request.tenant_id)


def latest_run(tenant_id):
    """The highest `(computed_at, model_version)` on the rows a run wrote.

    **There is no run table.** The ledger assigns S7 two tables and no more, and
    a run is identified by what it produced.
    """
    row = (
        ElasticityEstimate.objects.filter(tenant_id=tenant_id)
        .order_by("-computed_at")
        .values("computed_at", "model_version")
        .first()
    )
    return (row["computed_at"], row["model_version"]) if row else (None, "")


def _latest_estimates(tenant_id, computed_at, item_ids=None):
    queryset = ElasticityEstimate.objects.filter(
        tenant_id=tenant_id, computed_at=computed_at, location__isnull=True
    )
    if item_ids is not None:
        queryset = queryset.filter(item_id__in=item_ids)
    return {one.item_id: one for one in queryset}


def _live_and_resolved(tenant_id, item_ids):
    """One live proposal per reference, plus the most recent resolved one.

    The live row is what the `Sugerido` column shows; the resolved one is what
    it shows *instead*, with the person's number and the suggestion beneath it.
    Both are read here so the grid makes one query for each rather than one per
    row.
    """
    live: dict = {}
    resolved: dict = {}
    for one in PriceProposal.objects.filter(
        tenant_id=tenant_id, item_id__in=item_ids
    ).order_by("-computed_at", "-resolved_at"):
        if one.status in LIVE_PROPOSAL_STATUSES:
            live.setdefault(one.item_id, one)
        elif one.status in RESOLVED_PROPOSAL_STATUSES:
            resolved.setdefault(one.item_id, one)
    return live, resolved


def _staleness(proposal, *, price, cost, item, options) -> dict | None:
    """Whether this suggestion is still arithmetic about the price on the shelf.

    Strictly safer than the batch refusal it replaces, and strictly less
    irritating: nothing is all-or-nothing any more, because nothing is a batch.
    """
    if proposal is None or proposal.status not in LIVE_PROPOSAL_STATUSES:
        return None
    if price is not None and Decimal(price) != Decimal(proposal.current_price):
        return {
            "reason": "price_moved",
            "detail": (
                f"El precio cambió de {reasons.pesos(proposal.current_price)} a "
                f"{reasons.pesos(price)} después del cálculo."
            ),
        }
    if cost is not None and proposal.cost_basis:
        moved = abs(Decimal(cost) - proposal.cost_basis) / proposal.cost_basis
        if moved > STALE_COST_SHARE:
            return {
                "reason": "cost_moved",
                "detail": (
                    f"El costo se movió {reasons.percent(moved * 100)} desde el "
                    f"cálculo: {reasons.pesos(proposal.cost_basis)} → "
                    f"{reasons.pesos(cost)}."
                ),
            }
    cap = item.regulated_max_price
    if cap is not None and Decimal(proposal.suggested_price) > cap:
        return {
            "reason": "cap_binds",
            "detail": (
                f"Ahora hay un tope regulado de {reasons.pesos(cap)}, por debajo "
                "de la propuesta."
            ),
        }
    # **`cap_status` moved to `unknown`**, which is the other half of the rule
    # and does not always leave a cap price behind: a reference stated to be
    # outside price control carries no ceiling either, so a `not_regulated →
    # unknown` edit changes nothing about `regulated_max_price` and everything
    # about whether the engine would still raise it. `respects_regulated_cap`
    # is what distinguishes the two: it was true when the cap was known.
    if (
        cap is None
        and (item.cap_status or CapStatus.UNKNOWN) == CapStatus.UNKNOWN
        and proposal.respects_regulated_cap
        and not options["allow_raise_without_cap"]
        and Decimal(proposal.suggested_price) > Decimal(proposal.current_price)
    ):
        return {
            "reason": "cap_binds",
            "detail": (
                "Ya no se conoce el tope regulado de esta referencia y las alzas "
                "sin tope están desactivadas."
            ),
        }
    return None


def _proposal_out(proposal, *, estimate=None, goal=None, stale=None) -> dict:
    return {
        "id": proposal.id,
        "basis": proposal.basis,
        "status": proposal.status,
        "suggested_price": proposal.suggested_price,
        "current_price_at_proposal": proposal.current_price,
        "current_margin": proposal.current_margin,
        "projected_margin": proposal.projected_margin,
        "estimated_monthly_impact": proposal.estimated_monthly_impact,
        "trailing_monthly_units": proposal.trailing_monthly_units,
        "margin_gap_pp": proposal.margin_gap_pp,
        "step_pct": proposal.step_pct,
        "confidence": proposal.confidence,
        "respects_regulated_cap": proposal.respects_regulated_cap,
        "regulated_max_price_at_proposal": proposal.regulated_max_price_at_proposal,
        "reason_code": proposal.reason_code,
        "reason": reasons.proposal_sentence(proposal, estimate=estimate, goal=goal),
        "resolved_price": proposal.resolved_price,
        "resolved_by_name": proposal.resolved_by_name,
        "resolved_at": proposal.resolved_at,
        "computed_at": proposal.computed_at,
        "stale": stale,
    }


def _row_state(*, item, price, cost, estimate, live, resolved):
    """The `Estado` badge, and the two derived states are the point of it.

    `Sin evaluar` is *nothing could look at this*; `Sin propuesta` is *both
    engines looked and there is nothing to say*. A build that shows the same
    badge for both fails the criterion this whole surface is written around.
    """
    if live is not None:
        return live.status
    if resolved is not None:
        return resolved.status
    if not item.active or cost is None or price is None:
        return "unevaluated"
    if estimate is not None and estimate.status in ("no_cost", "inactive"):
        return "unevaluated"
    return "no_proposal"


def _row_reason(*, state, estimate, live, resolved, goal, figures) -> str:
    if live is not None:
        return reasons.proposal_sentence(live, estimate=estimate, goal=goal)
    if resolved is not None:
        return _resolution_sentence(resolved)
    if state == "unevaluated":
        return reasons.elasticity_sentence(estimate) if estimate else ""
    if estimate is not None and estimate.no_proposal_reason:
        return reasons.no_proposal_sentence(
            estimate.no_proposal_reason, proposal_figures=figures
        )
    return reasons.elasticity_sentence(estimate) if estimate else ""


def _resolution_sentence(proposal) -> str:
    """What happened to a suggestion, from its status rather than a code.

    The name comes from `resolved_by_name` and the price from `resolved_price`,
    both written by S1 -- this screen reports them and can change neither.
    """
    when = reasons.day(
        timezone.localtime(proposal.resolved_at).date()
        if proposal.resolved_at
        else None
    )
    who = proposal.resolved_by_name or "alguien del equipo"
    if proposal.status == PriceProposalStatus.TAKEN:
        return (
            f"Tomada el {when} por {who} · "
            f"{reasons.pesos(proposal.resolved_price)}, el precio sugerido."
        )
    if proposal.status == PriceProposalStatus.MODIFIED:
        gap = (
            (Decimal(proposal.resolved_price) - Decimal(proposal.suggested_price))
            / Decimal(proposal.suggested_price)
            * Decimal("100")
        )
        signed = f"+{reasons.percent(gap)}" if gap >= 0 else reasons.percent(gap)
        return (
            f"Ajustada el {when} por {who} · "
            f"{reasons.pesos(proposal.resolved_price)} frente a "
            f"{reasons.pesos(proposal.suggested_price)} sugeridos ({signed})."
        )
    if proposal.status == PriceProposalStatus.DISMISSED:
        return f"Descartada el {when} por {who}."
    return "Reemplazada por un cálculo más reciente."


# ---------------------------------------------------------------------------
# The Precios grid
# ---------------------------------------------------------------------------


@router.get("/pricing/items", response=Page[PricingRow], auth=owner_or_admin)
def list_pricing_items(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
    q: str | None = Query(None),
    manufacturer_id: uuid.UUID | None = Query(None),
    category_id: uuid.UUID | None = Query(None),
    state: RowStateFilterValue | None = Query(None),
    basis: BasisValue | None = Query(None),
    confidence: ConfidenceValue | None = Query(None),
):
    """The Precios grid, one row per catalog item.

    **Every current figure is recomputed on this read** -- the price, the cost
    basis and the cap -- which is what lets a suggestion be marked *stale* on
    screen instead of a transaction refusing it later. `Estado · Con propuesta`
    is the default view; a reference with nothing to say is still in the grid,
    with a badge distinguishing *nothing could evaluate this* from *both engines
    looked and there is nothing to do*.
    """
    tenant = _tenant(request)
    options = pricing_settings.read(tenant)
    goal = pricing_settings.goal(options)
    today = timezone.localdate()
    computed_at, _version = latest_run(tenant.id)

    items = Item.objects.filter(tenant_id=tenant.id).select_related(
        "manufacturer", "category"
    )
    if q:
        items = items.filter(
            Q(search_name__contains=q.strip().lower()) | Q(name__icontains=q.strip())
        )
    if manufacturer_id:
        items = items.filter(manufacturer_id=manufacturer_id)
    if category_id:
        items = items.filter(category_id=category_id)

    # The three filters below are read off this run's own rows, so they narrow
    # the item set in the database rather than after pagination -- a filter
    # applied to a page is a filter that reports the wrong `rowCount`.
    if basis or confidence or state:
        items = items.filter(
            id__in=_matching_ids(tenant.id, computed_at, state, basis, confidence)
        )

    rows, row_count, page, page_size = paginate(
        items,
        page=page,
        page_size=page_size,
        sort=sort or "name",
        order=order,
        sortable=GRID_SORTABLE,
    )
    return {
        "rows": _rows_for(
            tenant.id,
            rows,
            options=options,
            goal=goal,
            today=today,
            computed_at=computed_at,
        ),
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _matching_ids(tenant_id, computed_at, state, basis, confidence):
    """The item ids a `Base`, `Confianza` or `Estado` filter admits."""
    if state in ("unevaluated", "no_proposal"):
        estimates = ElasticityEstimate.objects.filter(
            tenant_id=tenant_id, computed_at=computed_at, location__isnull=True
        )
        if state == "unevaluated":
            estimates = estimates.filter(status__in=("no_cost", "inactive"))
        else:
            estimates = estimates.exclude(status__in=("no_cost", "inactive"))
        ids = set(estimates.values_list("item_id", flat=True))
        with_proposal = set(
            PriceProposal.objects.filter(tenant_id=tenant_id)
            .exclude(status=PriceProposalStatus.SUPERSEDED)
            .values_list("item_id", flat=True)
        )
        return ids - with_proposal if state == "no_proposal" else ids

    proposals = PriceProposal.objects.filter(tenant_id=tenant_id)
    if state and state != "live":
        proposals = proposals.filter(status=state)
    else:
        proposals = proposals.filter(status__in=LIVE_PROPOSAL_STATUSES)
    if basis or confidence:
        # **A compliance finding has no engine and no confidence.** It carries
        # `basis = margin_rule` only because the column is not nullable, so
        # letting `Base · Margen` return it would make the chip's count and the
        # provenance line's count disagree by however many references are over
        # their cap -- and would put a row with no suggestion under a filter
        # that means *suggested by the margin rule*.
        proposals = proposals.exclude(status=PriceProposalStatus.ABOVE_CAP)
    if basis:
        proposals = proposals.filter(basis=basis)
    if confidence:
        proposals = proposals.filter(confidence=confidence)
    return set(proposals.values_list("item_id", flat=True))


def _rows_for(tenant_id, items, *, options, goal, today, computed_at):
    ids = [one.id for one in items]
    estimates = _latest_estimates(tenant_id, computed_at, ids) if computed_at else {}
    live, resolved = _live_and_resolved(tenant_id, ids)
    # **Every current figure is recomputed on this read** -- and narrowed to the
    # page, because the whole point of doing it here is that it is cheap enough
    # to do on every read (§4's 400ms).
    prices = engine.current_prices(tenant_id, today, ids)
    costs = engine.cost_bases(tenant_id, ids)
    changes = engine.last_price_changes(tenant_id, today, ids)

    out = []
    for item in items:
        price = prices.get(item.id)
        cost, cost_source = costs.get(item.id, (None, None))
        estimate = estimates.get(item.id)
        one_live = live.get(item.id)
        one_resolved = resolved.get(item.id) if one_live is None else None
        margin = engine.margin_pct(price, cost, item.vat_class) if price else None
        state = _row_state(
            item=item,
            price=price,
            cost=cost,
            estimate=estimate,
            live=one_live,
            resolved=one_resolved,
        )
        stale = _staleness(one_live, price=price, cost=cost, item=item, options=options)
        figures = _figures(
            estimate,
            one_live,
            margin,
            goal,
            item,
            today,
            options,
            changes.get(item.id),
        )
        shown = one_live or one_resolved
        out.append(
            {
                "item_id": item.id,
                "name": item.name,
                "presentation": item.presentation,
                "manufacturer_name": (
                    item.manufacturer.name if item.manufacturer_id else None
                ),
                "category_name": item.category.name if item.category_id else None,
                "unit": item.unit,
                "active": item.active,
                "cost_basis": cost,
                "cost_source": cost_source,
                "current_price": price,
                "current_margin": margin,
                "margin_gap_pp": (
                    (goal - margin).quantize(Decimal("0.01"))
                    if goal is not None and margin is not None and margin < goal
                    else None
                ),
                "regulated_max_price": item.regulated_max_price,
                "cap_status": item.cap_status or CapStatus.UNKNOWN,
                "state": state,
                "reason": _row_reason(
                    state=state,
                    estimate=estimate,
                    live=one_live,
                    resolved=one_resolved,
                    goal=goal,
                    figures=figures,
                ),
                "basis": shown.basis if shown else None,
                "confidence": shown.confidence if shown else None,
                "elasticity": estimate.elasticity if estimate else None,
                "observations": estimate.observations if estimate else 0,
                "proposal": (
                    _proposal_out(shown, estimate=estimate, goal=goal, stale=stale)
                    if shown
                    else None
                ),
            }
        )
    return out


def _figures(estimate, live, margin, goal, item, today, options, changed):
    """Whatever the engine computed on the way to deciding not to suggest
    anything, so the sentence names a number rather than a category."""
    figures = {
        "margin": margin,
        "goal": goal,
        "cap": item.regulated_max_price,
    }
    if estimate is not None:
        figures["r2"] = estimate.r2
        figures["elasticity"] = estimate.elasticity
    if live is not None:
        figures["impact"] = live.estimated_monthly_impact
    if changed is not None:
        cooldown = int(options["min_days_between_changes"])
        figures["days_since"] = (today - changed).days
        figures["eligible_on"] = changed + timedelta(days=cooldown)
    return figures


# ---------------------------------------------------------------------------
# The record panel
# ---------------------------------------------------------------------------


@router.get("/pricing/items/{item_id}", response=PricingDetail, auth=owner_or_admin)
def read_pricing_item(request, item_id: uuid.UUID):
    """One reference: the estimate, the suggestion's arithmetic, the cap and its
    source, the price history with each row's author, and **every past
    suggestion beside what the person chose against it**.

    That last block is the measurement in its single-reference form -- where an
    owner arguing with the model gets to check whether the model has been right
    before.
    """
    tenant = _tenant(request)
    item = get_object_or_404(
        Item.objects.select_related("manufacturer", "category"),
        id=item_id,
        tenant_id=tenant.id,
    )
    options = pricing_settings.read(tenant)
    goal = pricing_settings.goal(options)
    today = timezone.localdate()
    computed_at, _version = latest_run(tenant.id)
    row = _rows_for(
        tenant.id,
        [item],
        options=options,
        goal=goal,
        today=today,
        computed_at=computed_at,
    )[0]

    estimate = (
        _latest_estimates(tenant.id, computed_at, [item.id]).get(item.id)
        if computed_at
        else None
    )
    per_sede = ElasticityEstimate.objects.filter(
        tenant_id=tenant.id,
        item_id=item.id,
        computed_at=computed_at,
        location__isnull=False,
    ).select_related("location")
    history = (
        PriceProposal.objects.filter(tenant_id=tenant.id, item_id=item.id)
        .exclude(status__in=LIVE_PROPOSAL_STATUSES)
        .order_by("-computed_at")[:20]
    )
    prices = (
        ItemPrice.objects.filter(tenant_id=tenant.id, item_id=item.id)
        .select_related("location")
        .order_by("-effective_from", "-created_at")[:20]
    )
    pricing_custom = (item.custom or {}).get("pricing") or {}
    return {
        "row": row,
        "estimate": _estimate_out(estimate) if estimate else None,
        "proposal": row["proposal"],
        "location_estimates": [
            {
                "location_id": one.location_id,
                "location_name": one.location.name if one.location else "",
                "elasticity": one.elasticity,
                "r2": one.r2,
                "observations": one.observations,
                "confidence": one.confidence,
                "status": one.status,
                "reason": reasons.elasticity_sentence(one),
            }
            for one in per_sede
        ],
        "prices": [
            {
                "id": one.id,
                "price": one.price,
                "effective_from": one.effective_from,
                "effective_to": one.effective_to,
                "source": one.source,
                "location_name": one.location.name if one.location else None,
                "set_by_name": one.set_by_name,
                "proposal_id": one.proposal_id,
            }
            for one in prices
        ],
        "history": [
            _proposal_out(one, estimate=estimate, goal=goal) for one in history
        ],
        "cap_source": pricing_custom.get("cap_source", ""),
    }


def _estimate_out(estimate) -> dict:
    return {
        "status": estimate.status,
        "elasticity": estimate.elasticity,
        "r2": estimate.r2,
        "observations": estimate.observations,
        "distinct_prices": estimate.distinct_prices,
        "price_dispersion": estimate.price_dispersion,
        "std_error": estimate.std_error,
        "ci_low": estimate.ci_low,
        "ci_high": estimate.ci_high,
        "weeks_excluded_stockout": estimate.weeks_excluded_stockout,
        "weeks_excluded_promo": estimate.weeks_excluded_promo,
        "imported_share": estimate.imported_share,
        "confidence": estimate.confidence,
        "window": estimate.window,
        "model_version": estimate.model_version,
        "computed_at": estimate.computed_at,
        "reason": reasons.elasticity_sentence(estimate),
    }


# ---------------------------------------------------------------------------
# The four tiles, and the run's own reach
# ---------------------------------------------------------------------------


@router.get("/pricing/summary", response=PricingSummaryOut, auth=owner_or_admin)
def pricing_summary(request):
    """The four KPI tiles, the run's evaluated / estimable counts by reason, and
    **the suggestion count by `basis`** -- the one figure that answers *how much
    of this screen is evidence yet*."""
    tenant = _tenant(request)
    options = pricing_settings.read(tenant)
    goal = pricing_settings.goal(options)
    computed_at, version = latest_run(tenant.id)

    live = list(
        PriceProposal.objects.filter(
            tenant_id=tenant.id, status__in=LIVE_PROPOSAL_STATUSES
        )
    )
    with_proposal = [one for one in live if one.status == PriceProposalStatus.PROPOSED]
    above = [one for one in live if one.status == PriceProposalStatus.ABOVE_CAP]
    impact = sum(
        (one.estimated_monthly_impact or Decimal("0")) for one in with_proposal
    )

    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    estimable = 0
    evaluated = 0
    if computed_at:
        for status, reason, count in _run_counts(tenant.id, computed_at):
            evaluated += count
            by_status[status] = by_status.get(status, 0) + count
            if reason:
                by_reason[reason] = by_reason.get(reason, 0) + count
            if status == "estimated":
                estimable += count

    by_basis = {
        ProposalBasis.MARGIN_RULE: sum(
            1 for one in with_proposal if one.basis == ProposalBasis.MARGIN_RULE
        ),
        ProposalBasis.ELASTICITY: sum(
            1 for one in with_proposal if one.basis == ProposalBasis.ELASTICITY
        ),
    }
    return {
        "references_with_proposal": len(with_proposal),
        "estimated_monthly_impact": impact,
        "projected_margin": _weighted(with_proposal, "projected_margin"),
        "current_margin": _weighted(with_proposal, "current_margin"),
        "margin_goal_pct": goal,
        "above_cap": len(above),
        "evaluated": evaluated,
        "estimable": estimable,
        "by_status": by_status,
        "by_reason": by_reason,
        "by_basis": by_basis,
        "computed_at": computed_at,
        "model_version": version,
        "window": estimator.WINDOW_LABEL,
        "allow_raise_without_cap": bool(options["allow_raise_without_cap"]),
    }


def _run_counts(tenant_id, computed_at):
    from django.db.models import Count

    return (
        ElasticityEstimate.objects.filter(
            tenant_id=tenant_id, computed_at=computed_at, location__isnull=True
        )
        .values_list("status", "no_proposal_reason")
        .annotate(total=Count("id"))
    )


def _weighted(proposals, field) -> Decimal | None:
    """The margin across the references with a live suggestion, weighted by the
    trailing volume behind each -- because an average over four thousand
    references treats a box a month and a box an hour as the same evidence."""
    numerator = Decimal("0")
    denominator = Decimal("0")
    for one in proposals:
        value = getattr(one, field)
        if value is None:
            continue
        weight = Decimal(one.trailing_monthly_units or 1)
        numerator += Decimal(value) * weight
        denominator += weight
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


@router.get("/pricing/adoption", response=PricingAdoptionOut, auth=owner_or_admin)
def pricing_adoption(request, days: int = Query(90)):
    """How often suggestions are taken, taken with a different number, or
    dismissed -- **split by `basis` and never aggregated across it**.

    The two engines make two different claims, and a blended adoption rate hides
    the case this stage most needs to detect: an elasticity engine nobody
    trusts, carried by a margin rule everybody uses.

    **What this cannot tell us**, stated so nobody reads more into it than it
    holds: it measures whether people agreed with the suggestions, not whether
    the suggestions were right. A margin that rose and a customer who quietly
    went elsewhere are both invisible here. The claim it supports is *the advice
    is being taken*; the claim it does not support is *the model made the
    pharmacy money*, which is measured against `daily_metrics` on the references
    that actually changed, joined through `proposal_id`, and only S9 computes it.
    """
    tenant = _tenant(request)
    until = timezone.localdate()
    since = until - timedelta(days=max(1, min(days, 730)))
    horizon = timezone.make_aware(
        datetime.combine(since, datetime.min.time()), timezone.get_current_timezone()
    )

    out = []
    for basis in (ProposalBasis.MARGIN_RULE, ProposalBasis.ELASTICITY):
        rows = list(
            PriceProposal.objects.filter(
                tenant_id=tenant.id, basis=basis, resolved_at__gte=horizon
            )
        )
        taken = [one for one in rows if one.status == PriceProposalStatus.TAKEN]
        modified = [one for one in rows if one.status == PriceProposalStatus.MODIFIED]
        dismissed = [one for one in rows if one.status == PriceProposalStatus.DISMISSED]
        superseded = PriceProposal.objects.filter(
            tenant_id=tenant.id,
            basis=basis,
            status=PriceProposalStatus.SUPERSEDED,
            computed_at__gte=horizon,
        ).count()
        # Everything ever suggested inside the window: what somebody resolved,
        # what a later run replaced, and what is still on the screen waiting.
        # Leaving the live rows out would make the superseded share a share of
        # the decided, which is the opposite of what it is for.
        awaiting = PriceProposal.objects.filter(
            tenant_id=tenant.id,
            basis=basis,
            status__in=LIVE_PROPOSAL_STATUSES,
            computed_at__gte=horizon,
        ).count()
        gaps = sorted(
            (Decimal(one.resolved_price) - Decimal(one.suggested_price))
            / Decimal(one.suggested_price)
            * Decimal("100")
            for one in modified
            if one.resolved_price and one.suggested_price
        )
        out.append(
            {
                "basis": basis,
                "taken": len(taken),
                "modified": len(modified),
                "dismissed": len(dismissed),
                "superseded": superseded,
                "resolved": len(rows),
                "proposed_ever": len(rows) + superseded + awaiting,
                "median_signed_gap_pct": _median(gaps),
                "dismissed_then_repriced": _repriced_after(tenant.id, dismissed),
            }
        )
    return {"since": since, "until": until, "by_basis": out}


def _median(values):
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle].quantize(Decimal("0.01"))
    return ((values[middle - 1] + values[middle]) / 2).quantize(Decimal("0.01"))


def _repriced_after(tenant_id, dismissed) -> int:
    """Of the dismissals, how many had their reference repriced by hand anyway
    inside the following month.

    Computable because a price change is a row in `item_prices` whether a
    suggestion informed it or not -- which is what tells a dismissal that meant
    *wrong* from one that meant *not now*.
    """
    total = 0
    for one in dismissed:
        if not one.resolved_at:
            continue
        after = timezone.localtime(one.resolved_at).date()
        if ItemPrice.objects.filter(
            tenant_id=tenant_id,
            item_id=one.item_id,
            effective_from__gt=after,
            effective_from__lte=after + timedelta(days=30),
        ).exists():
            total += 1
    return total


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.get("/pricing/runs", response=list[PricingRunOut], auth=owner_or_admin)
def list_runs(request, limit: int = Query(12)):
    """Run history and freshness, **derived from the rows the runs wrote**.

    There is no run table: the ledger assigns S7 two tables and no more.
    """
    from django.db.models import Count

    stamps = (
        ElasticityEstimate.objects.filter(tenant_id=request.tenant_id)
        .values("computed_at", "model_version")
        .annotate(total=Count("id"))
        .order_by("-computed_at")[: max(1, min(limit, 50))]
    )
    out = []
    for one in stamps:
        proposals = PriceProposal.objects.filter(
            tenant_id=request.tenant_id, computed_at=one["computed_at"]
        )
        out.append(
            {
                "computed_at": one["computed_at"],
                "model_version": one["model_version"],
                "estimates": one["total"],
                "proposals": proposals.count(),
                "by_basis": {
                    ProposalBasis.MARGIN_RULE: proposals.filter(
                        basis=ProposalBasis.MARGIN_RULE
                    ).count(),
                    ProposalBasis.ELASTICITY: proposals.filter(
                        basis=ProposalBasis.ELASTICITY
                    ).count(),
                },
            }
        )
    return out


@router.post("/pricing/runs", response=PricingRunEnqueuedOut, auth=owner_only)
def enqueue_run(request):
    """Enqueue a run now. Refused while one is in flight.

    **The most consequential write this stage exposes, and its whole consequence
    is that a screen changes.** *If restricting it to `owner` is wrong*, an
    `admin` who wants fresh figures asks somebody to press one button, which is
    a friction worth one line of code to remove -- a much smaller question than
    the one it replaces, and making it smaller is what A11 was for.
    """
    queued = jobs.enqueue_run(request.tenant_id, force=True)
    if not queued:
        raise HttpError(
            409,
            "Ya hay un cálculo de precios en curso. Espere a que termine para "
            "volver a pedirlo.",
        )
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="pricing.run",
        before=None,
        after={"requested_at": timezone.now().isoformat()},
        request_id=request_id.get(),
    )
    return {
        "queued": True,
        "detail": "Se está recalculando. Las propuestas nuevas aparecen en unos minutos.",
    }


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


@router.get("/pricing/caps", response=list[PricingCapOut], auth=owner_or_admin)
def list_caps(request):
    """Loaded caps with their source reference and date."""
    today = timezone.localdate()
    prices = engine.current_prices(request.tenant_id, today)
    out = []
    for item in cap_service.loaded(request.tenant_id):
        price = prices.get(item.id)
        out.append(
            {
                "item_id": item.id,
                "name": item.name,
                "presentation": item.presentation,
                "regulated_max_price": item.regulated_max_price,
                "cap_status": item.cap_status or CapStatus.UNKNOWN,
                "source": ((item.custom or {}).get("pricing") or {}).get(
                    "cap_source", ""
                ),
                "set_at": item.updated_at,
                "current_price": price,
                "above_cap": (price is not None and price > item.regulated_max_price),
            }
        )
    return out


# **Declared before `caps/{item_id}`**, and not for tidiness: a literal segment
# that arrives after a parameterised one at the same depth is matched by the
# parameter, and `POST /api/pricing/caps/import` would answer 405 against a
# route that only takes `PUT`.
@router.post("/pricing/caps/import", response=PricingCapImportOut, auth=owner_or_admin)
def import_caps(request, payload: PricingCapImportIn):
    """Bulk-load caps from CSV. Synchronous, refused above 5.000 rows.

    **It writes no `imports` row**: the ledger gives that table to S1 and S6,
    so a cap load records an `audit_log` row instead.
    """
    try:
        return cap_service.load_csv(
            tenant_id=request.tenant_id,
            actor=request.user,
            payload=payload.csv,
            request_id=request_id.get(),
        )
    except cap_service.CapRefused as refusal:
        raise HttpError(422, str(refusal)) from refusal


@router.put("/pricing/caps/{item_id}", response=PricingCapOut, auth=owner_or_admin)
def set_cap(request, item_id: uuid.UUID, payload: PricingCapIn):
    """Set or clear one item's cap. **Writes no price and touches no
    `item_prices` row** (A11)."""
    item = get_object_or_404(Item, id=item_id, tenant_id=request.tenant_id)
    try:
        cap_service.set_cap(
            item=item,
            actor=request.user,
            tenant_id=request.tenant_id,
            price=payload.regulated_max_price,
            status=payload.cap_status,
            source=payload.source,
            request_id=request_id.get(),
        )
    except cap_service.CapRefused as refusal:
        raise HttpError(422, str(refusal)) from refusal
    price = engine.current_prices(request.tenant_id, timezone.localdate()).get(item.id)
    return {
        "item_id": item.id,
        "name": item.name,
        "presentation": item.presentation,
        "regulated_max_price": item.regulated_max_price,
        "cap_status": item.cap_status,
        "source": payload.source,
        "set_at": item.updated_at,
        "current_price": price,
        "above_cap": (
            item.regulated_max_price is not None
            and price is not None
            and price > item.regulated_max_price
        ),
    }


# ---------------------------------------------------------------------------
# The settings group (rule 5)
# ---------------------------------------------------------------------------


@router.get("/settings/pricing", response=PricingSettingsOut, auth=owner_or_admin)
def read_pricing_settings(request):
    return pricing_settings.read(_tenant(request))


@router.patch("/settings/pricing", response=PricingSettingsOut, auth=owner_only)
def write_pricing_settings(request, payload: PricingSettingsIn):
    """One `jsonb_set`, every other group untouched (rule 5).

    Turning `allow_raise_without_cap` on is an ordinary settings write behind
    the ordinary permission dependency -- and it writes an `audit_log` row and
    puts a standing line on the Precios screen while it is on.
    """
    tenant = _tenant(request)
    before = pricing_settings.read(tenant)
    values = {
        key: value
        for key, value in payload.dict(exclude_unset=True).items()
        if value is not None and key != "clear_margin_goal"
    }
    if payload.clear_margin_goal:
        values[pricing_settings.GOAL_KEY] = None
    try:
        after = pricing_settings.write(tenant, values)
    except pricing_settings.Invalid as refusal:
        raise HttpError(422, str(refusal)) from refusal
    audit.record(
        actor=request.user,
        tenant_id=tenant.id,
        action=AuditAction.UPDATE,
        entity_type="settings.pricing",
        entity_id=tenant.id,
        before=before,
        after=after,
        request_id=request_id.get(),
    )
    return after


# ---------------------------------------------------------------------------
# The filter vocabularies the chips read
# ---------------------------------------------------------------------------


@router.get("/pricing/filters", response=dict, auth=owner_or_admin)
def pricing_filters(request):
    """`Laboratorio` and `Categoría`, restricted to references this screen shows.

    Served here rather than reused from S1's own lists because the chips are
    narrowed to what the grid actually holds: a laboratorio with no priced
    reference in it is a chip that filters to an empty screen.
    """
    return {
        "manufacturers": [
            {"id": one.id, "name": one.name}
            for one in Manufacturer.objects.filter(
                tenant_id=request.tenant_id
            ).order_by("name")
        ],
        "categories": [
            {"id": one.id, "name": one.name}
            for one in Category.objects.filter(tenant_id=request.tenant_id).order_by(
                "name"
            )
        ],
        "confidence": list(Confidence.values),
    }
