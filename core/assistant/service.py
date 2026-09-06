"""What writes this stage's two client-written tables, and the three definitions
S9 reads and does not recompute.

**One implementation of acceptance, reachable two ways.** `POST
/api/assistant/suggestions/{id}/accept` and S2's push writer both call `accept`
below, so an acceptance recorded online and one recorded from a queue that sat
through a blackout are the same act and cannot drift.

**The server never trusts a client's candidate list about safety.** `record`
re-runs the hard half of the filter -- active, prescription, controlled, expired
registration, and any `blocking` warning the extraction satisfies -- over
whatever arrived, and drops what fails into `excluded`. A till that is out of
date about the catalog would otherwise be able to offer what §7 says is never
offered, and check 2 would pass on every screen anyone looked at while being
false in the table.
"""

import uuid
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from core.assistant import filters, pipeline
from core.models import (
    AssistantMode,
    AssistantQuery,
    AssistantSuggestion,
    Item,
    ItemWarning,
    SaleLine,
    SaleSource,
    SaleStatus,
    SuggestionType,
    WarningSeverity,
)


class Refused(ValueError):
    """A write the service declines, in Spanish, naming what is wrong."""


# ---------------------------------------------------------------------------
# The offer
# ---------------------------------------------------------------------------


def record(
    *,
    tenant_id,
    location_id,
    client_uuid,
    device=None,
    user=None,
    user_name="",
    sale_id=None,
    transcript="",
    symptoms=(),
    candidates=(),
    candidate_count=0,
    bundle_version="",
    ruleset_computed_at=None,
    occurred_at=None,
    mode=AssistantMode.LOCAL,
    model="",
    cost_usd=Decimal("0"),
    latency_ms=None,
    recommendation="",
    recommendation_secondary="",
    output_check_passed=True,
    output_check_flags=(),
    excluded=(),
) -> tuple[AssistantQuery, list[AssistantSuggestion], bool]:
    """Write one query and the cards it showed. Returns `(query, rows, created)`.

    **Idempotent on `(tenant_id, client_uuid)`** -- a retried ask after a
    timeout returns the same row and calls the gateway once (A5). The offer rows
    are written here and not at acceptance, which is what gives the acceptance
    rate a denominator.
    """
    held = AssistantQuery.objects.filter(
        tenant_id=tenant_id, client_uuid=client_uuid
    ).first()
    if held is not None:
        return held, list(held.suggestions.all().order_by("rank")), False

    kept, dropped = vet(tenant_id=tenant_id, candidates=candidates, symptoms=symptoms)
    now = timezone.now()
    query = AssistantQuery(
        # **The till's own id, kept.** S4's writers do the same, and it is what
        # lets a device that pushed an offer during a blackout name the row it
        # is accepting without a second lookup -- `client_uuid` and `id` are one
        # value on every client-written table in this stage.
        id=client_uuid,
        tenant_id=tenant_id,
        location_id=location_id,
        client_uuid=client_uuid,
        device=device,
        user=user,
        # **Stamped, not joined.** §2 hard-deletes a `users` row, and the log
        # has to keep saying who asked.
        user_name=user_name or getattr(user, "name", "") or "",
        sale_id=sale_id,
        occurred_at=occurred_at or now,
        recorded_at=now,
        transcript=transcript or "",
        symptoms=list(symptoms or []),
        recommendation=recommendation or "",
        recommendation_secondary=recommendation_secondary or "",
        mode=mode,
        model=model or "",
        cost_usd=cost_usd or Decimal("0"),
        latency_ms=latency_ms,
        excluded=[*excluded, *dropped],
        output_check_passed=output_check_passed,
        output_check_flags=list(output_check_flags or []),
        candidate_count=int(candidate_count or 0),
        bundle_version=bundle_version or "",
        ruleset_computed_at=ruleset_computed_at,
    )
    try:
        with transaction.atomic():
            query.save()
    except IntegrityError:
        # The exact race A5 exists to make safe: two pushes carrying one batch
        # both found nothing and both tried. The loser reads the winner.
        winner = AssistantQuery.objects.filter(
            tenant_id=tenant_id, client_uuid=client_uuid
        ).first()
        if winner is None:
            raise
        return winner, list(winner.suggestions.all().order_by("rank")), False

    rows = [
        AssistantSuggestion(
            id=card["client_uuid"],
            tenant_id=tenant_id,
            query=query,
            location_id=location_id,
            client_uuid=card["client_uuid"],
            device=device,
            occurred_at=query.occurred_at,
            recorded_at=now,
            item_id=card["item_id"],
            type=card["type"],
            reason=card.get("reason") or "",
            reason_code=card.get("reason_code") or "",
            price=card["price"],
            rank=card.get("rank") or index,
            available_quantity=int(card.get("available_quantity") or 0),
            warning_id=card.get("warning_id") or None,
            rule_confidence=card.get("rule_confidence") or None,
        )
        for index, card in enumerate(kept, start=1)
    ]
    AssistantSuggestion.objects.bulk_create(rows)
    return query, rows, True


def vet(*, tenant_id, candidates, symptoms):
    """The hard half of the filter, re-run over whatever a client sent.

    Returns `(kept, dropped)`. `dropped` is in `assistant_queries.excluded`'s own
    shape, so a row removed here is indistinguishable on the record from one the
    till removed -- which is what makes criterion 5 readable off one column.
    """
    rows = list(candidates or [])
    if not rows:
        return [], []
    ids = [str(card.get("item_id")) for card in rows if card.get("item_id")]
    items = {
        str(item.id): item
        for item in Item.objects.filter(tenant_id=tenant_id, id__in=ids)
    }
    warnings: dict[str, list[ItemWarning]] = {}
    for warning in ItemWarning.objects.filter(
        tenant_id=tenant_id,
        active=True,
        severity=WarningSeverity.BLOCKING,
        item_id__in=ids,
    ):
        warnings.setdefault(str(warning.item_id), []).append(warning)
    extraction = filters.Extraction(symptoms)

    kept, dropped = [], []
    for card in rows:
        key = str(card.get("item_id") or "")
        item = items.get(key)
        if item is None:
            dropped.append(_drop(key, "", pipeline.EXCLUDED_INACTIVE))
            continue
        reason = _hard_reason(item)
        if reason is not None:
            dropped.append(_drop(key, item.name, reason))
            continue
        blocking = next(
            (
                warning
                for warning in warnings.get(key, [])
                if filters.evaluate(warning.triggers, extraction) == filters.SATISFIED
            ),
            None,
        )
        if blocking is not None:
            dropped.append(
                _drop(key, item.name, pipeline.EXCLUDED_WARNING, blocking.id)
            )
            continue
        checked = dict(card)
        checked["price"] = _money(card.get("price"))
        checked["client_uuid"] = _client_uuid(card.get("client_uuid"))
        checked["type"] = _type(card.get("type"))
        # **The name comes from the catalog and never from the till.** It is
        # what the prompt names the product by and what the output check's
        # allowlist is built from, so a client that sent none -- and none does,
        # because a name on the wire is a name that can disagree with the
        # catalog -- would leave the check with an empty allowlist and flag the
        # very product it offered.
        checked["item_name"] = item.name
        checked["presentation"] = item.presentation
        kept.append(checked)
    return kept, dropped


def _hard_reason(item):
    if not item.active:
        return pipeline.EXCLUDED_INACTIVE
    if item.requires_prescription:
        return pipeline.EXCLUDED_PRESCRIPTION
    if item.controlled:
        return pipeline.EXCLUDED_CONTROLLED
    if item.invima_status == "expired":
        return pipeline.EXCLUDED_INVIMA_EXPIRED
    return None


def _drop(item_id, item_name, reason, warning_id=None):
    return {
        "item_id": item_id,
        "item_name": item_name,
        "reason": reason,
        "warning_id": str(warning_id) if warning_id else None,
    }


def _money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as bad:
        raise Refused("El precio de una sugerencia no es un número.") from bad
    if amount < 0:
        raise Refused("El precio de una sugerencia no puede ser negativo.")
    return amount.quantize(Decimal("0.01"))


def _client_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as bad:
        raise Refused("Cada sugerencia lleva su propio identificador.") from bad


def _type(value):
    if value not in SuggestionType.values:
        raise Refused(f"«{value}» no es un tipo de sugerencia.")
    return value


# ---------------------------------------------------------------------------
# The acceptance
# ---------------------------------------------------------------------------


def accept(suggestion: AssistantSuggestion, *, sale_line: SaleLine, at=None):
    """Record acceptance, and flag the line, **in one transaction**.

    A batch that applies the line and not the flag is a batch that under-reports
    the assistant forever, so the two happen together or not at all.

    **Idempotent.** A second call with the same line returns the row unchanged;
    a call naming a different line is refused, because `UNIQUE (tenant_id,
    sale_line_id)` is what keeps the numerator honest and a silent overwrite
    would move one line's credit from one card to another.
    """
    if suggestion.sale_line_id is not None:
        if str(suggestion.sale_line_id) != str(sale_line.id):
            raise Refused(
                "Esta sugerencia ya está acreditada a otra línea del tiquete."
            )
        return suggestion
    if suggestion.item_id != sale_line.item_id:
        raise Refused("La línea del tiquete no corresponde al producto sugerido.")
    with transaction.atomic():
        held = (
            AssistantSuggestion.objects.select_for_update()
            .filter(id=suggestion.id)
            .first()
        )
        if held is None:
            raise Refused("La sugerencia ya no existe.")
        if held.sale_line_id is not None:
            if str(held.sale_line_id) != str(sale_line.id):
                raise Refused(
                    "Esta sugerencia ya está acreditada a otra línea del tiquete."
                )
            return held
        held.accepted = True
        held.accepted_at = at or timezone.now()
        held.sale_line = sale_line
        held.save(update_fields=["accepted", "accepted_at", "sale_line", "updated_at"])
        # **The one column S8 writes on S4's table** (ledger, disputed columns).
        SaleLine.objects.filter(id=sale_line.id).update(from_suggestion=True)
    return held


def attach(query: AssistantQuery, *, sale):
    """Name the ticket the question was asked against.

    **It is a second event and not a field on the first**, because the sale does
    not exist on the server when the offer is recorded: an open ticket's header
    reaches the server on the ordinary delta cadence and a till that was offline
    pushes it at close, after the offer it was asked during. The till sends this
    once the sale is on its way, and `client_uuid` ordering puts it behind the
    sale it names.
    """
    if query.sale_id is not None:
        return query
    query.sale = sale
    query.save(update_fields=["sale", "updated_at"])
    return query


def supersede(query: AssistantQuery, *, at=None):
    """The cashier re-asked on the same open sale.

    Its **un-accepted** suggestions leave the denominator; the accepted ones
    stay, because a line on the ticket is a line the assistant put there
    whatever was asked afterwards.
    """
    if query.superseded_at is not None:
        return query
    query.superseded_at = at or timezone.now()
    query.save(update_fields=["superseded_at", "updated_at"])
    return query


# ---------------------------------------------------------------------------
# The three definitions S9 reads and does not recompute
# ---------------------------------------------------------------------------


def offered_population(tenant_id, *, opened, closed, location_ids=None):
    """**The one population predicate, written once and read three ways.**

    Suggestions on queries that were not superseded, attached to sales that
    closed at the counter, inside the period by `recorded_at` (rule 8).

    `sales.source = 'counter'` is named here and nowhere else, which is what
    check 11 reads: an imported sale from the previous system was never offered
    a suggestion, and letting it into the denominator makes the rate look worse
    in exactly the months S6's history loader covers.
    """
    rows = AssistantSuggestion.objects.filter(
        tenant_id=tenant_id,
        recorded_at__gte=opened,
        recorded_at__lt=closed,
        query__superseded_at__isnull=True,
        query__sale__isnull=False,
        query__sale__status=SaleStatus.CLOSED,
        query__sale__source=SaleSource.COUNTER,
    )
    if location_ids is not None:
        rows = rows.filter(location_id__in=list(location_ids))
    return rows


def acceptance(tenant_id, *, opened, closed, location_ids=None) -> dict:
    """`{offered, accepted, rate}` -- the Panel's headline tile, from one read."""
    rows = offered_population(
        tenant_id, opened=opened, closed=closed, location_ids=location_ids
    )
    figures = rows.aggregate(
        offered=Count("id"), accepted=Count("id", filter=Q(accepted=True))
    )
    offered = int(figures["offered"] or 0)
    accepted = int(figures["accepted"] or 0)
    return {
        "offered": offered,
        "accepted": accepted,
        # **Null and not zero** where nothing was offered (§B.9.2 tier 3): a
        # rate of 0% is a claim about a period, and no denominator is not.
        "rate": (accepted / offered) if offered else None,
    }


def ticket_comparison(tenant_id, *, opened, closed, location_ids=None) -> dict:
    """The mean `sales.total` over closed counter sales, split on whether the
    sale carries at least one line with `from_suggestion`.

    Served by S4's `(tenant_id, sale_id)` index rather than by a partial variant
    on the same columns -- rule 4 creates an index once, and a second one here
    would cost a write on every suggested line to save one filter.
    """
    from core.models import Sale

    sales = Sale.objects.filter(
        tenant_id=tenant_id,
        status=SaleStatus.CLOSED,
        source=SaleSource.COUNTER,
        recorded_at__gte=opened,
        recorded_at__lt=closed,
    )
    if location_ids is not None:
        sales = sales.filter(location_id__in=list(location_ids))
    suggested = Q(lines__from_suggestion=True)
    with_suggestion = sales.filter(suggested).distinct()
    without = sales.exclude(suggested).distinct()
    return {
        "suggested_tickets": with_suggestion.count(),
        "suggested_mean": with_suggestion.aggregate(mean=Avg("total"))["mean"],
        "plain_tickets": without.count(),
        "plain_mean": without.aggregate(mean=Avg("total"))["mean"],
    }


def combinations(tenant_id, *, opened, closed, location_ids=None, limit=5):
    """*Combinaciones más aceptadas*, **at category grain on both sides**.

    A combination is the ordered pair (anchor category, suggestion category),
    where the anchor is the category of the item on the sale's lowest-`position`
    line carrying `from_suggestion = false` -- the reference the customer came
    in for -- and the suggestion side is the category of each accepted
    `first_choice` or `bought_together` suggestion on that same sale.

    The category on each side is the item's own `items.category_id`, rendered as
    that row's `categories.name` and **never rolled up to a `parent_id`**:
    *antidiarreico*, *protector gástrico* and *vitamina C* are the grain the
    handoff's own labels sit at, and a roll-up to the parent makes every row
    read *Medicamentos + Medicamentos*.

    One sale contributes each distinct pair once however many lines carry it,
    and a pair whose two sides fall in the same category is not a combination.
    Ranking is by the number of sales carrying the pair, descending; ties break
    on the accepted-suggestion count inside those sales, then on the rendered
    label alphabetically -- so two runs over one period return the same rows in
    the same order.
    """
    rows = offered_population(
        tenant_id, opened=opened, closed=closed, location_ids=location_ids
    ).filter(
        accepted=True,
        type__in=(SuggestionType.FIRST_CHOICE, SuggestionType.BOUGHT_TOGETHER),
    )
    accepted = list(
        rows.values(
            "query__sale_id",
            "item__category_id",
            "item__category__name",
        )
    )
    if not accepted:
        return []

    sale_ids = {str(row["query__sale_id"]) for row in accepted}
    anchors = _anchor_categories(tenant_id, sale_ids)

    per_sale: dict[str, set[tuple[str, str]]] = {}
    weight: dict[tuple[str, str], int] = {}
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    for row in accepted:
        sale_id = str(row["query__sale_id"])
        anchor = anchors.get(sale_id)
        suggestion = row["item__category_id"]
        if anchor is None or suggestion is None:
            continue
        pair = (str(anchor[0]), str(suggestion))
        if pair[0] == pair[1]:
            continue
        per_sale.setdefault(sale_id, set()).add(pair)
        weight[pair] = weight.get(pair, 0) + 1
        labels[pair] = (anchor[1], row["item__category__name"] or "")

    sales_carrying: dict[tuple[str, str], int] = {}
    for pairs in per_sale.values():
        for pair in pairs:
            sales_carrying[pair] = sales_carrying.get(pair, 0) + 1

    ordered = sorted(
        sales_carrying,
        key=lambda pair: (
            -sales_carrying[pair],
            -weight.get(pair, 0),
            _collated(f"{labels[pair][0]} + {labels[pair][1]}"),
        ),
    )
    return [
        {
            "anchor_category_id": pair[0],
            "anchor": labels[pair][0],
            "suggestion_category_id": pair[1],
            "suggestion": labels[pair][1],
            "label": f"{labels[pair][0]} + {labels[pair][1]}",
            "sales": sales_carrying[pair],
            "accepted": weight.get(pair, 0),
        }
        for pair in ordered[:limit]
    ]


def _anchor_categories(tenant_id, sale_ids):
    """The category of each sale's lowest-`position` unsuggested line.

    That line is **the reference the customer came in for**, which is what makes
    the pair directional and what makes *"Suero oral + antidiarreico"* read the
    way round the handoff draws it.
    """
    rows = (
        SaleLine.objects.filter(
            tenant_id=tenant_id, sale_id__in=list(sale_ids), from_suggestion=False
        )
        .order_by("sale_id", "position")
        .values("sale_id", "position", "item__category_id", "item__category__name")
    )
    anchors: dict[str, tuple[str, str]] = {}
    for row in rows:
        key = str(row["sale_id"])
        if key in anchors or row["item__category_id"] is None:
            continue
        anchors[key] = (
            str(row["item__category_id"]),
            row["item__category__name"] or "",
        )
    return anchors


def _collated(label: str) -> str:
    """Spanish collation, as far as a tie-break needs it: accents fold, case
    folds, and `ñ` sorts after `n` -- which `unicodedata` gives for free once
    the accents are gone."""
    from core.assistant.vocabulary import fold

    return fold(label)
