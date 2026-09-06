"""The closed vocabularies this stage's safety layer rests on.

**One versioned reference asset, shipped with the release** (S8, *In* 6). It
holds four closed lists and the Spanish lexicon that maps what a customer says
onto them:

  `SYMPTOM_KEYS`      what an extraction may emit and a trigger may name
  `POPULATION_KEYS`   who the customer is, as a chip and as a trigger clause
  `INGREDIENT_KEYS`   what they said they are already taking
  `CLAUSE_KEYS`       the five shapes a trigger clause may take

**This closure is the load-bearing decision of the safety layer.** If the
extractor may emit a key the trigger vocabulary does not contain, or a warning
may name a condition the extractor cannot produce, then the filter never fires
and nothing anywhere raises: the assistant looks like it is working and the
safety layer is decorative. So the vocabulary is one list, the API refuses an
`item_warnings` write whose `triggers` names a key outside it, and
`assistant.health_check` counts warnings whose triggers have never once matched
an extraction in 30 days.

**The English key and the Spanish label are never derived from each other at
runtime.** `key` is what the filter matches on; `label` is the chip a cashier
reads. Two columns, one table, stated here once.

**The condition lexicon and the diagnostic patterns are shipped assets, not
settings** (S8, *Gated on*). A deployment that can edit the output check's own
vocabulary can turn the output check off, which is the same defect as a
configurable advisory notice (A8). `bundle_version` records which one ran.
"""

import re
import unicodedata

#: Bumped whenever any list below changes. It is stamped on every
#: `assistant_queries` row, which is what makes a till extracting against last
#: quarter's vocabulary visible rather than invisible.
LEXICON_VERSION = "1"


def fold(text: str) -> str:
    """Accents stripped, lowercased -- the same folding `search_name` uses.

    Stated once and applied to both sides of every match, so `Losartán`,
    `losartan` and `LOSARTAN` are one key and a cashier typing without accents
    at a counter is not a cashier the extractor ignores.
    """
    lowered = (text or "").lower()
    return "".join(
        character
        for character in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(character) != "Mn"
    )


# ---------------------------------------------------------------------------
# 1 · symptoms
# ---------------------------------------------------------------------------

#: `key -> (label, surface forms)`. The label is the chip; the surface forms are
#: what a person actually says at a Colombian counter, folded on both sides.
#:
#: **The list is deliberately short of a clinical taxonomy.** It covers what an
#: auxiliar is asked at a mostrador; a key nobody can say is a key no warning can
#: usefully name.
SYMPTOMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "diarrhea": (
        "diarrea",
        (
            "diarrea",
            "diarreas",
            "soltura",
            "descompuesto del estomago",
            "flojera del estomago",
        ),
    ),
    "fever": ("fiebre", ("fiebre", "calentura", "temperatura alta", "febril")),
    "vomiting": ("vómito", ("vomito", "vomitando", "vomita", "devuelve la comida")),
    "nausea": (
        "náuseas",
        ("nausea", "nauseas", "ganas de vomitar", "mareo del estomago"),
    ),
    "abdominal_pain": (
        "dolor abdominal",
        (
            "dolor de estomago",
            "dolor abdominal",
            "retorcijones",
            "colico abdominal",
            "le duele la barriga",
        ),
    ),
    "heartburn": ("acidez", ("acidez", "agrieras", "reflujo", "ardor en el pecho")),
    "constipation": (
        "estreñimiento",
        ("estrenimiento", "estrenido", "no puede obrar", "duro del estomago"),
    ),
    "headache": (
        "dolor de cabeza",
        ("dolor de cabeza", "cefalea", "migrana", "jaqueca"),
    ),
    "muscle_pain": (
        "dolor muscular",
        ("dolor muscular", "dolor en el cuerpo", "dolor de cuerpo", "malestar general"),
    ),
    "back_pain": (
        "dolor de espalda",
        ("dolor de espalda", "dolor lumbar", "dolor de cintura"),
    ),
    "sore_throat": (
        "dolor de garganta",
        (
            "dolor de garganta",
            "garganta irritada",
            "ardor de garganta",
            "amigdalas inflamadas",
        ),
    ),
    "cough": ("tos", ("tos", "tosiendo", "tos seca", "tos con flema")),
    "nasal_congestion": (
        "congestión nasal",
        ("congestion", "congestion nasal", "nariz tapada", "tapada la nariz"),
    ),
    "runny_nose": (
        "secreción nasal",
        ("mocos", "moco", "secrecion nasal", "le corre la nariz"),
    ),
    "allergy": ("alergia", ("alergia", "alergico", "rinitis", "estornudos")),
    "skin_rash": ("brote en la piel", ("brote", "sarpullido", "ronchas", "erupcion")),
    "itching": ("picazón", ("picazon", "rasquina", "comezon")),
    "blood_in_stool": (
        "sangre en la deposición",
        (
            "sangre en la deposicion",
            "sangre en las heces",
            "hay sangre",
            "con sangre",
            "deposicion con sangre",
        ),
    ),
    "dizziness": ("mareo", ("mareo", "mareado", "vertigo")),
    "insomnia": ("insomnio", ("insomnio", "no puede dormir", "no concilia el sueno")),
    "menstrual_pain": (
        "cólicos menstruales",
        ("colicos", "colico menstrual", "dolor menstrual", "dolor de la regla"),
    ),
    "earache": ("dolor de oído", ("dolor de oido", "otalgia", "le duele el oido")),
    "eye_irritation": (
        "ojos irritados",
        ("ojos irritados", "ojo rojo", "ardor en los ojos", "conjuntivitis"),
    ),
    "burning_urination": (
        "ardor al orinar",
        ("ardor al orinar", "arde al orinar", "infeccion urinaria"),
    ),
    "dehydration": ("deshidratación", ("deshidratacion", "deshidratado", "esta seco")),
    "wound": ("herida", ("herida", "cortada", "raspon", "quemadura")),
    "fatigue": ("cansancio", ("cansancio", "debilidad", "decaido", "sin fuerzas")),
}

SYMPTOM_KEYS: tuple[str, ...] = tuple(SYMPTOMS)


# ---------------------------------------------------------------------------
# 2 · populations
# ---------------------------------------------------------------------------

#: Who is going to take it. **Two of these are chronic states rather than ages**
#: -- `diabetic` and `hypertensive` -- and they are here rather than in a sixth
#: list because a contraindication phrases them identically: *no dar a
#: diabéticos* is the same clause shape as *no dar a menores*.
POPULATIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "adult": ("adulto", ("adulto", "adulta", "mayor de edad", "para mi", "es para mi")),
    "child": (
        "niño",
        (
            "nino",
            "nina",
            "menor",
            "el chiquito",
            "la chiquita",
            "pediatrico",
            "para un nino",
        ),
    ),
    "infant": ("bebé", ("bebe", "lactante", "recien nacido", "de meses")),
    "pregnant": (
        "embarazada",
        ("embarazada", "embarazo", "en embarazo", "esta esperando"),
    ),
    "breastfeeding": (
        "lactancia",
        ("lactancia", "esta lactando", "dando pecho", "amamantando"),
    ),
    "elderly": (
        "adulto mayor",
        ("adulto mayor", "anciano", "abuelo", "abuela", "de la tercera edad"),
    ),
    "diabetic": ("diabético", ("diabetico", "diabetica", "diabetes", "azucar alta")),
    "hypertensive": (
        "hipertenso",
        ("hipertenso", "hipertensa", "hipertension", "tension alta", "presion alta"),
    ),
}

POPULATION_KEYS: tuple[str, ...] = tuple(POPULATIONS)

#: The populations that are mutually exclusive **ages**, and therefore the ones
#: whose presence *decides* another one false. Saying `adulto` settles that the
#: customer is not a `niño`; it settles nothing about `diabético`, which is why
#: the chronic states are not in this set.
#:
#: This is what makes a `{"population": "child"}` clause *irrelevant* rather than
#: *unresolved* on an adult query -- see `evaluate` in `filters.py`.
AGE_POPULATIONS: frozenset[str] = frozenset({"adult", "child", "infant", "elderly"})


# ---------------------------------------------------------------------------
# 3 · active treatments
# ---------------------------------------------------------------------------

#: What the customer says they are already taking, as `key -> (label, surface
#: forms)`. The key is the **folded** INN name, which is also what
#: `items.active_ingredient` folds to -- so an `interacts_with_ingredient`
#: clause and an extraction meet on one string rather than on two spellings of
#: it -- while the label keeps the accent a chip is read with.
INGREDIENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "losartan": ("losartán", ("losartan",)),
    "enalapril": ("enalapril", ("enalapril",)),
    "amlodipino": ("amlodipino", ("amlodipino",)),
    "metformina": ("metformina", ("metformina",)),
    "warfarina": ("warfarina", ("warfarina", "warfarin")),
    "clopidogrel": ("clopidogrel", ("clopidogrel",)),
    "acido acetilsalicilico": (
        "aspirina",
        ("aspirina", "acido acetilsalicilico", "asa"),
    ),
    "atorvastatina": ("atorvastatina", ("atorvastatina",)),
    "ibuprofeno": ("ibuprofeno", ("ibuprofeno",)),
    "naproxeno": ("naproxeno", ("naproxeno",)),
    "diclofenaco": ("diclofenaco", ("diclofenaco",)),
    "acetaminofen": ("acetaminofén", ("acetaminofen", "paracetamol")),
    "omeprazol": ("omeprazol", ("omeprazol",)),
    "levotiroxina": ("levotiroxina", ("levotiroxina",)),
    "insulina": ("insulina", ("insulina",)),
    "furosemida": ("furosemida", ("furosemida",)),
    "hidroclorotiazida": ("hidroclorotiazida", ("hidroclorotiazida",)),
    "amoxicilina": ("amoxicilina", ("amoxicilina",)),
    "prednisolona": ("prednisolona", ("prednisolona", "prednisona")),
    "sertralina": ("sertralina", ("sertralina",)),
    "alprazolam": ("alprazolam", ("alprazolam",)),
    "carbamazepina": ("carbamazepina", ("carbamazepina",)),
}

INGREDIENT_KEYS: tuple[str, ...] = tuple(INGREDIENTS)

#: What a person says in front of the medicine's name. The pattern that turns
#: *"toma losartán"* into `{"kind": "active_treatment", "key": "losartan"}`.
TREATMENT_LEADS: tuple[str, ...] = (
    "toma",
    "tomando",
    "esta tomando",
    "usa",
    "usando",
    "esta en tratamiento con",
    "tratamiento con",
    "le formularon",
    "medicado con",
    "consume",
)


# ---------------------------------------------------------------------------
# 4 · the trigger clause vocabulary
# ---------------------------------------------------------------------------

#: The five shapes a clause may take, and no sixth.
CLAUSE_KEYS: tuple[str, ...] = (
    "symptom",
    "population",
    "interacts_with_ingredient",
    "duration_days",
    "measurement",
)

OPERATORS: tuple[str, ...] = (">", ">=", "<", "<=", "==")

UNITS: tuple[str, ...] = ("celsius", "days")


class InvalidTrigger(ValueError):
    """A `triggers` value naming a key outside the closed vocabulary.

    Raised by `check_triggers` and answered as a **field-scope** refusal naming
    the key (§B.10.3), never as a save that half-works: a trigger the extractor
    cannot produce is a warning that never fires, and the whole safety layer is
    then decorative.
    """


def check_triggers(triggers) -> list[dict]:
    """Validate one warning's `triggers`, or refuse naming the offending key.

    Returns the normalised array. A warning may carry **no** clauses -- an
    `interaction` whose text is advice for the cashier to read rather than a
    condition anything evaluates is legitimate, and it simply never fires.
    """
    if triggers is None:
        return []
    if not isinstance(triggers, list):
        raise InvalidTrigger(
            "Las condiciones de una advertencia son una lista de cláusulas."
        )
    normalised = []
    for index, clause in enumerate(triggers):
        normalised.append(_check_clause(clause, index))
    return normalised


def _check_clause(clause, index) -> dict:
    where = f"condición {index + 1}"
    if not isinstance(clause, dict) or not clause:
        raise InvalidTrigger(f"La {where} no es una condición.")
    unknown = [key for key in clause if key not in (*CLAUSE_KEYS, *_QUALIFIERS)]
    if unknown:
        raise InvalidTrigger(
            f"«{unknown[0]}» no está en el vocabulario de condiciones. "
            f"Las condiciones se escriben con: {', '.join(CLAUSE_KEYS)}."
        )
    named = [key for key in CLAUSE_KEYS if key in clause]
    if len(named) != 1:
        raise InvalidTrigger(
            f"La {where} nombra {len(named)} condiciones y debe nombrar una."
        )
    key = named[0]
    if key == "symptom":
        return _check_symptom_clause(clause, where)
    if key == "population":
        return _check_member(clause, "population", POPULATION_KEYS, where, "población")
    if key == "interacts_with_ingredient":
        return _check_member(
            clause,
            "interacts_with_ingredient",
            INGREDIENT_KEYS,
            where,
            "principio activo",
        )
    return _check_duration_clause(clause, where)


#: The three qualifier keys a `symptom` clause may carry beside its own name.
_QUALIFIERS = ("operator", "value", "unit")


def _check_member(clause, key, allowed, where, noun) -> dict:
    value = clause.get(key)
    if value not in allowed:
        raise InvalidTrigger(
            f"«{value}» no es una {noun} que el asistente sepa extraer, así que "
            f"la {where} nunca se activaría."
        )
    return {key: value}


def _check_symptom_clause(clause, where) -> dict:
    symptom = clause.get("symptom")
    if symptom not in SYMPTOM_KEYS:
        raise InvalidTrigger(
            f"«{symptom}» no es un síntoma que el asistente sepa extraer, así "
            f"que la {where} nunca se activaría."
        )
    qualifiers = [key for key in _QUALIFIERS if key in clause]
    if not qualifiers:
        return {"symptom": symptom}
    if not {"operator", "value"} <= set(qualifiers):
        # A clause carrying only a `unit`, or only an `operator`, is a threshold
        # somebody started and did not finish. It is a **field-scope refusal
        # naming what is missing** (§B.10.3) and never a 500 on the next line
        # that indexes the key that is not there.
        raise InvalidTrigger(
            f"La {where} lleva un umbral incompleto: escriba «operator» y «value»."
        )
    if clause["operator"] not in OPERATORS:
        raise InvalidTrigger(
            f"«{clause['operator']}» no es una comparación. Use {', '.join(OPERATORS)}."
        )
    value = clause["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidTrigger(f"El umbral de la {where} debe ser un número.")
    unit = clause.get("unit", "celsius")
    if unit not in UNITS:
        raise InvalidTrigger(f"«{unit}» no es una unidad. Use {', '.join(UNITS)}.")
    return {
        "symptom": symptom,
        "operator": clause["operator"],
        "value": float(value),
        "unit": unit,
    }


def _check_duration_clause(clause, where) -> dict:
    inner = clause.get("duration_days")
    if not isinstance(inner, dict):
        raise InvalidTrigger(
            f"La {where} debe escribir la duración como "
            '{"operator": ">=", "value": 2}.'
        )
    if inner.get("operator") not in OPERATORS:
        raise InvalidTrigger(
            f"«{inner.get('operator')}» no es una comparación. Use "
            f"{', '.join(OPERATORS)}."
        )
    value = inner.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidTrigger(f"El umbral de la {where} debe ser un número.")
    return {"duration_days": {"operator": inner["operator"], "value": float(value)}}


def trigger_keys(triggers) -> set[str]:
    """Every vocabulary key one warning's triggers name.

    `assistant.health_check` matches these against 30 days of extractions to
    find a warning that has never once fired.
    """
    keys: set[str] = set()
    for clause in triggers or []:
        if not isinstance(clause, dict):
            continue
        for key in ("symptom", "population", "interacts_with_ingredient"):
            if key in clause:
                keys.add(str(clause[key]))
        if "duration_days" in clause:
            keys.add("duration_days")
    return keys


# ---------------------------------------------------------------------------
# 5 · the output check's own vocabularies
# ---------------------------------------------------------------------------

#: Condition nouns a recommendation may not contain. **Naming a condition is
#: diagnosing**, and the blueprint's sixth rule is that the assistant does not.
#: Folded before matching, so `Infección` and `infeccion` are one entry.
CONDITION_LEXICON: tuple[str, ...] = (
    "gastroenteritis",
    "infeccion",
    "infeccion viral",
    "infeccion bacteriana",
    "amebiasis",
    "amibiasis",
    "virus",
    "viral",
    "bacteria",
    "colitis",
    "gastritis",
    "apendicitis",
    "intoxicacion",
    "salmonelosis",
    "parasitosis",
    "parasitos",
    "dengue",
    "covid",
    "influenza",
    "neumonia",
    "bronquitis",
    "faringitis",
    "amigdalitis",
    "sinusitis",
    "otitis",
    "cistitis",
    "migrana",
    "ulcera",
    "reflujo gastroesofagico",
    "diabetes",
    "hipertension",
    "anemia",
    "hepatitis",
    "tifoidea",
)

#: Diagnostic phrasing. A recommendation that reaches for any of these is
#: reaching for a diagnosis whatever nouns it avoided.
DIAGNOSTIC_PATTERNS: tuple[str, ...] = (
    r"\busted tiene\b",
    r"\btiene una?\b",
    r"\bes un caso de\b",
    r"\bprobablemente sea\b",
    r"\bprobablemente tenga\b",
    r"\bse trata de\b",
    r"\bel diagnostico\b",
    r"\bpadece\b",
    r"\bsufre de\b",
    r"\bpuede ser\b",
    r"\bparece ser\b",
)

#: A stop-or-change instruction near an ingredient the customer named as an
#: active treatment. **This is the one output rule that is about the customer's
#: own medicine**, and it is the one whose failure mode is a person stopping a
#: cardiovascular drug because a till told them to.
TREATMENT_CHANGE_PATTERNS: tuple[str, ...] = (
    r"\bsuspend\w*\b",
    r"\bdeje de tomar\b",
    r"\bdejar de tomar\b",
    r"\bcambie\b",
    r"\bcambiar\b",
    r"\breemplace\b",
    r"\breemplazar\b",
    r"\bsustituya\b",
    r"\bno siga tomando\b",
    r"\bretire\b",
)

CONDITION_EXPRESSIONS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{re.escape(word)}\b") for word in CONDITION_LEXICON
)
DIAGNOSTIC_EXPRESSIONS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in DIAGNOSTIC_PATTERNS
)
TREATMENT_CHANGE_EXPRESSIONS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern) for pattern in TREATMENT_CHANGE_PATTERNS
)


# ---------------------------------------------------------------------------
# 6 · the mandatory advisory notice
# ---------------------------------------------------------------------------

#: The sentence at the foot of card C. It lives here so the server's own tests
#: can assert on it; **the client ships its own copy inside the suggestions
#: component and takes it from no prop, setting, role or payload** (A8), which
#: is what the build gate greps for. It is deliberately *not* in the bundle: a
#: notice delivered over the wire is a notice a deployment can empty.
ADVISORY_NOTICE = (
    "Con fiebre de más de dos días, remitir a consulta médica. Botica no diagnostica."
)
