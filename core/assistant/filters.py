"""The safety layer, evaluated: three outcomes, and the middle one is the whole
of `Con condición`.

**A product carrying a blocking warning for what the customer actually said
never becomes a candidate.** It is removed before anything is ranked, before any
prompt is built, and there is no path in this stage from a filtered item to a
rendered card (S8, *Outcome*). This module is where that happens, and it is
deliberately separate from the ranker so that the order can be read off the code
rather than inferred from it.

## The three outcomes, and why *undecided* is not *irrelevant*

A trigger's clauses are **ORed**, and the trigger's own outcome is the worst of
them:

  **satisfied**   some clause is decided **true** by the extracted set
  **irrelevant**  every clause is decided **false** by it
  **unresolved**  neither -- the extracted set **cannot decide it**

The distinction that carries the weight is between *the customer said it is not
so* and *nobody asked*. The lexicon has negation handling precisely so those two
are different facts (S8, *1 · Extract*), and if silence were `irrelevant` the
negation handling would have nothing to do: an unmentioned symptom and a denied
one would produce the same filter.

## What each outcome does, and why it is not the same for all three types

`evaluate` answers what the extraction *knows*. What that means for a card is
the caller's, and it is deliberately asymmetric by warning type -- because the
three types ask different questions:

  `do_not_suggest_if`   asks **"has anyone ruled this out?"** An unresolved
                        trigger is a question nobody put to the customer, so the
                        card is **Con condición** and its reason line is the
                        warning itself. That is the whole of `conditional`.
  `contraindication`    ask **"does this apply to the person in front of you?"**
  `interaction`         The customer either is pregnant or is not, and either
                        named a treatment or was not asked; an undecided trigger
                        is not a caution to read out, it is a condition that did
                        not come up. Only a **satisfied** one reaches a card --
                        as a filter where it is `blocking`, as a `conditional`
                        where it is `advisory`.

That asymmetry is what makes the stage's own criteria 1, 5 and 6 all true on one
warning while card C stays readable. The handoff's row triggers on *fiebre por
encima de 38,5 °C o sangre*:

  *"diarrea y algo de fiebre"*      fever stated, no temperature -> unresolved
                                     -> **Con condición**, the warning as reason
  *"...fiebre de 39"*                39 > 38,5 -> satisfied -> **removed**
  the `fiebre` chip removed          nobody asked -> unresolved -> **Con
                                     condición** again (criterion 6)
  *"diarrea sin fiebre"*             fever denied and blood never asked -> still
                                     unresolved, because the blood half is still
                                     a question nobody put

*What breaks if the asymmetry goes the other way* -- every unresolved trigger of
every type becoming a card -- is that a counter screen carries four caution
lines on every query, a cashier stops reading them, and the one that mattered is
the one nobody read. *What breaks if it goes away entirely* -- only satisfied
triggers ever reaching a card -- is the handoff's own Loperamida row, which
exists precisely to be shown when the temperature was not given.
"""

from core.assistant.vocabulary import AGE_POPULATIONS

SATISFIED = "satisfied"
UNRESOLVED = "unresolved"
IRRELEVANT = "irrelevant"

#: Worst-first, so a trigger's outcome is the worst of its clauses'. The clauses
#: of one trigger are **ORed**: one satisfied clause satisfies the trigger, and
#: a trigger nothing decides is unresolved.
_RANK = {SATISFIED: 0, UNRESOLVED: 1, IRRELEVANT: 2}

#: The types whose unresolved trigger is a question a cashier reads out loud.
#: See the module docstring: `do_not_suggest_if` asks *has anyone ruled this
#: out?* and the other two ask *does this apply?*
ASKS_TO_BE_RULED_OUT = "do_not_suggest_if"


def makes_conditional(warning_type, severity, outcome) -> bool:
    """Whether one evaluated warning puts the card in **Con condición**.

    Stated here rather than at the filter, because it is the same rule the
    device applies and the seed's own card builder applies, and three copies of
    it would be three chances to disagree about a safety string.
    """
    if outcome == IRRELEVANT:
        return False
    if warning_type == ASKS_TO_BE_RULED_OUT:
        # `satisfied` never reaches this: a satisfied blocking trigger filtered
        # the item, and a satisfied advisory one is a caution that does apply.
        return True
    return outcome == SATISFIED and severity == "advisory"


_COMPARISONS = {
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
    "==": lambda left, right: left == right,
}


class Extraction:
    """One query's extracted set, indexed for the handful of lookups a clause
    makes -- built once per query rather than per candidate per clause."""

    def __init__(self, symptoms):
        self.symptoms: dict[str, dict] = {}
        self.denied: set[str] = set()
        self.populations: set[str] = set()
        self.treatments: set[str] = set()
        self.duration: float | None = None
        for fact in symptoms or []:
            key = str(fact.get("key") or "")
            kind = fact.get("kind")
            if not key:
                continue
            if kind == "symptom":
                if fact.get("negated"):
                    self.denied.add(key)
                else:
                    self.symptoms[key] = fact
            elif kind == "population" and not fact.get("negated"):
                self.populations.add(key)
            elif kind == "active_treatment" and not fact.get("negated"):
                self.treatments.add(key)
            elif kind == "duration":
                self.duration = _number(fact.get("value"))

    @property
    def keys(self) -> set[str]:
        return set(self.symptoms) | self.populations | self.treatments


def evaluate(triggers, extraction: Extraction) -> str:
    """One warning's whole trigger array, ORed, as one of the three outcomes.

    A warning with **no** clauses is `irrelevant`: an `interaction` whose text
    is advice for a cashier to read rather than a condition anything evaluates
    is legitimate, and it simply never fires.
    """
    outcomes = [_clause(clause, extraction) for clause in triggers or []]
    if not outcomes:
        return IRRELEVANT
    return min(outcomes, key=lambda outcome: _RANK[outcome])


def _clause(clause, extraction: Extraction) -> str:
    if not isinstance(clause, dict):
        return IRRELEVANT
    if "symptom" in clause:
        return _symptom(clause, extraction)
    if "population" in clause:
        return _population(clause, extraction)
    if "interacts_with_ingredient" in clause:
        return _ingredient(clause["interacts_with_ingredient"], extraction)
    if "duration_days" in clause:
        return _duration(clause["duration_days"], extraction)
    return IRRELEVANT


def _ingredient(key, extraction: Extraction) -> str:
    """An interaction with a medicine the customer says they are already on.

    **Naming one treatment decides the others false.** *"toma losartán"* is an
    answer to *"¿está tomando algo?"*, and a counter that treated it as evidence
    about warfarina as well would put an interaction caution on every card for
    every customer who answered the question.
    """
    if key in extraction.treatments:
        return SATISFIED
    if extraction.treatments:
        return IRRELEVANT
    return UNRESOLVED


def _symptom(clause, extraction: Extraction) -> str:
    key = clause.get("symptom")
    if key in extraction.denied:
        # The customer said it is not so. This is the one thing that makes a
        # clause irrelevant on the symptom side, and it is why the lexicon
        # handles negation at all.
        return IRRELEVANT
    fact = extraction.symptoms.get(key)
    if "operator" not in clause:
        return SATISFIED if fact is not None else UNRESOLVED
    if fact is None:
        return UNRESOLVED
    measured = _number(fact.get("value"))
    if measured is None:
        # *fiebre* is stated and no temperature is -- the handoff's own
        # Loperamida card, and the whole of `conditional`.
        return UNRESOLVED
    return _compare(clause, measured)


def _population(clause, extraction: Extraction) -> str:
    key = clause.get("population")
    if key in extraction.populations:
        return SATISFIED
    # **An age decides another age false.** Saying *adulto* settles that the
    # customer is not a *niño*; it settles nothing about *diabético*, which is
    # why the chronic states are outside `AGE_POPULATIONS`.
    if key in AGE_POPULATIONS and extraction.populations & AGE_POPULATIONS:
        return IRRELEVANT
    return UNRESOLVED


def _duration(inner, extraction: Extraction) -> str:
    if not isinstance(inner, dict):
        return IRRELEVANT
    if extraction.duration is None:
        return UNRESOLVED
    return _compare(inner, extraction.duration)


def _compare(clause, measured: float) -> str:
    comparison = _COMPARISONS.get(str(clause.get("operator")))
    threshold = _number(clause.get("value"))
    if comparison is None or threshold is None:
        return IRRELEVANT
    return SATISFIED if comparison(measured, threshold) else IRRELEVANT


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
