"""Step 1 of the pipeline: what the customer said, as keys the filter matches on.

**The lexicon runs first, always, on the device, even when the network is up**
(S8, *1 · Extract*). When the model is reachable it also extracts, and **it may
add chips and may never remove one the lexicon found** -- a model that could
narrow the symptom set could un-filter a product the safety layer had excluded,
which is the one thing the model must not be able to do. That rule is enforced
in `prose.merge_model_symptoms`, not here; this module is the lexicon's half and
has no model in it at all.

The output shape is fixed by §3:

    [{"key": "diarrhea", "label": "diarrea", "kind": "symptom",
      "source": "lexicon"}]

`key` is English and is what a trigger names; `label` is Spanish and is what a
cashier reads on the chip. Neither is derived from the other at runtime.

**Four kinds, and only three of them are chips.** `symptom`, `population` and
`active_treatment` are drawn; `duration` is not, because the handoff's own
screen draws four chips for a transcript that states a duration, and a duration
qualifies the symptoms rather than standing beside them.

**Negation is load-bearing and is the reason this module is not a substring
scan.** *"sin fiebre"* is not the same fact as saying nothing about fever: the
first decides a `do_not_suggest_if` clause false and lets the item rank
normally, the second leaves it undecided and puts the warning on the card. A
lexicon with no negation would collapse those two into one and the difference is
a Con condición card that should not be there -- or, worse, is not.
"""

import re

from core.assistant.vocabulary import (
    INGREDIENTS,
    POPULATIONS,
    SYMPTOMS,
    TREATMENT_LEADS,
    fold,
)

#: What turns a mention into its own denial, looked for in the words immediately
#: before the match.
NEGATIONS: tuple[str, ...] = (
    "sin",
    "no",
    "nada de",
    "ninguna",
    "ningun",
    "tampoco",
    "niega",
)

#: How far back a negation may sit and still bind. Four words is *"no tiene nada
#: de fiebre"* and stops short of *"no le sirvió el jarabe, tiene fiebre"*.
NEGATION_WINDOW_WORDS = 4

#: Between kinds the row is drawn symptoms, then who it is for, then what they
#: are already on -- which is the order the handoff's own chips read in.
_KIND_ORDER = {"symptom": 0, "population": 1, "active_treatment": 2, "duration": 3}

#: `dos días`, `3 dias`, `una semana`. Written out because a cashier types what
#: the customer said and the customer says *"lleva dos días"*.
NUMBER_WORDS: dict[str, int] = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "quince": 15,
}

_DURATION = re.compile(
    r"\b(?P<count>\d{1,3}|" + "|".join(NUMBER_WORDS) + r")\s+"
    r"(?P<unit>dias?|semanas?|meses|mes|horas?)\b"
)

#: `fiebre de 39`, `39 grados`, `38,5 °C`, `temperatura de 38.5`. The comma is
#: the decimal separator a Colombian types (§A.11) and the point is what a
#: keypad emits, so both are read and neither is a thousands separator here --
#: no fever is four figures.
_TEMPERATURE = re.compile(
    r"(?:fiebre|temperatura|calentura)\D{0,12}(?P<value>\d{2}(?:[.,]\d)?)"
    r"|(?P<alt>\d{2}(?:[.,]\d)?)\s*(?:grados|°\s*c|ºc|c\b)"
)

#: The range a body temperature can be in. A `39` that came out of a pack size
#: or a price is not a fever, and a threshold compared against one is a filter
#: firing on nothing.
TEMPERATURE_FLOOR = 34.0
TEMPERATURE_CEILING = 43.0


def extract(transcript: str) -> list[dict]:
    """The chips and the facts behind them, in the order they are drawn.

    Symptoms first, then populations, then active treatments, then the duration
    -- which is the order the handoff's own row of chips reads in, and it is
    stable so that two runs over one transcript produce the same row.
    """
    folded = fold(transcript or "")
    if not folded.strip():
        return []
    words = _word_spans(folded)
    temperature = _temperature(folded)
    duration = _duration_days(folded)

    facts: list[dict] = []
    facts.extend(_matches(folded, words, SYMPTOMS, "symptom"))
    facts.extend(_matches(folded, words, POPULATIONS, "population"))
    facts.extend(_treatments(folded, words))
    # Inside a kind, the order is the order the customer said them, which is the
    # order a cashier reads the row back in. Between kinds it is the fixed one
    # above, so a transcript that names an age before a symptom still draws its
    # chips in the shape the handoff does.
    facts.sort(key=lambda fact: (_KIND_ORDER[fact["kind"]], fact.pop("_at", 0)))

    for fact in facts:
        if fact["kind"] == "symptom" and fact["key"] == "fever":
            if temperature is not None and not fact.get("negated"):
                fact["value"] = temperature
                fact["unit"] = "celsius"
                fact["label"] = f"fiebre {_spanish_number(temperature)} °C"

    if duration is not None:
        facts.append(
            {
                "key": "duration_days",
                "label": f"{_spanish_number(duration)} días",
                "kind": "duration",
                "source": "lexicon",
                "value": duration,
                "unit": "days",
            }
        )
    return facts


def _matches(folded, words, table, kind) -> list[dict]:
    """Every key in one table the transcript names, longest surface form first.

    Longest-first matters: *"dolor de estomago"* must not be found as *"dolor de
    cabeza"*'s neighbour, and *"sangre en la deposicion"* must win over the
    bare *"con sangre"* that sits inside it.
    """
    found: dict[str, dict] = {}
    for key, (label, forms) in table.items():
        for form in sorted(forms, key=len, reverse=True):
            needle = fold(form)
            at = _find_whole(folded, needle)
            if at is None:
                continue
            found[key] = {
                "key": key,
                "label": label,
                "kind": kind,
                "source": "lexicon",
                "_at": at,
            }
            if _negated(folded, words, at):
                found[key]["negated"] = True
                found[key]["label"] = f"sin {label}"
            break
    return list(found.values())


def _treatments(folded, words) -> list[dict]:
    """*"toma losartán"* -- an ingredient the customer says they are already on.

    **The lead word is required.** A transcript naming a molecule the cashier is
    about to sell is not a statement that the customer already takes it, and an
    `interacts_with_ingredient` clause fired by the product on the counter is a
    filter that removes exactly the thing it was asked about.
    """
    del words
    facts = []
    for key, (label, forms) in INGREDIENTS.items():
        for form in sorted(forms, key=len, reverse=True):
            at = _find_whole(folded, fold(form))
            if at is None or not _led_by_treatment(folded, at):
                continue
            facts.append(
                {
                    "key": key,
                    "label": f"tratamiento activo · {label}",
                    "kind": "active_treatment",
                    "source": "lexicon",
                    "_at": at,
                }
            )
            break
    return facts


def _led_by_treatment(folded, at) -> bool:
    lead = folded[max(0, at - 40) : at]
    return any(word in lead for word in TREATMENT_LEADS)


def _find_whole(haystack: str, needle: str):
    """Where `needle` sits in `haystack` on word boundaries, or `None`.

    A plain `in` would find `tos` inside `estomago` and put a cough chip on
    every stomach complaint in the country.
    """
    if not needle:
        return None
    for match in re.finditer(re.escape(needle), haystack):
        start, end = match.span()
        before = haystack[start - 1] if start else " "
        after = haystack[end] if end < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return start
    return None


def _word_spans(folded) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[a-z0-9]+", folded)]


def _negated(folded, words, at) -> bool:
    """Whether a negation sits within `NEGATION_WINDOW_WORDS` before the match,
    with no clause break between the two.

    The clause break is what keeps *"no le sirvió el jarabe, tiene fiebre"* out
    of the negated set: a comma or a full stop ends the denial.
    """
    index = next(
        (i for i, (start, _, _) in enumerate(words) if start >= at), len(words)
    )
    window = words[max(0, index - NEGATION_WINDOW_WORDS) : index]
    for position, (start, end, word) in enumerate(window):
        if word not in NEGATIONS:
            continue
        between = folded[end:at]
        if any(mark in between for mark in (",", ";", ".", " pero ", " aunque ")):
            continue
        del position, start
        return True
    return False


def _duration_days(folded):
    match = _DURATION.search(folded)
    if match is None:
        return None
    raw = match.group("count")
    count = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw, 0)
    unit = match.group("unit")
    if unit.startswith("hora"):
        # Under a day is still a duration, and rounding it up to one is what
        # makes `duration_days >= 2` false rather than accidentally true.
        return round(count / 24, 2)
    if unit.startswith("semana"):
        return count * 7
    if unit.startswith("mes"):
        return count * 30
    return float(count)


def _temperature(folded):
    for match in _TEMPERATURE.finditer(folded):
        raw = match.group("value") or match.group("alt")
        if raw is None:
            continue
        value = float(raw.replace(",", "."))
        if TEMPERATURE_FLOOR <= value <= TEMPERATURE_CEILING:
            return value
    return None


def _spanish_number(value: float) -> str:
    """§A.11 · the decimal separator is a comma, and a whole number carries none."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".replace(".", ",")


def keys_of(symptoms, kind=None) -> set[str]:
    """The keys one extraction holds, optionally of one kind, negations aside."""
    return {
        str(fact.get("key"))
        for fact in symptoms or []
        if not fact.get("negated") and (kind is None or fact.get("kind") == kind)
    }
