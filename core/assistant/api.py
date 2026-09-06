"""S8's endpoints, on the router `core.api` mounts.

Every path carries the `/api/` prefix and is English (§3), runs behind S0's
single permission dependency (§2) inside the pinned transaction (A1), honours
the grid contract (§9), and appends to `audit_log` through S0's path on every
elevated-role mutation (ledger).

**Nothing here is on the critical path of a sale, and that is structural rather
than a promise** (§4, §10). `POST /api/assistant/queries` is called from the
assistant column and is awaited by nothing else: not the search, not the line
add, not the totals, not `Cobrar`. The two paths share no lock, no transaction
and no request.

**`DELETE /api/item-warnings/{id}` deactivates.** A registry collection that is
hard-deleted leaves no row to serve a departure marker for and lives on every
till forever (S2, criterion 14), so there is no hard delete anywhere on this
router.
"""

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Query, Router, Schema
from ninja.errors import HttpError

from core import audit, gateway, scoping
from core.assistant import (
    extract,
    jobs,
    pipeline,
    prose,
    service,
    settings as assistant_settings,
    vocabulary,
)
from core.assistant.reasons import bundle_strings
from core.grid import DEFAULT_PAGE_SIZE, Page, paginate
from core.middleware import request_id
from core.models import (
    AssistantMode,
    AssistantQuery,
    AssistantSuggestion,
    AuditAction,
    CrossSellRule,
    Item,
    ItemWarning,
    ItemWarningSource,
    Sale,
    SaleLine,
    Tenant,
)
from core.permissions import any_member, owner_only, owner_or_admin
from core.sync import devices as device_service

router = Router()

BUSINESS_TIMEZONE = ZoneInfo("America/Bogota")

WarningTypeValue = Literal["interaction", "contraindication", "do_not_suggest_if"]
SeverityValue = Literal["blocking", "advisory"]
SourceValue = Literal["catalog", "manual"]
ModeValue = Literal["model", "local"]
SuggestionTypeValue = Literal["first_choice", "conditional", "bought_together"]
BandValue = Literal["low", "medium", "high"]
SortOrder = Literal["asc", "desc"]

WARNING_SORTABLE = {
    "item": ["item__name"],
    "type": ["type", "item__name"],
    "severity": ["severity", "item__name"],
}
RULE_SORTABLE = {
    "lift": ["-lift", "item_a__name"],
    "support": ["-support", "item_a__name"],
    "item_a": ["item_a__name", "item_b__name"],
}
QUERY_SORTABLE = {"recorded_at": ["-recorded_at"], "cost": ["-cost_usd"]}


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class AssistantSymptomIn(Schema):
    key: str
    label: str
    kind: str
    source: str = "lexicon"
    negated: bool = False
    value: float | None = None
    unit: str | None = None


class AssistantCandidateIn(Schema):
    """One card the till drew, with the `client_uuid` its row will carry."""

    client_uuid: uuid.UUID
    item_id: uuid.UUID
    type: SuggestionTypeValue
    reason: str = ""
    reason_code: str = ""
    price: Decimal
    rank: int = 1
    available_quantity: int = 0
    warning_id: uuid.UUID | None = None
    rule_confidence: BandValue | None = None
    #: The figures behind a `bought_together` card, sent to the model so its
    #: second register can name them and read by nothing else.
    pair_share: str | None = None
    pair_anchor: str | None = None


class AssistantAskIn(Schema):
    client_uuid: uuid.UUID
    device_id: uuid.UUID | None = None
    occurred_at: datetime | None = None
    #: The till's own id for the open ticket, which is the sale's `client_uuid`
    #: **and** its server id -- S4's writer keeps the id the device minted. It
    #: is normally null here: an open ticket's header reaches the server on the
    #: ordinary delta cadence, so the sale usually arrives after the question
    #: was asked and the `attach` event is what names it.
    sale_client_uuid: uuid.UUID | None = None
    transcript: str = ""
    symptoms: list[AssistantSymptomIn] = []
    candidates: list[AssistantCandidateIn] = []
    #: What survived the filter, which is the `3 de 12 referencias` denominator
    #: and is **not** the number of cards.
    candidate_count: int = 0
    excluded: list[dict] = []
    bundle_version: str = ""
    ruleset_computed_at: datetime | None = None


class AssistantSuggestionOut(Schema):
    id: uuid.UUID
    client_uuid: uuid.UUID
    item_id: uuid.UUID
    type: SuggestionTypeValue
    reason: str
    reason_code: str
    price: Decimal
    rank: int
    available_quantity: int
    accepted: bool


class AssistantAskOut(Schema):
    id: uuid.UUID
    mode: ModeValue
    recommendation: str
    recommendation_secondary: str
    #: `{item_id: frase}` -- only for cards whose reason is not fixed by a
    #: warning. A `conditional` card is never in this map.
    reasons: dict[str, str]
    output_check_passed: bool
    suggestions: list[AssistantSuggestionOut]


class AssistantAcceptIn(Schema):
    sale_line_id: uuid.UUID


class AssistantBundleOut(Schema):
    version: str
    symptoms: dict
    populations: dict
    ingredients: dict
    treatment_leads: list[str]
    negations: list[str]
    number_words: dict[str, int]
    age_populations: list[str]
    symptom_category_map: dict
    strings: dict
    #: The two settings the column's own shape depends on, delivered with the
    #: bundle because a till that is offline still has to know whether the
    #: column exists and how many cards it draws. Everything else in the group
    #: is an office concern.
    enabled: bool
    suggestion_card_count: int
    retain_transcripts: bool


class ItemWarningOut(Schema):
    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    type: WarningTypeValue
    text: str
    severity: SeverityValue
    source: SourceValue
    triggers: list[dict]
    active: bool
    #: `null` where the warning has never been evaluated against an extraction,
    #: which is not the same as *has never fired* -- §B.9.2 tier 3.
    last_matched_at: datetime | None
    never_matched: bool


class ItemWarningIn(Schema):
    item_id: uuid.UUID
    type: WarningTypeValue
    text: str
    severity: SeverityValue
    triggers: list[dict] = []


class ItemWarningPatch(Schema):
    text: str | None = None
    severity: SeverityValue | None = None
    triggers: list[dict] | None = None
    active: bool | None = None


class CrossSellRuleOut(Schema):
    id: uuid.UUID
    location_id: uuid.UUID | None
    location_name: str | None
    item_a_id: uuid.UUID
    item_a: str
    item_b_id: uuid.UUID
    item_b: str
    support: int
    #: P(B present | A present). **Rendered under `% del ancla`**, never under
    #: `Confianza` -- the two are different quantities and one Spanish word over
    #: both is how the wrong number ends up on screen.
    confidence: Decimal
    lift: Decimal
    window: str
    ticket_count: int
    basis: Literal["counter", "imported", "mixed"]
    confidence_band: BandValue
    computed_at: datetime


class CrossSellRefreshOut(Schema):
    queued: bool
    detail: str


class AssistantQueryRowOut(Schema):
    id: uuid.UUID
    recorded_at: datetime
    location_id: uuid.UUID
    location_name: str
    user_name: str | None
    #: `null` where retention has elapsed. The grid renders `—` and the reading
    #: `depurado por retención`, never a blank and never a zero.
    symptoms: list[dict] | None
    mode: ModeValue
    output_check_passed: bool
    output_check_flags: list[str]
    accepted_count: int
    offered_count: int
    cost_usd: Decimal
    latency_ms: int | None
    purged: bool


class AssistantMetricsOut(Schema):
    offered: int
    accepted: int
    rate: float | None
    model_queries: int
    local_queries: int
    rejected_queries: int
    rejection_rate: float | None
    spend_month_to_date: Decimal
    spend_cap: Decimal
    suggested_tickets: int
    suggested_mean: Decimal | None
    plain_tickets: int
    plain_mean: Decimal | None
    combinations: list[dict]
    #: What `assistant.health_check` computes, carried on the same read rather
    #: than on a path of its own -- the report is three counts over indexed
    #: columns, and a second endpoint for it is one the *API surface* contract
    #: does not name. **The job and the screen run the same function**, so they
    #: cannot disagree about whether a warning has ever fired.
    rejection_alert: bool
    alert_threshold: float
    queries_without_chips: int
    unmapped_symptom_keys: list[str]
    dormant_warnings: list[dict]


class AssistantSettingsOut(Schema):
    enabled: bool
    model_enabled: bool
    model: str
    monthly_spend_cap_usd: float
    model_timeout_ms: int
    retain_transcripts: bool
    transcript_retention_days: int
    symptom_category_map: dict
    suggestion_card_count: int
    cross_sell_min_support: int
    cross_sell_min_confidence: float
    cross_sell_rules_per_item: int
    cross_sell_window_days: int
    output_check_alert_rate: float


class AssistantSettingsPatch(Schema):
    enabled: bool | None = None
    model_enabled: bool | None = None
    model: str | None = None
    monthly_spend_cap_usd: float | None = None
    model_timeout_ms: int | None = None
    retain_transcripts: bool | None = None
    transcript_retention_days: int | None = None
    symptom_category_map: dict | None = None
    suggestion_card_count: int | None = None
    cross_sell_min_support: int | None = None
    cross_sell_min_confidence: float | None = None
    cross_sell_rules_per_item: int | None = None
    cross_sell_window_days: int | None = None
    output_check_alert_rate: float | None = None


class AssistantHealthOut(Schema):
    """What `assistant.health_check` last found, for Ajustes.

    Computed on read rather than stored: the report is three counts over
    indexed columns and a table for it would be a fifth table for a screen
    nobody opens twice a day.
    """

    rejection_rate: float | None
    rejection_alert: bool
    alert_threshold: float
    queries_without_chips: int
    queries_considered: int
    unmapped_symptom_keys: list[str]
    dormant_warnings: list[dict]


# ---------------------------------------------------------------------------
# The counter's three calls
# ---------------------------------------------------------------------------


@router.post("/assistant/queries", response=AssistantAskOut, auth=any_member)
def ask(request, payload: AssistantAskIn):
    """Ask. **Idempotent on `(tenant_id, client_uuid)`** (A5).

    The chips, the filter, the ranking and the cards were all computed on the
    device before this was called, and they are what the body carries. What this
    endpoint adds is the recommendation's two registers -- and where the model
    is off, capped, unreachable, slow or rejected, it adds them from the same
    ranking by a template over `reason_code` and stamps `mode = 'local'`.
    """
    tenant = Tenant.objects.get(id=request.tenant_id)
    values = assistant_settings.read(tenant)
    if not values.get("enabled"):
        raise HttpError(403, "El asistente está desactivado en esta droguería.")

    device = device_service.resolve(request)
    # A2 · the sede is a scope, and the device's own sede has to be inside the
    # identity's. A cashier reclaiming a till at another sede is the case this
    # catches; a `cashier` with no home sede is refused by the helper itself
    # rather than falling through to every location.
    scoping.readable_locations(
        request.user, request.tenant_id, requested=[device.location_id]
    )
    symptoms = [one.dict() for one in payload.symptoms]
    cards = [_card(one) for one in payload.candidates]

    held = AssistantQuery.objects.filter(
        tenant_id=request.tenant_id, client_uuid=payload.client_uuid
    ).first()
    if held is not None:
        return _ask_out(held, list(held.suggestions.order_by("rank")), {})

    # **The server's own hard filter runs before any prompt exists.** §7's three
    # exclusions and every satisfied blocking warning are applied to whatever
    # arrived, and only what survives is named to a vendor -- otherwise a till
    # that was out of date about the catalog could put a prescription-only
    # reference in front of a model, which is the one order of operations *3 ·
    # Filter* is emphatic about.
    kept, dropped = service.vet(
        tenant_id=request.tenant_id, candidates=cards, symptoms=symptoms
    )
    cards = kept

    answer = None
    # **Nothing to write about is not a call worth making.** A question the
    # shelf could not answer gets card C's deliberately-empty shape and card B's
    # local line; asking a vendor to compose prose about an empty list is a
    # charge against the cap for a sentence nobody reads.
    if values.get("model_enabled") and cards:
        answer = prose.ask(
            tenant=tenant,
            settings=values,
            symptoms=symptoms,
            cards=cards,
            transcript=payload.transcript,
            location_name=device.location.name,
        )

    passed = True
    flags: list[str] = []
    cleaned: dict = {}
    mode = AssistantMode.LOCAL
    model_name = ""
    cost = Decimal("0")
    latency = None
    if answer is not None:
        latency = answer.get("latency_ms")
        cost = answer.get("cost_usd") or Decimal("0")
        model_name = answer.get("model") or ""
        passed, flags, cleaned = prose.check(
            prose.parse(answer.get("text")),
            cards=cards,
            symptoms=symptoms,
            known_molecules=_molecules(request.tenant_id),
        )
        if passed:
            mode = AssistantMode.MODEL

    if mode == AssistantMode.MODEL:
        primary = cleaned["recommendation"]
        secondary = cleaned["recommendation_secondary"]
        improved = cleaned["reasons"]
    else:
        primary, secondary = pipeline.local_prose(cards, payload.candidate_count)
        improved = {}
    for card in cards:
        better = improved.get(card["item_id"])
        if better:
            card["reason"] = better

    transcript = payload.transcript if values.get("retain_transcripts") else ""
    query, rows, _created = service.record(
        tenant_id=request.tenant_id,
        location_id=device.location_id,
        client_uuid=payload.client_uuid,
        device=device,
        user=request.user,
        user_name=request.user.name,
        sale_id=_own_sale(request.tenant_id, payload.sale_client_uuid),
        transcript=transcript,
        symptoms=symptoms,
        candidates=cards,
        candidate_count=payload.candidate_count,
        bundle_version=payload.bundle_version or bundle_version(values),
        ruleset_computed_at=payload.ruleset_computed_at,
        occurred_at=payload.occurred_at,
        mode=mode,
        model=model_name,
        cost_usd=cost,
        latency_ms=latency,
        recommendation=primary,
        recommendation_secondary=secondary,
        output_check_passed=passed,
        output_check_flags=flags,
        excluded=[*payload.excluded, *dropped],
    )
    return _ask_out(query, rows, improved)


def _molecules(tenant_id) -> set[str]:
    """Every active ingredient this pharmacy stocks, for the output check.

    One distinct scan over the catalog, on an endpoint that is off the sale's
    critical path by construction (§4). It is what makes *"names a product name
    outside the candidate list"* a decidable rule rather than a guess about
    which words in a sentence are products.
    """
    return {
        one
        for one in Item.objects.filter(tenant_id=tenant_id, active=True)
        .exclude(active_ingredient="")
        .values_list("active_ingredient", flat=True)
        .distinct()
        if one
    }


def _card(one: AssistantCandidateIn) -> dict:
    return {
        "client_uuid": str(one.client_uuid),
        "item_id": str(one.item_id),
        "item_name": "",
        "presentation": "",
        "type": one.type,
        "reason": one.reason,
        "reason_code": one.reason_code,
        "price": one.price,
        "rank": one.rank,
        "available_quantity": one.available_quantity,
        "warning_id": str(one.warning_id) if one.warning_id else None,
        "rule_confidence": one.rule_confidence,
        "pair_share": one.pair_share,
        "pair_anchor": one.pair_anchor,
    }


def _own_sale(tenant_id, client_uuid):
    """The ticket this question was asked against, where it has already arrived.

    Resolved on `client_uuid` rather than on `id` -- the two are the same value
    on a sale S4's writer landed, and the one the till is authoritative about is
    the one it minted.
    """
    if client_uuid is None:
        return None
    return (
        Sale.objects.filter(tenant_id=tenant_id, client_uuid=client_uuid)
        .values_list("id", flat=True)
        .first()
    )


def _ask_out(query, rows, improved):
    return {
        "id": query.id,
        "mode": query.mode,
        "recommendation": query.recommendation,
        "recommendation_secondary": query.recommendation_secondary,
        "reasons": improved,
        "output_check_passed": query.output_check_passed,
        "suggestions": [
            {
                "id": row.id,
                "client_uuid": row.client_uuid,
                "item_id": row.item_id,
                "type": row.type,
                "reason": row.reason,
                "reason_code": row.reason_code,
                "price": row.price,
                "rank": row.rank,
                "available_quantity": row.available_quantity,
                "accepted": row.accepted,
            }
            for row in rows
        ],
    }


@router.post(
    "/assistant/suggestions/{suggestion_id}/accept",
    response=AssistantSuggestionOut,
    auth=any_member,
)
def accept(request, suggestion_id: uuid.UUID, payload: AssistantAcceptIn):
    """Record acceptance and flag the line, in one transaction.

    **Idempotent** -- a second call with the same `sale_line_id` returns the row
    unchanged; a call naming a different line is refused.
    """
    suggestion = get_object_or_404(
        AssistantSuggestion, id=suggestion_id, tenant_id=request.tenant_id
    )
    line = get_object_or_404(
        SaleLine, id=payload.sale_line_id, tenant_id=request.tenant_id
    )
    try:
        row = service.accept(suggestion, sale_line=line)
    except service.Refused as refusal:
        raise HttpError(409, str(refusal)) from refusal
    return {
        "id": row.id,
        "client_uuid": row.client_uuid,
        "item_id": row.item_id,
        "type": row.type,
        "reason": row.reason,
        "reason_code": row.reason_code,
        "price": row.price,
        "rank": row.rank,
        "available_quantity": row.available_quantity,
        "accepted": row.accepted,
    }


@router.post("/assistant/queries/{query_id}/supersede", response=dict, auth=any_member)
def supersede(request, query_id: uuid.UUID):
    """The cashier re-asked on the same open sale."""
    query = get_object_or_404(AssistantQuery, id=query_id, tenant_id=request.tenant_id)
    row = service.supersede(query)
    return {"id": str(row.id), "superseded_at": row.superseded_at}


@router.get("/assistant/bundle", response=AssistantBundleOut, auth=any_member)
def bundle(request):
    """The client reference bundle and its version.

    **Not a registry collection.** The lexicon, the vocabulary and the tenant's
    map are one document of a few kilobytes with no per-row deltas and no
    natural key worth versioning, so they are cached with the device record and
    refreshed when the version changes -- the same treatment S2 gives the sede's
    own name and code. A fifth collection to compact and reconcile for one
    document is what rule 9 exists to prevent.
    """
    device_service.resolve(request)
    tenant = Tenant.objects.get(id=request.tenant_id)
    values = assistant_settings.read(tenant)
    return {
        "version": bundle_version(values),
        "symptoms": {
            key: {"label": label, "forms": list(forms)}
            for key, (label, forms) in vocabulary.SYMPTOMS.items()
        },
        "populations": {
            key: {"label": label, "forms": list(forms)}
            for key, (label, forms) in vocabulary.POPULATIONS.items()
        },
        "ingredients": {
            key: {"label": label, "forms": list(forms)}
            for key, (label, forms) in vocabulary.INGREDIENTS.items()
        },
        "treatment_leads": list(vocabulary.TREATMENT_LEADS),
        "negations": list(extract.NEGATIONS),
        "number_words": dict(extract.NUMBER_WORDS),
        "age_populations": sorted(vocabulary.AGE_POPULATIONS),
        "symptom_category_map": values.get("symptom_category_map") or {},
        "strings": bundle_strings(),
        "enabled": bool(values.get("enabled")),
        "suggestion_card_count": int(values.get("suggestion_card_count", 3)),
        "retain_transcripts": bool(values.get("retain_transcripts")),
    }


#: What the bundle carries beyond the shipped lexicon, and therefore what its
#: version has to be computed over. **The two settings are in here for a reason
#: that is not tidiness**: a till refreshes the bundle only when the version
#: moves, so an `enabled` an administrator turned off would sit in a cached
#: document forever and criterion 22 would be false on every till that had
#: already synced once.
BUNDLED_KEYS = (
    "symptom_category_map",
    "enabled",
    "suggestion_card_count",
    "retain_transcripts",
)


def bundle_version(values) -> str:
    """`<lexicon>.<digest>` -- the shipped list and everything tenant-specific
    the bundle carries.

    A digest rather than a counter, because the map is edited in Ajustes and a
    counter would need a column nobody else reads. It is stamped on every
    `assistant_queries` row, which is what makes a till extracting against last
    quarter's vocabulary visible.
    """
    document = json.dumps(
        {key: values.get(key) for key in BUNDLED_KEYS}, sort_keys=True
    )
    digest = hashlib.sha256(document.encode("utf-8")).hexdigest()[:8]
    return f"{vocabulary.LEXICON_VERSION}.{digest}"


# ---------------------------------------------------------------------------
# The office reads
# ---------------------------------------------------------------------------


@router.get(
    "/assistant/queries", response=Page[AssistantQueryRowOut], auth=owner_or_admin
)
def query_log(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
    location_id: uuid.UUID | None = Query(None),
    mode: ModeValue | None = Query(None),
    passed: bool | None = Query(None),
    days: int = Query(30),
):
    """The query log. **Renders only what retention has kept.**"""
    opened, closed = _period(days)
    rows = (
        AssistantQuery.objects.filter(
            tenant_id=request.tenant_id, recorded_at__gte=opened, recorded_at__lt=closed
        )
        .select_related("location", "user")
        .annotate(
            offered=Count("suggestions", distinct=True),
            taken=Count(
                "suggestions", filter=Q(suggestions__accepted=True), distinct=True
            ),
        )
    )
    rows = scoping.scope(
        rows,
        request.user,
        request.tenant_id,
        requested=[location_id] if location_id else None,
    )
    if mode is not None:
        rows = rows.filter(mode=mode)
    if passed is not None:
        rows = rows.filter(output_check_passed=passed)
    return _query_page(
        rows.order_by("-recorded_at"),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
    )


def _query_page(rows, *, page, page_size, sort, order):
    found, row_count, page, page_size = paginate(
        rows,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=QUERY_SORTABLE,
    )
    return {
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
        "rows": [
            {
                "id": row.id,
                "recorded_at": row.recorded_at,
                "location_id": row.location_id,
                "location_name": row.location.name,
                # The name the row stamped, which outlives the account.
                "user_name": row.user_name or None,
                # **A purged row renders `—`, never a blank and never a zero**
                # (§B.9.2 tier 3). The distinction is carried by `purged` rather
                # than inferred from an empty list, because a query that extracted
                # no chip at all is a different fact.
                "symptoms": None if _purged(row) else row.symptoms,
                "mode": row.mode,
                "output_check_passed": row.output_check_passed,
                "output_check_flags": row.output_check_flags,
                "accepted_count": row.taken,
                "offered_count": row.offered,
                "cost_usd": row.cost_usd,
                "latency_ms": row.latency_ms,
                "purged": _purged(row),
            }
            for row in found
        ],
    }


def _purged(row) -> bool:
    """Whether retention has taken this row's words.

    A row whose transcript, recommendation **and** symptoms are all empty was
    purged; a live row that extracted nothing still carries its recommendation.
    """
    return not row.symptoms and not row.transcript and not row.recommendation


@router.get("/assistant/metrics", response=AssistantMetricsOut, auth=owner_or_admin)
def metrics(
    request,
    days: int = Query(30),
    location_id: uuid.UUID | None = Query(None),
):
    """Offered, accepted, the rate, the mode split, the rejection rate, the
    month-to-date spend, and the three definitions over a period and a sede set.

    **S9 reads these and derives none of them a second way** (*Hands off*).
    """
    opened, closed = _period(days)
    allowed = scoping.readable_locations(
        request.user,
        request.tenant_id,
        requested=[location_id] if location_id else None,
    )
    figures = service.acceptance(
        request.tenant_id, opened=opened, closed=closed, location_ids=allowed
    )
    comparison = service.ticket_comparison(
        request.tenant_id, opened=opened, closed=closed, location_ids=allowed
    )
    queries = AssistantQuery.objects.filter(
        tenant_id=request.tenant_id,
        recorded_at__gte=opened,
        recorded_at__lt=closed,
        location_id__in=allowed,
    )
    counts = queries.aggregate(
        model_queries=Count("id", filter=Q(mode=AssistantMode.MODEL)),
        local_queries=Count("id", filter=Q(mode=AssistantMode.LOCAL)),
        rejected=Count("id", filter=Q(output_check_passed=False)),
    )
    answered = int(counts["model_queries"] or 0) + int(counts["rejected"] or 0)
    tenant = Tenant.objects.get(id=request.tenant_id)
    values = assistant_settings.read(tenant)
    report = jobs.health_report(tenant)
    return {
        **figures,
        "model_queries": counts["model_queries"] or 0,
        "local_queries": counts["local_queries"] or 0,
        "rejected_queries": counts["rejected"] or 0,
        # Over the queries a model actually answered, not over every query: a
        # rate whose denominator counted offline queries would fall whenever the
        # fibre did.
        "rejection_rate": (int(counts["rejected"] or 0) / answered)
        if answered
        else None,
        "spend_month_to_date": gateway.spend_this_month(request.tenant_id),
        "spend_cap": assistant_settings.spend_cap(values),
        **comparison,
        "combinations": service.combinations(
            request.tenant_id, opened=opened, closed=closed, location_ids=allowed
        ),
        **{
            key: report[key]
            for key in (
                "rejection_alert",
                "alert_threshold",
                "queries_without_chips",
                "unmapped_symptom_keys",
                "dormant_warnings",
            )
        },
    }


# ---------------------------------------------------------------------------
# The safety layer
# ---------------------------------------------------------------------------


@router.get("/item-warnings", response=Page[ItemWarningOut], auth=owner_or_admin)
def list_warnings(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("asc"),
    item_id: uuid.UUID | None = Query(None),
    type: WarningTypeValue | None = Query(None),
    severity: SeverityValue | None = Query(None),
    source: SourceValue | None = Query(None),
    active: bool | None = Query(None),
):
    rows = ItemWarning.objects.filter(tenant_id=request.tenant_id).select_related(
        "item"
    )
    if item_id is not None:
        rows = rows.filter(item_id=item_id)
    if type is not None:
        rows = rows.filter(type=type)
    if severity is not None:
        rows = rows.filter(severity=severity)
    if source is not None:
        rows = rows.filter(source=source)
    rows = rows.filter(active=True if active is None else active)
    found, row_count, page, page_size = paginate(
        rows.order_by("item__name", "type"),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=WARNING_SORTABLE,
    )
    dormant = jobs.dormant_warning_ids(request.tenant_id)
    return {
        "rows": [_warning_out(row, dormant) for row in found],
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
    }


def _warning_out(row, dormant):
    return {
        "id": row.id,
        "item_id": row.item_id,
        "item_name": row.item.name,
        "type": row.type,
        "text": row.text,
        "severity": row.severity,
        "source": row.source,
        "triggers": row.triggers,
        "active": row.active,
        "last_matched_at": None,
        "never_matched": str(row.id) in dormant,
    }


@router.post("/item-warnings", response=ItemWarningOut, auth=owner_or_admin)
def create_warning(request, payload: ItemWarningIn):
    """Create one. `triggers` is validated against the closed vocabulary and
    refused otherwise, at **field scope**, naming the key (§B.10.3)."""
    item = get_object_or_404(Item, id=payload.item_id, tenant_id=request.tenant_id)
    triggers = _checked_triggers(payload.triggers)
    row = ItemWarning.objects.create(
        tenant_id=request.tenant_id,
        item=item,
        type=payload.type,
        text=payload.text.strip(),
        severity=payload.severity,
        source=ItemWarningSource.MANUAL,
        triggers=triggers,
        active=True,
        created_by_user=request.user,
    )
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.CREATE,
        entity_type="item_warning",
        entity_id=row.id,
        after=_snapshot(row),
        request_id=request_id.get(),
    )
    return _warning_out(row, set())


@router.patch(
    "/item-warnings/{warning_id}", response=ItemWarningOut, auth=owner_or_admin
)
def edit_warning(request, warning_id: uuid.UUID, payload: ItemWarningPatch):
    row = get_object_or_404(ItemWarning, id=warning_id, tenant_id=request.tenant_id)
    before = _snapshot(row)
    if payload.text is not None:
        row.text = payload.text.strip()
    if payload.severity is not None:
        row.severity = payload.severity
    if payload.triggers is not None:
        row.triggers = _checked_triggers(payload.triggers)
    if payload.active is not None:
        row.active = payload.active
    row.save()
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="item_warning",
        entity_id=row.id,
        before=before,
        after=_snapshot(row),
        request_id=request_id.get(),
    )
    return _warning_out(row, set())


@router.delete("/item-warnings/{warning_id}", response=ItemWarningOut, auth=owner_only)
def deactivate_warning(request, warning_id: uuid.UUID):
    """**Deactivates -- sets `active = false`.**

    A registry collection is never hard-deleted while the registry lists it, or
    the row lives on every till forever (S2, criterion 14). The device sees a
    departure marker on its next pull and stops filtering on it.
    """
    row = get_object_or_404(ItemWarning, id=warning_id, tenant_id=request.tenant_id)
    before = _snapshot(row)
    row.active = False
    row.save(update_fields=["active", "updated_at"])
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.ARCHIVE,
        entity_type="item_warning",
        entity_id=row.id,
        before=before,
        after=_snapshot(row),
        request_id=request_id.get(),
    )
    return _warning_out(row, set())


def _checked_triggers(triggers):
    try:
        return vocabulary.check_triggers(triggers)
    except vocabulary.InvalidTrigger as refusal:
        raise HttpError(422, str(refusal)) from refusal


def _snapshot(row):
    return {
        "item_id": str(row.item_id),
        "type": row.type,
        "text": row.text,
        "severity": row.severity,
        "triggers": row.triggers,
        "active": row.active,
    }


# ---------------------------------------------------------------------------
# The mined rules
# ---------------------------------------------------------------------------


@router.get("/cross-sell-rules", response=Page[CrossSellRuleOut], auth=owner_or_admin)
def list_rules(
    request,
    page: int = Query(1),
    page_size: int = Query(DEFAULT_PAGE_SIZE),
    sort: str | None = Query(None),
    order: SortOrder = Query("desc"),
    item_id: uuid.UUID | None = Query(None),
    location_id: uuid.UUID | None = Query(None),
    network: bool = Query(False),
):
    """The mined rules, read-only. **So a regente can see why the assistant
    offers what it offers.**

    Ajustes' provenance line -- `Reglas calculadas sobre {ventana} · {n} tickets
    · actualizadas el {fecha}` -- is composed from `window`, `ticket_count` and
    `computed_at`, which every row already carries. There is no endpoint for it,
    because a second path for three columns already on the wire is a path the
    *API surface* contract does not name.
    """
    rows = CrossSellRule.objects.filter(tenant_id=request.tenant_id).select_related(
        "item_a", "item_b", "location"
    )
    if item_id is not None:
        rows = rows.filter(Q(item_a_id=item_id) | Q(item_b_id=item_id))
    if network:
        rows = rows.filter(location__isnull=True)
    elif location_id is not None:
        scoping.readable_locations(
            request.user, request.tenant_id, requested=[location_id]
        )
        rows = rows.filter(location_id=location_id)
    found, row_count, page, page_size = paginate(
        rows.order_by("-lift", "item_a__name"),
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        sortable=RULE_SORTABLE,
    )
    return {
        "row_count": row_count,
        "page": page,
        "page_size": page_size,
        "rows": [
            {
                "id": row.id,
                "location_id": row.location_id,
                "location_name": row.location.name if row.location_id else None,
                "item_a_id": row.item_a_id,
                "item_a": row.item_a.name,
                "item_b_id": row.item_b_id,
                "item_b": row.item_b.name,
                "support": row.support,
                "confidence": row.confidence,
                "lift": row.lift,
                "window": row.window,
                "ticket_count": row.ticket_count,
                "basis": row.basis,
                "confidence_band": row.confidence_band,
                "computed_at": row.computed_at,
            }
            for row in found
        ],
    }


@router.post(
    "/cross-sell-rules/refresh", response=CrossSellRefreshOut, auth=owner_or_admin
)
def refresh_rules(request, location_id: uuid.UUID | None = Query(None)):
    """Run the miner now, for a tenant or for one sede. **Returns the job
    handle, not the rules** -- mining is asynchronous and a request that waited
    for it would be a request that timed out on a network with real history.

    A sede named here fans out one job for that scope alone; with none, the
    tenant fans out one per sede plus the network pass.
    """
    if location_id is not None:
        scoping.readable_locations(
            request.user, request.tenant_id, requested=[location_id]
        )
    queued = jobs.enqueue_refresh(request.tenant_id, location_id=location_id)
    return {
        "queued": queued,
        "detail": (
            "El cálculo quedó en cola. Las reglas cambian cuando termine."
            if queued
            else "Ya hay un cálculo en cola para esta droguería."
        ),
    }


# ---------------------------------------------------------------------------
# The settings group
# ---------------------------------------------------------------------------


@router.get("/settings/assistant", response=AssistantSettingsOut, auth=owner_or_admin)
def read_settings(request):
    return assistant_settings.read(Tenant.objects.get(id=request.tenant_id))


@router.patch("/settings/assistant", response=AssistantSettingsOut, auth=owner_or_admin)
def write_settings(request, payload: AssistantSettingsPatch):
    """Written through S0's single-group helper -- one `jsonb_set`, every other
    group untouched, raising rather than passing quietly when the `UPDATE`
    matches no row (rule 5). Audit-logged."""
    tenant = Tenant.objects.get(id=request.tenant_id)
    before = assistant_settings.read(tenant)
    changes = {
        key: value
        for key, value in payload.dict(exclude_unset=True).items()
        if value is not None
    }
    if "symptom_category_map" in changes:
        changes["symptom_category_map"] = _checked_map(
            request.tenant_id, changes["symptom_category_map"]
        )
    try:
        after = assistant_settings.write(tenant, changes)
    except assistant_settings.Invalid as refusal:
        raise HttpError(422, str(refusal)) from refusal
    audit.record(
        actor=request.user,
        tenant_id=request.tenant_id,
        action=AuditAction.UPDATE,
        entity_type="settings.assistant",
        entity_id=request.tenant_id,
        before=before,
        after=after,
        request_id=request_id.get(),
    )
    return after


def _checked_map(tenant_id, mapping):
    """The map, with every category id checked against this tenant's own tree.

    A map naming another network's category would seed nothing and would do it
    silently, which is the failure the closed vocabulary exists to prevent one
    door along.
    """
    from core.models import Category

    try:
        checked = assistant_settings.check_map(mapping)
    except assistant_settings.Invalid as refusal:
        raise HttpError(422, str(refusal)) from refusal
    wanted = {one for ids in checked.values() for one in ids}
    known = {
        str(one)
        for one in Category.objects.filter(
            tenant_id=tenant_id, id__in=list(wanted)
        ).values_list("id", flat=True)
    }
    missing = sorted(wanted - known)
    if missing:
        raise HttpError(
            422,
            f"«{missing[0]}» no es una categoría de esta droguería.",
        )
    return checked


def _period(days: int) -> tuple[datetime, datetime]:
    """A period on the pharmacy's own clock, closed at the start of tomorrow.

    Every window in this stage is read on `recorded_at` (rule 8: `recorded_at`
    for every report, `occurred_at` never).
    """
    if days < 1 or days > 730:
        raise HttpError(422, "El periodo va de 1 a 730 días.")
    today: date = timezone.now().astimezone(BUSINESS_TIMEZONE).date()
    closed = datetime.combine(today + timedelta(days=1), time.min, BUSINESS_TIMEZONE)
    opened = closed - timedelta(days=days)
    return opened, closed
