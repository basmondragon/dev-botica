"""Step 5: the one step the model is in, and the check on the way out.

**The model is constrained twice -- once in the prompt and once on the way out
-- because a prompt constraint on its own is a hope.** A recommendation that
names a condition, diagnoses, contradicts a medicine the customer said they are
on, or names a product that was not in the candidate list is discarded and never
reaches the screen, and the cashier sees the local recommendation instead of an
error.

**What is sent:** the extracted symptom set, the ordered candidate list -- id,
name, presentation, `available_quantity`, price, `type`, `reason_code` -- and
the cross-sell figures behind any `bought_together` candidate. **What is not:**
the customer's identity, `customers` in any form, the ticket's other lines'
prices, the tenant's name, or anything from `item_warnings` beyond the
`conditional` cards' own text. **The transcript is sent only where
`retain_transcripts` permits it and `model_enabled` is true**; where it is not,
the model receives the extracted keys and labels and nothing verbatim.

**What may come back:** `recommendation` (≤ 180 characters),
`recommendation_secondary` (≤ 220), and an optional `reason` per candidate id.
Nothing else is read. It may not add, remove, reorder or re-price anything, and
**a `conditional` card's reason is never rewritten** -- a safety string a model
paraphrases has stopped being a safety string.

A response that adds an id, omits a required field, exceeds a cap or is not
parseable is a rejection under the same path as any other. **There is one
failure treatment and it is the local fallback**, because an error at a counter
with a customer waiting is worse than a quieter card (§B.10.3).
"""

import json
import logging
import time

from core import gateway
from core.assistant.vocabulary import (
    CONDITION_EXPRESSIONS,
    DIAGNOSTIC_EXPRESSIONS,
    INGREDIENTS,
    TREATMENT_CHANGE_EXPRESSIONS,
    fold,
)
from core.models import SuggestionType

logger = logging.getLogger(__name__)

RECOMMENDATION_MAX = 180
SECONDARY_MAX = 220
REASON_MAX = 140

#: The flags the check writes into `output_check_flags`. English codes, so a
#: rate can be grouped without parsing a sentence.
FLAG_UNPARSEABLE = "unparseable"
FLAG_SHAPE = "shape"
FLAG_LENGTH = "length"
FLAG_UNKNOWN_ITEM = "unknown_item"
FLAG_CONDITION_NAMED = "condition_named"
FLAG_DIAGNOSTIC_PHRASING = "diagnostic_phrasing"
FLAG_TREATMENT_CHANGE = "treatment_change"
FLAG_REWROTE_WARNING = "rewrote_warning"

SYSTEM = (
    "Eres el asistente de mostrador de una droguería colombiana. Escribes en "
    "español de Colombia, en segunda persona, para una auxiliar que atiende a "
    "una persona que está esperando.\n"
    "Reglas, todas obligatorias:\n"
    "1. No diagnosticas. No nombras enfermedades, infecciones, virus ni "
    "cuadros clínicos, ni siquiera como posibilidad.\n"
    "2. Solo puedes hablar de los productos de la lista que recibes. No "
    "agregas, quitas, reordenas ni cambias precios.\n"
    "3. Nunca sugieres suspender, cambiar ni reemplazar un tratamiento que la "
    "persona ya toma.\n"
    "4. Respondes únicamente con un objeto JSON con las claves "
    '"recommendation", "recommendation_secondary" y "reasons".\n'
    f"5. «recommendation» tiene máximo {RECOMMENDATION_MAX} caracteres y dice "
    "qué ofrecer primero. «recommendation_secondary» tiene máximo "
    f"{SECONDARY_MAX} caracteres y dice por qué, con la cifra que recibas.\n"
    "6. «reasons» es un objeto {item_id: frase} y solo puede traer los ids que "
    "recibiste. Es opcional y puede venir vacío."
)


def build_prompt(*, symptoms, cards, transcript="", location_name="") -> str:
    """The whole of what the vendor sees, and it is deliberately small."""
    chips = [
        {"key": fact.get("key"), "label": fact.get("label"), "kind": fact.get("kind")}
        for fact in symptoms or []
    ]
    candidates = [
        {
            "item_id": card["item_id"],
            "name": card["item_name"],
            "presentation": card.get("presentation") or "",
            "type": card["type"],
            "reason_code": card["reason_code"],
            "available_quantity": card["available_quantity"],
            "price": str(card["price"]),
            # Only the `conditional` cards' own text, and nothing else from the
            # safety layer: the model is told what the card already says so it
            # does not contradict it, and is told nothing it could paraphrase
            # into a new safety claim.
            "fixed_reason": card["reason"] if card.get("warning_id") else None,
            "pair_share": card.get("pair_share"),
            "pair_anchor": card.get("pair_anchor"),
        }
        for card in cards
    ]
    document = {
        "sede": location_name,
        "sintomas": chips,
        "candidatos": candidates,
    }
    if transcript:
        document["transcripcion"] = transcript
    return json.dumps(document, ensure_ascii=False, default=str)


def ask(*, tenant, settings, symptoms, cards, transcript="", location_name=""):
    """One inline call, under `model_timeout_ms`. Returns `None` where the
    gateway is off, capped or unreachable -- never an exception a caller has to
    treat as an event.

    **It is inline rather than queued** because a queued call would need the
    till to poll for prose it has already rendered a local version of.
    """
    if not gateway.enabled_for(tenant):
        return None
    allowed = bool(settings.get("retain_transcripts")) and bool(
        settings.get("model_enabled")
    )
    timeout = float(settings.get("model_timeout_ms", 4000)) / 1000
    started = time.monotonic()
    try:
        answer = gateway.complete(
            tenant=tenant,
            system=SYSTEM,
            prompt=build_prompt(
                symptoms=symptoms,
                cards=cards,
                transcript=transcript if allowed else "",
                location_name=location_name,
            ),
            max_tokens=600,
            model=settings.get("model") or None,
            timeout=timeout,
        )
    except gateway.Unavailable as unreachable:
        logger.info("assistant gateway unavailable: %s", unreachable)
        return None
    answer["latency_ms"] = int((time.monotonic() - started) * 1000)
    return answer


def parse(text) -> dict | None:
    """The response contract, as a shape rather than as a hope."""
    if not isinstance(text, str):
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def check(
    payload, *, cards, symptoms, known_molecules=()
) -> tuple[bool, list[str], dict]:
    """Applied to every model response **before anything renders**.

    Returns `(passed, flags, cleaned)`. On a failure nothing model-written is
    used: `output_check_passed` is false, `output_check_flags` names which rule
    fired, and the card falls back to the local recommendation with the `MODO
    LOCAL` eyebrow. **The cashier sees no error, no toast and no empty card.**
    """
    flags: list[str] = []
    if payload is None:
        return False, [FLAG_UNPARSEABLE], {}

    primary = payload.get("recommendation")
    secondary = payload.get("recommendation_secondary")
    if not isinstance(primary, str) or not isinstance(secondary, str):
        return False, [FLAG_SHAPE], {}
    primary = primary.strip()
    secondary = secondary.strip()
    if not primary:
        return False, [FLAG_SHAPE], {}
    if len(primary) > RECOMMENDATION_MAX or len(secondary) > SECONDARY_MAX:
        flags.append(FLAG_LENGTH)

    offered = {card["item_id"]: card for card in cards}
    reasons_in = payload.get("reasons") or {}
    if not isinstance(reasons_in, dict):
        return False, [FLAG_SHAPE], {}
    cleaned: dict[str, str] = {}
    for item_id, sentence in reasons_in.items():
        card = offered.get(str(item_id))
        if card is None:
            flags.append(FLAG_UNKNOWN_ITEM)
            continue
        if not isinstance(sentence, str) or len(sentence.strip()) > REASON_MAX:
            flags.append(FLAG_LENGTH)
            continue
        if card.get("warning_id"):
            # **A `conditional` card's reason is never rewritten.** The model
            # was told the fixed text; offering a replacement for it is the
            # response trying to paraphrase a safety string.
            flags.append(FLAG_REWROTE_WARNING)
            continue
        cleaned[str(item_id)] = sentence.strip()

    prose = f"{primary}\n{secondary}\n" + "\n".join(cleaned.values())
    flags.extend(
        _forbidden(
            prose, cards=cards, symptoms=symptoms, known_molecules=known_molecules
        )
    )

    if flags:
        return False, sorted(set(flags)), {}
    return (
        True,
        [],
        {
            "recommendation": primary,
            "recommendation_secondary": secondary,
            "reasons": cleaned,
        },
    )


def _forbidden(prose, *, cards, symptoms, known_molecules) -> list[str]:
    """The four content rules, run over everything the response would render."""
    folded = fold(prose)
    flags = []
    if any(pattern.search(folded) for pattern in CONDITION_EXPRESSIONS):
        flags.append(FLAG_CONDITION_NAMED)
    if any(pattern.search(folded) for pattern in DIAGNOSTIC_EXPRESSIONS):
        flags.append(FLAG_DIAGNOSTIC_PHRASING)
    if _names_treatment_change(folded, symptoms):
        flags.append(FLAG_TREATMENT_CHANGE)
    if _names_a_product_nobody_offered(folded, cards, known_molecules):
        flags.append(FLAG_UNKNOWN_ITEM)
    return flags


def _names_treatment_change(folded, symptoms) -> bool:
    """A stop-or-change instruction **near an ingredient the customer named**.

    The proximity is the whole of it: *"cambie a la presentación de 10"* is a
    sentence about a box, and *"suspenda el losartán"* is a sentence about a
    person's cardiovascular treatment. Two hundred characters is a paragraph,
    which is longer than anything this response may be.
    """
    treatments = [
        str(fact.get("key"))
        for fact in symptoms or []
        if fact.get("kind") == "active_treatment" and not fact.get("negated")
    ]
    if not treatments:
        return False
    surfaces: list[str] = []
    for key in treatments:
        label, forms = INGREDIENTS.get(key, (key, (key,)))
        surfaces.extend(fold(one) for one in (label, key, *forms))
    for pattern in TREATMENT_CHANGE_EXPRESSIONS:
        for hit in pattern.finditer(folded):
            window = folded[max(0, hit.start() - 100) : hit.end() + 100]
            if any(surface and surface in window for surface in surfaces):
                return True
    return False


def _names_a_product_nobody_offered(folded, cards, known_molecules) -> bool:
    """A product name outside the candidate list.

    Matched on the **first word** of each offered name -- the molecule -- rather
    than on the whole string, because a response naming *"suero oral"* for
    *"Sales de rehidratación oral"* is naming the same product and a response
    naming *"loperamida"* when no loperamida was offered is not.

    `known_molecules` is **this tenant's own catalog**, folded, and it is what
    makes the rule decidable: a word is a product name if the pharmacy sells
    something by that name, and a response naming one it was not given has
    reached past the list. Without it the check would have to guess which
    words in a sentence are products, and a guess is what criterion 11 exists
    to replace.
    """
    offered = {
        fold(card["item_name"]).split()[0] for card in cards if card["item_name"]
    }
    known = (
        {fold(label) for label, _ in INGREDIENTS.values()}
        | {fold(key) for key in INGREDIENTS}
        | {fold(one) for one in known_molecules if one}
    )
    for word in set(folded.replace("\n", " ").split()):
        stripped = word.strip(".,;:()«»\"'")
        if len(stripped) < 6 or stripped in offered:
            continue
        if stripped in known:
            # A molecule the catalog knows that nobody offered. This is exactly
            # criterion 11: the response reached past the list it was given.
            return True
    return False


def merge_model_symptoms(lexicon_facts, model_facts) -> list[dict]:
    """The model **may add chips and may never remove one the lexicon found**.

    A model that could narrow the symptom set could un-filter a product the
    safety layer had excluded, which is the one thing it must not be able to do.
    So this is a union keyed on `(kind, key)` with the lexicon's own fact
    winning every collision, and an added key outside the closed vocabulary is
    dropped rather than trusted.
    """
    from core.assistant.vocabulary import (
        INGREDIENT_KEYS,
        POPULATION_KEYS,
        SYMPTOM_KEYS,
    )

    allowed = {
        "symptom": set(SYMPTOM_KEYS),
        "population": set(POPULATION_KEYS),
        "active_treatment": set(INGREDIENT_KEYS),
    }
    merged = list(lexicon_facts or [])
    held = {(fact.get("kind"), fact.get("key")) for fact in merged}
    for fact in model_facts or []:
        kind = fact.get("kind")
        key = fact.get("key")
        if kind not in allowed or key not in allowed[kind]:
            continue
        if (kind, key) in held:
            continue
        merged.append({**fact, "source": "model"})
        held.add((kind, key))
    return merged


def has_conditional(cards) -> bool:
    return any(card["type"] == SuggestionType.CONDITIONAL for card in cards)
