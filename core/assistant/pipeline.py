"""The candidate pipeline: extract, seed, filter, rank, label.

Five steps, deterministic, **identical online and offline** (A8). The model
participates in exactly one of them, and it is not the one that chooses
products -- so this module has no gateway import in it at all.

**This is the server's copy, and the till has its own** in
`web/src/assistant/pipeline.ts`. The duplication is inherent rather than
incidental: the whole claim of this stage is a counter whose fibre is cut, and a
pipeline that only ran on a server would make every criterion in *Offline*
false. What is **not** duplicated is the data the two read -- the lexicon, the
vocabulary, the symptom map, the reason strings and the two till-facing
settings all reach the device through `GET /api/assistant/bundle`, so there is
one copy of every Spanish sentence and one closed vocabulary in the product.

This copy is what the demo fixture builds its offers from, what the suite
asserts the filter on, and what `POST /api/assistant/queries` re-runs the hard
half of over whatever a client sent. That last one matters: **the server never
trusts a client's candidate list about prescription, controlled or expired
stock**. A till that is out of date about the catalog would otherwise be able to
offer what §7 says is never offered, and the check would pass on every screen
anyone looked at.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Q, Sum

from core.assistant import filters, reasons
from core.assistant.vocabulary import POPULATIONS, SYMPTOMS, fold
from core.catalog import prices as price_service
from core.models import (
    Category,
    CrossSellConfidence,
    CrossSellRule,
    InvimaStatus,
    Item,
    ItemWarning,
    StockOnHand,
    SuggestionType,
    WarningSeverity,
)

#: Step 4's seed strengths, in the order *Rank, and label* fixes them.
EXACT_CATEGORY = Decimal("1.0")
INGREDIENT_MATCH = Decimal("0.8")
NAME_MATCH = Decimal("0.5")

#: Why an item was removed, stamped into `assistant_queries.excluded`. English,
#: like every other code in the product, and read by criterion 5.
EXCLUDED_INACTIVE = "inactive"
EXCLUDED_OUT_OF_STOCK = "out_of_stock"
EXCLUDED_PRESCRIPTION = "requires_prescription"
EXCLUDED_CONTROLLED = "controlled"
EXCLUDED_INVIMA_EXPIRED = "invima_expired"
EXCLUDED_WARNING = "warning_blocking"


@dataclass
class Candidate:
    """One item that survived the filter, with everything a card needs."""

    item: Item
    seed_strength: Decimal
    seed_keys: set[str] = field(default_factory=set)
    available_quantity: int = 0
    price: Decimal | None = None
    #: Set where the item was reached through a rule rather than from a symptom.
    rule: CrossSellRule | None = None
    anchor_name: str = ""
    #: The warning that makes this a `conditional` card, and its outcome.
    warning: ItemWarning | None = None
    warning_outcome: str = filters.IRRELEVANT
    #: Set where a higher-ranked candidate for the same symptom was dropped for
    #: want of stock at this sede.
    substitute: bool = False
    from_ticket: bool = False

    @property
    def lift(self) -> Decimal:
        return self.rule.lift if self.rule is not None else Decimal("0")


@dataclass
class Outcome:
    """What one query produced, before any prose exists."""

    candidates: list[Candidate]
    excluded: list[dict]
    cards: list[dict]
    seeded_count: int
    unmapped_keys: list[str]

    @property
    def candidate_count(self) -> int:
        """The `3 de 12 referencias` denominator: what survived the filter."""
        return len(self.candidates)


def run(
    *,
    tenant_id,
    location_id,
    symptoms,
    settings,
    ticket_item_ids=(),
    today=None,
) -> Outcome:
    """The whole pipeline for one query, against one sede's own shelf."""
    extraction = filters.Extraction(symptoms)
    day = today or price_service.today()

    seeded, unmapped = seed(tenant_id, extraction, settings)
    reached = _through_rules(tenant_id, location_id, seeded, ticket_item_ids)
    everything = {**seeded, **{k: v for k, v in reached.items() if k not in seeded}}

    survivors, excluded = filter_candidates(
        tenant_id=tenant_id,
        location_id=location_id,
        candidates=everything,
        extraction=extraction,
        day=day,
    )
    ranked = rank(survivors)
    _mark_substitutes(ranked, _out_of_stock_ingredients(everything, excluded))
    cards = compose(ranked, settings)
    return Outcome(
        candidates=ranked,
        excluded=excluded,
        cards=cards,
        seeded_count=len(seeded),
        unmapped_keys=unmapped,
    )


# ---------------------------------------------------------------------------
# 2 · seed
# ---------------------------------------------------------------------------


def seed(tenant_id, extraction: filters.Extraction, settings):
    """Each `symptom_key` to a set of `categories`, then the active items in them.

    **Where a key is unmapped the seed falls back to matching its Spanish
    surface forms** against `items.name`, `active_ingredient` and
    `categories.name` -- weaker, and **counted**, so an unmapped vocabulary
    shows up in Ajustes rather than as a quiet drop in quality.

    The fallback covers a *key* the map does not cover. It does not stand in for
    a map that is entirely empty, because a fallback that fires on every key is
    not a fallback -- which is why `GET /api/assistant/queries`' own empty state
    distinguishes the two and why the seed ships the map populated.
    """
    mapping = settings.get("symptom_category_map") or {}
    keys = sorted(extraction.symptoms)
    if not keys:
        return {}, []

    mapped: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for key in keys:
        categories = [str(one) for one in (mapping.get(key) or [])]
        if categories:
            mapped[key] = categories
        else:
            unmapped.append(key)

    candidates: dict[str, Candidate] = {}
    if mapped:
        wanted = {one for categories in mapped.values() for one in categories}
        rows = Item.objects.filter(
            tenant_id=tenant_id, active=True, category_id__in=wanted
        ).select_related("category")
        by_category: dict[str, list[str]] = {}
        for key, categories in mapped.items():
            for category in categories:
                by_category.setdefault(category, []).append(key)
        for item in rows:
            _note(
                candidates,
                item,
                EXACT_CATEGORY,
                by_category.get(str(item.category_id), []),
            )

    for key in unmapped:
        for item, strength in _by_surface_form(tenant_id, key):
            _note(candidates, item, strength, [key])
    return candidates, unmapped


def _note(candidates, item, strength, keys):
    held = candidates.get(str(item.id))
    if held is None:
        candidates[str(item.id)] = Candidate(
            item=item, seed_strength=strength, seed_keys=set(keys)
        )
        return
    held.seed_strength = max(held.seed_strength, strength)
    held.seed_keys.update(keys)


def _by_surface_form(tenant_id, key):
    """The weaker seed: what the words themselves match in the catalog.

    Ingredient and category matches score 0,8; a name match scores 0,5 -- the
    same three strengths *Rank, and label* fixes, so an unmapped key produces a
    worse-ordered list and never an unordered one.
    """
    label, forms = SYMPTOMS.get(key, ("", ()))
    terms = {fold(one) for one in (label, *forms) if one}
    if not terms:
        return []
    ingredient = Q()
    named = Q()
    by_category = Q()
    for term in terms:
        ingredient |= Q(active_ingredient__icontains=term)
        named |= Q(search_name__contains=term)
        # `categories` carries no generated `search_name` -- S1 gave one to
        # `items` and `manufacturers`, which are what the counter searches -- so
        # the fallback folds on the database's own case-insensitive match rather
        # than on a column that is not there.
        by_category |= Q(name__icontains=term)
    categories = list(
        Category.objects.filter(tenant_id=tenant_id)
        .filter(by_category)
        .values_list("id", flat=True)
    )
    found: list[tuple[Item, Decimal]] = []
    strong = Item.objects.filter(tenant_id=tenant_id, active=True).filter(
        ingredient | Q(category_id__in=categories)
    )
    found.extend((item, INGREDIENT_MATCH) for item in strong[:200])
    seen = {item.id for item, _ in found}
    weak = Item.objects.filter(tenant_id=tenant_id, active=True).filter(named)
    found.extend((item, NAME_MATCH) for item in weak[:200] if item.id not in seen)
    return found


def _through_rules(tenant_id, location_id, seeded, ticket_item_ids):
    """Step 2's other half: what a `cross_sell_rule` reaches from here.

    Anchors are the symptom-seeded items **and the lines already on the ticket**
    -- the second is what `ticket_companion` is, and it is why a suggestion
    appears for a customer who described nothing and scanned one box.

    **The sede's rule wins over the network's**, which is what makes *"en este
    punto el 64%"* a true sentence rather than a network claim.
    """
    anchors = sorted(
        {uuid.UUID(str(one)) for one in seeded}
        | {uuid.UUID(str(one)) for one in ticket_item_ids}
    )
    if not anchors:
        return {}
    rows = list(
        CrossSellRule.objects.filter(tenant_id=tenant_id, item_a_id__in=anchors)
        .filter(Q(location_id=location_id) | Q(location_id__isnull=True))
        .select_related("item_b", "item_a")
        .order_by("-lift")
    )
    best: dict[str, CrossSellRule] = {}
    for rule in rows:
        key = str(rule.item_b_id)
        held = best.get(key)
        if held is None:
            best[key] = rule
            continue
        # A sede row beats a network row whatever their lifts say; between two
        # rows at one scope the higher lift wins, which the ordering already did.
        if held.location_id is None and rule.location_id is not None:
            best[key] = rule
    reached: dict[str, Candidate] = {}
    ticket = {str(one) for one in ticket_item_ids}
    for key, rule in best.items():
        if key in seeded or key in ticket:
            continue
        reached[key] = Candidate(
            item=rule.item_b,
            seed_strength=Decimal("0"),
            rule=rule,
            anchor_name=rule.item_a.name,
            from_ticket=str(rule.item_a_id) in ticket,
        )
    return reached


# ---------------------------------------------------------------------------
# 3 · filter
# ---------------------------------------------------------------------------


def filter_candidates(*, tenant_id, location_id, candidates, extraction, day):
    """The five steps of *3 · Filter*, in order, before anything is ranked.

    Every removal is recorded, because criterion 5 is read off `excluded` and
    not off a screen: *"reading the query back shows the item in `excluded`
    naming the `item_warnings` row that removed it"*.
    """
    if not candidates:
        return [], []
    ids = list(candidates)
    held = _on_hand(tenant_id, location_id, ids, day)
    warnings = _warnings(tenant_id, ids)

    survivors: list[Candidate] = []
    excluded: list[dict] = []

    def drop(candidate, reason, warning_id=None):
        excluded.append(
            {
                "item_id": str(candidate.item.id),
                "item_name": candidate.item.name,
                "reason": reason,
                "warning_id": str(warning_id) if warning_id else None,
            }
        )

    for key, candidate in candidates.items():
        item = candidate.item
        if not item.active:
            drop(candidate, EXCLUDED_INACTIVE)
            continue
        quantity = held.get(key, 0)
        # A service (`tracks_stock = false`, A7) is eligible and skips the stock
        # test: there is nothing on a shelf to be out of.
        if item.tracks_stock and quantity <= 0:
            drop(candidate, EXCLUDED_OUT_OF_STOCK)
            continue
        if item.requires_prescription:
            # **Never suggested** (§7). It stays sellable through S4's search by
            # a person who has seen the prescription; the assistant simply never
            # proposes it.
            drop(candidate, EXCLUDED_PRESCRIPTION)
            continue
        if item.controlled:
            drop(candidate, EXCLUDED_CONTROLLED)
            continue
        if item.invima_status == InvimaStatus.EXPIRED:
            # Botica does not block the **sale** of a lapsed registration -- it
            # surfaces the state and records the pharmacy's decision -- but it
            # declines to **recommend** one. Suggesting is not selling.
            drop(candidate, EXCLUDED_INVIMA_EXPIRED)
            continue

        blocked = None
        conditional = None
        for warning in warnings.get(key, []):
            outcome = filters.evaluate(warning.triggers, extraction)
            if (
                outcome == filters.SATISFIED
                and warning.severity == WarningSeverity.BLOCKING
            ):
                blocked = warning
                break
            if conditional is None and filters.makes_conditional(
                warning.type, warning.severity, outcome
            ):
                conditional = (warning, outcome)
        if blocked is not None:
            drop(candidate, EXCLUDED_WARNING, blocked.id)
            continue
        if conditional is not None:
            candidate.warning, candidate.warning_outcome = conditional

        candidate.available_quantity = quantity
        candidate.price = _price(item, location_id, day)
        if candidate.price is None:
            # A card with no price has no `Agregar` behind it: S4 refuses the
            # line, so offering it would be offering something the till cannot
            # put on the ticket.
            drop(candidate, EXCLUDED_OUT_OF_STOCK)
            continue
        survivors.append(candidate)
    return survivors, excluded


def _on_hand(tenant_id, location_id, item_ids, day):
    """This sede's units, **excluding lots already expired**.

    A lot whose expiry has passed is stock the sede holds and must not sell, so
    it is out of the availability figure a card prints as well as out of the
    filter that decides whether a card exists.
    """
    rows = (
        StockOnHand.objects.filter(
            tenant_id=tenant_id, location_id=location_id, item_id__in=item_ids
        )
        .filter(
            Q(lot__isnull=True)
            | Q(lot__expires_at__isnull=True)
            | Q(lot__expires_at__gt=day)
        )
        # **The model's ordering is dropped deliberately, and it is not cosmetic
        # here.** `stock_on_hand` orders by `location__name, item__name`, which
        # on a `values().annotate()` joins both tables in and adds both names to
        # the GROUP BY -- a three-way join and a sort, per question, to build a
        # dictionary nothing reads in order. The same applies to every read on
        # this path.
        .order_by()
        .values("item_id")
        .annotate(quantity=Sum("quantity"))
    )
    return {str(row["item_id"]): int(row["quantity"] or 0) for row in rows}


def _warnings(tenant_id, item_ids):
    # The model orders by `item__name, type`, and the filter loop below takes
    # the **first** blocking warning and the first conditional one -- so the
    # order decides which `item_warnings` row an exclusion names, and dropping
    # it outright would make that answer differ between two identical queries.
    # `type, id` is the same sequence within one item, which is the only grain
    # this dictionary is read at, and it sorts on this table's own columns
    # instead of joining `items` once per question to reach a name.
    rows = ItemWarning.objects.filter(
        tenant_id=tenant_id, active=True, item_id__in=item_ids
    ).order_by("type", "id")
    held: dict[str, list[ItemWarning]] = {}
    for warning in rows:
        held.setdefault(str(warning.item_id), []).append(warning)
    return held


def _price(item, location_id, day):
    row = price_service.in_force(item.id, location_id=location_id, on=day)
    return None if row is None else row.price


# ---------------------------------------------------------------------------
# 4 · rank, and label
# ---------------------------------------------------------------------------


def _out_of_stock_ingredients(candidates, excluded) -> set[str]:
    """The molecules this sede had nothing of, folded to one spelling."""
    dropped = {
        one["item_id"] for one in excluded if one["reason"] == EXCLUDED_OUT_OF_STOCK
    }
    return {
        _molecule(candidate.item)
        for key, candidate in candidates.items()
        if key in dropped and _molecule(candidate.item)
    }


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """Seed strength, then `lift`, then units descending, then price ascending.

    **The ranker deliberately reads no `demand_forecasts`.** S6 and S8 run in
    parallel off S4 (§13), and a ranker that needs a forecast cannot be
    demonstrated until S6 lands. The cost of being wrong is that the first card
    is occasionally a slow mover the sede happens to be long on, which is a
    worse suggestion and never an unsafe one -- and the fix is one term added to
    the score once `demand_forecasts` exists.

    The trailing `name` is not a tie-break anybody reads: it is what makes two
    runs over one shelf return one order, which criterion 20 and the seed's own
    determinism both rest on.
    """
    return sorted(
        candidates,
        key=lambda one: (
            -one.seed_strength,
            -one.lift,
            -one.available_quantity,
            one.price if one.price is not None else Decimal("0"),
            one.item.name,
        ),
    )


def _mark_substitutes(ranked, dropped_ingredients):
    """`substitute_available` -- **the last box of its molecule on the shelf.**

    That is what a *sustituto* is at a counter: the presentation the sede
    normally sells is out and this is the one still there. It is deliberately
    narrower than *anything ranked below something out of stock* -- on a catalog
    of four thousand references, some box of some molecule is always out, and a
    reason code that fires on every first card is a reason code that says
    nothing.
    """
    if not dropped_ingredients:
        return
    surviving: dict[str, int] = {}
    for candidate in ranked:
        ingredient = _molecule(candidate.item)
        if ingredient:
            surviving[ingredient] = surviving.get(ingredient, 0) + 1
    for candidate in ranked:
        ingredient = _molecule(candidate.item)
        if (
            ingredient
            and ingredient in dropped_ingredients
            and surviving.get(ingredient) == 1
        ):
            candidate.substitute = True


def _molecule(item) -> str:
    return (item.active_ingredient or "").strip().lower()


def type_of(candidate: Candidate) -> str:
    """`suggestion_type`, **derived and never chosen by a model**."""
    if candidate.warning is not None:
        return SuggestionType.CONDITIONAL
    if candidate.rule is not None:
        return SuggestionType.BOUGHT_TOGETHER
    return SuggestionType.FIRST_CHOICE


def reason_for(candidate: Candidate, *, first: bool) -> tuple[str, str]:
    """`(reason_code, reason)` -- the code, and the Spanish sentence it renders.

    A `conditional` card's reason **is** the warning's own `text`, verbatim. It
    is never templated and never rewritten, because a safety string a model
    paraphrases has stopped being a safety string.
    """
    if candidate.warning is not None:
        return "warning_conditional", candidate.warning.text
    rule = candidate.rule
    if rule is not None:
        if candidate.from_ticket:
            return "ticket_companion", reasons.line("ticket_companion")
        if rule.location_id is None:
            return "bought_together_network", reasons.line(
                "bought_together_network", anchor=candidate.anchor_name
            )
        if rule.confidence_band == CrossSellConfidence.LOW:
            # A percentage carried to two significant figures out of forty
            # tickets is a false precision, and it is read out loud to a
            # customer.
            return "bought_together_location", reasons.line(
                "bought_together_location_low", anchor=candidate.anchor_name
            )
        return "bought_together_location", reasons.line(
            "bought_together_location",
            share=reasons.share(rule.confidence),
            anchor=candidate.anchor_name,
        )
    if candidate.substitute:
        return "substitute_available", reasons.line("substitute_available")
    if first:
        key = sorted(candidate.seed_keys)[0] if candidate.seed_keys else ""
        return "symptom_primary", reasons.line("symptom_primary", symptom_key=key)
    return "symptom_secondary", reasons.line("symptom_secondary")


def compose(ranked: list[Candidate], settings) -> list[dict]:
    """The drawn set: **one card per type, in the order the handoff draws them.**

    `first_choice`, then `conditional`, then `bought_together`, then -- only
    where `suggestion_card_count` is above three -- the next-best of each in the
    same order. At the default of three that is exactly the handoff's row, and
    it is what makes *Sin reglas todavía* leave the third slot **empty** rather
    than filling it with a second analgesic: a type with no candidate does not
    appear, and nothing occupies its place.
    """
    limit = int(settings.get("suggestion_card_count", 3) or 3)
    buckets: dict[str, list[Candidate]] = {
        SuggestionType.FIRST_CHOICE: [],
        SuggestionType.CONDITIONAL: [],
        SuggestionType.BOUGHT_TOGETHER: [],
    }
    for candidate in ranked:
        buckets[type_of(candidate)].append(candidate)

    order = [
        SuggestionType.FIRST_CHOICE,
        SuggestionType.CONDITIONAL,
        SuggestionType.BOUGHT_TOGETHER,
    ]
    # **The first three slots are one per type, and a type with no candidate
    # leaves its slot empty.** Nothing is promoted into it: *Sin reglas todavía*
    # is a card C with two cards and no third, which is a new tenant's normal
    # first state rather than an error state (§1). A build agent that filled the
    # gap with a second analgesic would make the cold-start path invisible
    # exactly where the product most needs it seen.
    drawn = [buckets[kind][0] for kind in order if buckets[kind]][:limit]
    # Only **above** the three types -- and only where an administrator asked
    # for more cards than there are types -- does the list carry a further
    # candidate, which is what `symptom_secondary` is. An empty type slot is
    # never backfilled: two cards and no third is the whole of *Sin reglas
    # todavía*.
    extra = limit - len(order)
    if extra > 0:
        held = {id(one) for one in drawn}
        for candidate in ranked:
            if extra <= 0:
                break
            if id(candidate) not in held:
                drawn.append(candidate)
                held.add(id(candidate))
                extra -= 1

    cards = []
    for position, candidate in enumerate(drawn, start=1):
        code, reason = reason_for(
            candidate, first=position == 1 or candidate.warning is not None
        )
        cards.append(
            {
                "item_id": str(candidate.item.id),
                "item_name": candidate.item.name,
                "presentation": candidate.item.presentation,
                "type": type_of(candidate),
                "reason_code": code,
                "reason": reason,
                "price": candidate.price,
                "rank": position,
                "available_quantity": candidate.available_quantity,
                "warning_id": (
                    str(candidate.warning.id) if candidate.warning is not None else None
                ),
                "rule_confidence": (
                    candidate.rule.confidence_band
                    if candidate.rule is not None
                    else None
                ),
                "ruleset_computed_at": (
                    candidate.rule.computed_at if candidate.rule is not None else None
                ),
            }
        )
    return cards


def newest_ruleset(cards):
    """The freshest `computed_at` behind any drawn card, for the query's stamp."""
    stamps = [
        card["ruleset_computed_at"] for card in cards if card.get("ruleset_computed_at")
    ]
    return max(stamps) if stamps else None


def local_prose(cards, candidate_count) -> tuple[str, str]:
    """Card B, written from the same ranking by a template over `reason_code`.

    **In fewer words, and with no cross-sell sentence where there are no rules**
    -- the handoff's own *"La sede tiene las tres referencias disponibles."* is
    true on a first morning and its *"el 64% de los clientes…"* is not.
    """
    first = next(
        (card for card in cards if card["type"] == SuggestionType.FIRST_CHOICE), None
    )
    conditional = next(
        (card for card in cards if card["type"] == SuggestionType.CONDITIONAL), None
    )
    if first is not None:
        primary = reasons.LOCAL_PRIMARY_FIRST.format(item=first["item_name"])
    elif conditional is not None:
        primary = reasons.LOCAL_PRIMARY_CONDITIONAL
    else:
        primary = reasons.LOCAL_PRIMARY_NONE

    pair = next(
        (card for card in cards if card["type"] == SuggestionType.BOUGHT_TOGETHER), None
    )
    if pair is not None and pair["reason_code"] != "ticket_companion":
        anchor = _anchor_of(pair)
        secondary = reasons.LOCAL_SECONDARY_PAIR.format(
            item=pair["item_name"], anchor=anchor
        )
    elif candidate_count == 1:
        secondary = reasons.LOCAL_SECONDARY_ONE
    elif candidate_count > 1:
        secondary = reasons.LOCAL_SECONDARY_MANY.format(count=candidate_count)
    else:
        secondary = ""
    return primary, secondary


def _anchor_of(card) -> str:
    """The anchor's name, read back out of the reason the pipeline just wrote.

    The card carries the sentence and not the row, and card B's second register
    names the same anchor its own card does -- so it is taken from there rather
    than from a second lookup that could disagree with it.
    """
    reason = card.get("reason") or ""
    for marker in (" con ", " junto con "):
        if marker in reason:
            return reason.rsplit(marker, 1)[1].split(" en esta sede")[0].strip()
    return ""


def empty_body(candidate_count, seeded_count) -> str:
    """Card C's second line: a **stock** statement, never a history one."""
    if seeded_count == 0:
        return reasons.EMPTY_BODY_NONE
    if seeded_count == 1:
        return reasons.EMPTY_BODY_ONE
    del candidate_count
    return reasons.EMPTY_BODY_MANY.format(count=seeded_count)


def population_label(key) -> str:
    return POPULATIONS.get(key, (key, ()))[0]
