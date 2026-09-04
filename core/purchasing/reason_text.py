"""The model's prose for one order's lines (§10).

**Quantities always, prose sometimes.** The arithmetic is done before this
module is reached and never waits on it: every line already carries a
`reason_code` and the fixed string that code renders, so a gateway that is
unreachable, switched off or slow costs the order a sentence and nothing else.

**Only `learned` lines are sent.** A language model asked to dress up *we have
no history* writes a finding, and shipping an invented finding is the one thing
this stage must never do (§1). The database says so too: a
`purchase_order_lines` row may carry prose only where `basis = 'learned'`.

What the call buys, and it is the whole reason it exists: a sentence the
arithmetic cannot write. `Sustituto del ibuprofeno en quiebre` requires seeing
another line in the same order, which is why the prompt carries the order rather
than one line at a time.
"""

import json
import logging
from dataclasses import dataclass

from core import gateway
from core.models import ForecastBasis, PurchaseOrderLine

logger = logging.getLogger(__name__)

#: How many lines one call describes. An order of four hundred lines is four
#: hundred sentences nobody reads; the buyer reads the top of the list, which is
#: what the screen sorts to the top anyway.
LINE_BUDGET = 60

SYSTEM = (
    "Eres el analista de compras de una cadena de droguerías colombianas. "
    "Para cada línea de una orden de reposición escribes UNA cláusula corta en "
    "español, de menos de 60 caracteres, que explica por qué se pide esa "
    "cantidad. Reglas que no puedes romper: no inventes datos que no estén en "
    "la línea; no menciones temporadas, tendencias ni rotación si la línea no "
    "los trae; puedes relacionar una línea con otra de la misma orden cuando "
    "eso explique la cantidad. Responde solo con un objeto JSON cuya clave es "
    "el identificador de la línea y cuyo valor es la cláusula."
)


@dataclass(frozen=True)
class Fact:
    """One line as the model sees it.

    A plain record rather than an annotated `PurchaseOrderLine`, because what
    goes to a vendor should be a thing somebody can read in one place -- and
    because a model instance carrying two attributes that are not columns is a
    shape the next reader has to go looking for.
    """

    line_id: str
    item: str
    presentation: str
    quantity: int
    stock: int
    coverage_days: float | None
    reason_code: str
    reason_text: str


def _payload(fact: Fact) -> dict:
    return {
        "id": fact.line_id,
        "producto": fact.item,
        "presentacion": fact.presentation,
        "cantidad": fact.quantity,
        "stock": fact.stock,
        "cobertura_dias": fact.coverage_days,
        "motivo": fact.reason_code,
        "motivo_texto": fact.reason_text,
    }


def write(order, *, tenant, facts) -> int:
    """Fill `reason` on this order's `learned` lines. Returns how many landed.

    The caller has already restricted `facts` to `learned` lines; the update
    below names `basis` again, so a line regenerated under another regime
    between the call and the answer cannot take a learned claim.
    """
    sendable = list(facts)[:LINE_BUDGET]
    if not sendable:
        return 0

    prompt = json.dumps(
        {"lineas": [_payload(fact) for fact in sendable]},
        ensure_ascii=False,
    )
    answer = gateway.complete(tenant=tenant, prompt=prompt, system=SYSTEM)
    try:
        written = json.loads(_json_body(answer["text"]))
    except ValueError as error:
        raise gateway.Unavailable("the gateway answered with no JSON in it") from error
    if not isinstance(written, dict):
        raise gateway.Unavailable("the gateway answered with no object in it")

    by_id = {fact.line_id: fact for fact in sendable}
    landed = 0
    for line_id, clause in written.items():
        fact = by_id.get(str(line_id))
        if fact is None or not isinstance(clause, str):
            continue
        text = clause.strip()[:200]
        if not text:
            continue
        # **A retry replaces prose and never a number** -- this update names one
        # column, and `basis` is in the predicate so a line that has since been
        # regenerated under another regime cannot take a learned claim.
        landed += PurchaseOrderLine.objects.filter(
            id=fact.line_id, basis=ForecastBasis.LEARNED
        ).update(reason=text)
    logger.info("reason text for order %s: %s line(s)", order.id, landed)
    return landed


def _json_body(text: str) -> str:
    """The object inside whatever the model wrapped it in.

    A model that answers with a fenced code block is not a failure worth losing
    an order's prose over, and a model that answers with prose around an object
    is one `find` away from being read correctly.
    """
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1]
        if body.startswith("json"):
            body = body[4:]
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1:
        return body
    return body[start : end + 1]
