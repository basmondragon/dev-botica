"""`reason_code` -- the deterministic cause behind every Spanish reason line.

Every suggestion carries an English `reason_code` and a Spanish `reason`. **The
code is written by the pipeline and never by the model**; the string is the
code's fallback text unless a warning or the model supplied better. This is what
makes a reason line non-empty offline, and it is what lets S9 group reasons
without parsing Spanish prose -- the same discipline S6 applies to
`purchase_order_lines.reason_code`.

**The strings live here and are served to the till inside
`GET /api/assistant/bundle`**, so there is one copy of every sentence in the
product rather than one on each side of the wire. The till renders them offline
from its cached bundle; the server renders them from this module when it writes
a local recommendation of its own.

The reason line is always **units at this sede · the reason**, in that order,
because the units are the fact the cashier verifies by looking at the shelf and
the reason is the sentence they repeat out loud. That composition is the
client's, and it is why nothing below carries a units figure.
"""

from core.models import SUGGESTION_REASON_CODES

#: The generic form, used where a symptom has no sentence of its own.
SYMPTOM_PRIMARY_DEFAULT = "es lo primero que se ofrece para lo que describe"

#: `symptom_primary`, templated per symptom key -- the handoff's own line for
#: `diarrhea` and the same register for the rest. A key with no entry falls back
#: to the generic, which is a weaker sentence and never an empty one.
SYMPTOM_PRIMARY: dict[str, str] = {
    "diarrhea": "repone la pérdida de líquidos, que es lo que más pesa en estos casos",
    "dehydration": "repone sales y líquidos, que es lo que hace falta primero",
    "fever": "baja la fiebre y alivia el malestar general",
    "vomiting": "calma el estómago y ayuda a retener líquidos",
    "nausea": "calma las náuseas sin adormecer",
    "abdominal_pain": "alivia el cólico sin irritar el estómago",
    "heartburn": "corta la acidez y protege el estómago",
    "constipation": "regula el tránsito sin forzarlo",
    "headache": "es el analgésico que primero se ofrece para el dolor de cabeza",
    "muscle_pain": "alivia el dolor muscular y baja la inflamación",
    "back_pain": "alivia el dolor de espalda y baja la inflamación",
    "sore_throat": "alivia la garganta desde la primera toma",
    "cough": "calma la tos y ayuda a expulsar la flema",
    "nasal_congestion": "destapa la nariz y deja dormir",
    "runny_nose": "seca la secreción y baja la congestión",
    "allergy": "corta la reacción alérgica sin dar sueño",
    "skin_rash": "calma el brote y la irritación de la piel",
    "itching": "calma la picazón mientras pasa la irritación",
    "dizziness": "calma el mareo y ayuda a estabilizarse",
    "insomnia": "ayuda a conciliar el sueño sin dependencia",
    "menstrual_pain": "alivia el cólico menstrual desde la primera toma",
    "earache": "alivia el dolor de oído mientras se consulta",
    "eye_irritation": "alivia la irritación y limpia el ojo",
    "burning_urination": "alivia el ardor mientras se consulta",
    "wound": "limpia y protege la herida",
    "fatigue": "aporta lo que hace falta cuando hay decaimiento",
}

#: The rest of the table, as format strings. **Two forms of
#: `bought_together_location`, selected by the rule's `confidence_band`**: a
#: percentage carried to two significant figures out of forty tickets is a false
#: precision, and it is read out loud to a customer.
TEMPLATES: dict[str, str] = {
    "symptom_secondary": "alivia el síntoma que describe el cliente",
    "bought_together_location": "aparece en el {share} de los tickets con {anchor}",
    "bought_together_location_low": "se lleva junto con {anchor} en esta sede",
    "bought_together_network": "en la red se lleva junto con {anchor}",
    "ticket_companion": "se lleva junto con lo que ya está en el ticket",
    "substitute_available": "sustituto de una referencia agotada en la sede",
}

#: Card C's deliberately-empty shape (§B.10.2). Its second line names what the
#: filter found and what the shelf did not, which is a **stock** statement and
#: never a history one.
EMPTY_TITLE = "Ninguna referencia disponible para lo que describe el cliente"
EMPTY_BODY_ONE = "La referencia que aplica está agotada en esta sede."
EMPTY_BODY_MANY = "Las {count} referencias que aplican están agotadas en esta sede."
EMPTY_BODY_NONE = "Ninguna referencia del catálogo aplica a lo que describe el cliente."

#: Card B's local register -- **the same ranking, in fewer words** (S8, *5 ·
#: Prose*). There is no model anywhere near these.
LOCAL_PRIMARY_FIRST = "Ofrezca {item} primero."
LOCAL_PRIMARY_CONDITIONAL = "Lea la condición de la tarjeta antes de ofrecer."
LOCAL_PRIMARY_NONE = "No hay una primera opción disponible en esta sede."
LOCAL_SECONDARY_PAIR = "En esta sede {item} se lleva junto con {anchor}."
LOCAL_SECONDARY_ONE = "La sede tiene la referencia disponible."
LOCAL_SECONDARY_MANY = "La sede tiene las {count} referencias disponibles."


#: What the client is sent, as one document. Stated as a function rather than a
#: constant so the bundle and this module cannot drift apart.
def bundle_strings() -> dict:
    return {
        "symptom_primary": SYMPTOM_PRIMARY,
        "symptom_primary_default": SYMPTOM_PRIMARY_DEFAULT,
        "templates": TEMPLATES,
        "empty": {
            "title": EMPTY_TITLE,
            "one": EMPTY_BODY_ONE,
            "many": EMPTY_BODY_MANY,
            "none": EMPTY_BODY_NONE,
        },
        "local": {
            "primary_first": LOCAL_PRIMARY_FIRST,
            "primary_conditional": LOCAL_PRIMARY_CONDITIONAL,
            "primary_none": LOCAL_PRIMARY_NONE,
            "secondary_pair": LOCAL_SECONDARY_PAIR,
            "secondary_one": LOCAL_SECONDARY_ONE,
            "secondary_many": LOCAL_SECONDARY_MANY,
        },
    }


def share(confidence) -> str:
    """§A.11 · a percentage with a decimal comma and no trailing zero.

    `0.41` is `41%`, `0.415` is `41,5%`. The figure is read out loud to a
    customer, so it is rounded to one decimal and never to three.
    """
    percent = round(float(confidence) * 100, 1)
    if percent.is_integer():
        return f"{int(percent)}%"
    return f"{percent:.1f}".replace(".", ",") + "%"


def line(code: str, **values) -> str:
    """The fallback Spanish sentence for one code, or an empty string.

    `warning_conditional` deliberately has none: its reason **is** the warning's
    own `text`, verbatim, and a template here would be a second place for a
    safety string to come from.
    """
    if code == "symptom_primary":
        key = values.get("symptom_key")
        return SYMPTOM_PRIMARY.get(str(key), SYMPTOM_PRIMARY_DEFAULT)
    template = TEMPLATES.get(code)
    return template.format(**values) if template else ""


#: The codes this module can render, checked against the column's own domain so
#: a code added to one and not the other fails a test rather than a screen.
RENDERABLE = frozenset(SUGGESTION_REASON_CODES)
