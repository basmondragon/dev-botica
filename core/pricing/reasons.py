"""The reason vocabulary, and it is **fixed and closed** (§10, *UI*).

Every sentence the Precios screen and its record panel show is composed here,
from a `reason_code`, an `elasticity_status` or a `no_proposal_reason`, with the
row's own figures interpolated. **S7 calls no LLM.** §10 puts OpenRouter on S6
and S8 only, and the consequence is stated as a property rather than a policy: a
suggestion on this surface cannot be explained by text that was not derived from
its own arithmetic, because there is no code path that could produce any.

The figures go through the §A.11 forms on the way in -- `$15.600`, `24,8%`,
`+1,9 pp`, `02/10`, U+2212 for a negative -- because the sentence is assembled
once, server-side, and a client that re-formatted half of it would produce two
spellings of one number in one line.
"""

from datetime import date
from decimal import Decimal

from core.models import ElasticityStatus, NoProposalReason

#: U+2212 MINUS SIGN, never a hyphen (§A.11).
MINUS = "−"


def pesos(amount) -> str:
    """`$15.600` -- prefixed, unspaced, thousands dot, no decimals.

    Never abbreviated: `M` belongs to a KPI tile and never to a sentence or a
    table cell, which is where every figure this module interpolates is read.
    """
    if amount is None:
        return "—"
    whole = int(Decimal(str(amount)).quantize(Decimal("1")))
    body = "$" + f"{abs(whole):,}".replace(",", ".")
    return f"{MINUS}{body}" if whole < 0 else body


def percent(value) -> str:
    """`24,8%` -- decimal comma, one place, no space before the sign."""
    if value is None:
        return "—"
    return f"{_one_decimal(value)}%"


def points(value) -> str:
    """`5,0 pp` -- percentage points, unsigned, for a gap that is a distance."""
    if value is None:
        return "—"
    return f"{_one_decimal(abs(Decimal(str(value))))} pp"


def coefficient(value) -> str:
    """`−0,34` -- a β, at two places, with U+2212 for the sign it almost always
    has. Two places rather than one because the difference between `−0,94` and
    `−1,04` is the difference between a raise and a veto."""
    if value is None:
        return "—"
    number = Decimal(str(value)).quantize(Decimal("0.01"))
    body = f"{abs(number)}".replace(".", ",")
    return f"{MINUS}{body}" if number < 0 else body


def ratio(value) -> str:
    """`0,41` -- an r², at two places."""
    if value is None:
        return "—"
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}".replace(".", ",")


def day(value: date | None) -> str:
    """`02/10` -- a day and a month (§A.11)."""
    return "—" if value is None else f"{value:%d/%m}"


def _one_decimal(value) -> str:
    number = Decimal(str(value)).quantize(Decimal("0.1"))
    body = f"{abs(number):,.1f}".replace(",", " ").replace(".", ",")
    body = body.replace(" ", ".")
    return f"{MINUS}{body}" if number < 0 else body


# ---------------------------------------------------------------------------
# The estimate's own sentence
# ---------------------------------------------------------------------------


def elasticity_sentence(estimate) -> str:
    """Why this reference has the estimate it has -- or has none.

    **On a `margin_rule` proposal this is not an apology and is not hidden**: it
    renders as the reason the margin rule owns the reference, which is a
    statement of method. `Un solo precio en 26 semanas` says exactly what is
    missing, and waiting does not fix it.
    """
    status = estimate.status
    if status == ElasticityStatus.ESTIMATED:
        return (
            f"Elasticidad {coefficient(estimate.elasticity)} sobre "
            f"{estimate.observations} semanas · r² {ratio(estimate.r2)}."
        )
    if status == ElasticityStatus.INSUFFICIENT_VARIATION:
        if estimate.distinct_prices <= 1:
            return "Un solo precio en 26 semanas. Sin variación no hay elasticidad que estimar."
        return (
            f"{estimate.distinct_prices} precios en 26 semanas, demasiado "
            "parecidos entre sí. Sin variación no hay elasticidad que estimar."
        )
    if status == ElasticityStatus.INSUFFICIENT_OBSERVATIONS:
        return (
            f"{estimate.observations} semanas con venta después de excluir "
            f"quiebres. Se necesitan {MIN_OFFER_OBSERVATIONS}."
        )
    if status == ElasticityStatus.NO_SALES:
        return "Sin ventas en la ventana de 26 semanas."
    if status == ElasticityStatus.NO_COST:
        return "Sin costo cargado. No se puede calcular margen."
    return "Referencia inactiva en el catálogo."


#: Repeated from the estimator so the sentence above and the floor it names
#: cannot drift apart. The estimator imports it from here.
MIN_OFFER_OBSERVATIONS = 12


# ---------------------------------------------------------------------------
# The proposal's own sentence
# ---------------------------------------------------------------------------


def proposal_sentence(proposal, *, estimate=None, goal=None) -> str:
    """The record panel's first line for a reference that got a suggestion.

    Composed from `reason_code` and the row's own stored figures. There is no
    branch here that reaches a model, a template a person edits, or a string
    another surface could spell differently -- S9's margin reports render these
    same strings for these same codes.
    """
    code = proposal.reason_code
    if code == "inelastic_raise":
        units = units_lost(proposal, estimate)
        impact = proposal.estimated_monthly_impact
        tail = (
            f" cuesta unas {units} unidades al mes y suma {pesos(impact)}."
            if units is not None and impact is not None
            else "."
        )
        beta = coefficient(estimate.elasticity if estimate else None)
        weeks = estimate.observations if estimate else 0
        return (
            f"Demanda poco sensible al precio · β {beta} sobre {weeks} semanas. "
            f"Subir {percent(abs(proposal.step_pct))}{tail}"
        )
    if code == "elastic_reduce":
        beta = coefficient(estimate.elasticity if estimate else None)
        return (
            f"Demanda sensible al precio · β {beta}. Bajar "
            f"{percent(abs(proposal.step_pct))} gana más volumen del que cuesta "
            "en margen."
        )
    if code == "cap_bound_raise":
        return (
            f"Limitado por el tope regulado de "
            f"{pesos(proposal.regulated_max_price_at_proposal)}. La propuesta es "
            "el precio máximo permitido."
        )
    if code == "margin_below_goal":
        return (
            f"Margen {percent(proposal.current_margin)} frente a la meta de "
            f"{percent(goal)} · subir {percent(abs(proposal.step_pct))} lo deja "
            f"en {percent(proposal.projected_margin)}. Faltan "
            f"{points(proposal.margin_gap_pp)} para la meta. Sin elasticidad "
            "estimada: el impacto supone que el volumen no cambia."
        )
    if code == "above_regulated_cap":
        # **Not a pricing opportunity -- a compliance finding.** The till is
        # charging above the legal maximum today, and the sentence says so with
        # both figures rather than leaving the badge to carry it alone.
        return (
            f"El precio actual de {pesos(proposal.current_price)} supera el tope "
            f"regulado de {pesos(proposal.regulated_max_price_at_proposal)}. "
            "Corrija el precio en el catálogo."
        )
    return ""


def units_lost(proposal, estimate) -> int | None:
    """How many units a month the projected rise costs -- `unas 11 unidades`.

    `q̂₃₀ · (1 − (p₁/p₀)^β)`, from the two figures the row already stores. It is
    computed here rather than stored because it is a **restatement** of the
    trailing volume and the elasticity, and a fifth column that had to agree
    with four others is a fifth column that can disagree with them.

    Null wherever either input is: a reference with no trailing volume has no
    unit figure to lose, and the sentence drops the clause rather than printing
    a zero.
    """
    units = proposal.trailing_monthly_units
    beta = estimate.elasticity if estimate is not None else None
    if units is None or beta is None or not proposal.current_price:
        return None
    ratio_of_prices = Decimal(proposal.suggested_price) / Decimal(
        proposal.current_price
    )
    if ratio_of_prices <= 0:
        return None
    projected = Decimal(units) * _power(ratio_of_prices, Decimal(str(beta)))
    return int(abs(Decimal(units) - projected).quantize(Decimal("1")))


def _power(base: Decimal, exponent: Decimal) -> Decimal:
    """`base ** exponent` in floating point, answered as a `Decimal`.

    The exponent is a fitted coefficient and the base is a price ratio, so this
    is statistics rather than money: `Decimal` carries no `**` for a fractional
    exponent, and rounding a projected unit count at the sixth place changes
    nothing a screen shows.
    """
    return Decimal(str(float(base) ** float(exponent)))


def no_proposal_sentence(reason, *, proposal_figures) -> str:
    """Why an item that **was** evaluated got no proposal anyway.

    `proposal_figures` is a plain mapping of whatever the engine computed on the
    way to deciding not to suggest anything -- the margin it measured, the cap
    it found, the impact that fell short. A sentence that named none of them
    would be a badge with extra words.
    """
    figures = proposal_figures
    if reason == NoProposalReason.LOW_CONFIDENCE:
        return (
            f"Estimación de baja confianza · r² {ratio(figures.get('r2'))}. "
            "No se propone."
        )
    if reason == NoProposalReason.BELOW_MATERIALITY:
        return (
            f"Impacto estimado {pesos(figures.get('impact'))} al mes. Por debajo "
            "del mínimo para proponer."
        )
    if reason == NoProposalReason.COOLDOWN:
        return (
            f"El precio cambió hace {figures.get('days_since', 0)} días. Se "
            f"vuelve a evaluar el {day(figures.get('eligible_on'))}."
        )
    if reason == NoProposalReason.CAP_BLOCKS_RAISE:
        return (
            "Sin tope regulado conocido. Las alzas están desactivadas para esta "
            "referencia."
        )
    if reason == NoProposalReason.CAP_AT_CURRENT:
        return (
            "El precio actual ya está en el tope regulado de "
            f"{pesos(figures.get('cap'))}."
        )
    if reason == NoProposalReason.AT_MARGIN_GOAL:
        return (
            f"Margen {percent(figures.get('margin'))}, por encima de la meta de "
            f"{percent(figures.get('goal'))}. No hay nada que ajustar."
        )
    if reason == NoProposalReason.MARGIN_GAP_IMMATERIAL:
        return (
            f"Margen {percent(figures.get('margin'))} frente a la meta de "
            f"{percent(figures.get('goal'))}. La diferencia no alcanza el mínimo "
            "para proponer un ajuste."
        )
    if reason == NoProposalReason.ELASTIC_VETO:
        return (
            f"Estimación débil pero sensible al precio · β "
            f"{coefficient(figures.get('elasticity'))} con r² "
            f"{ratio(figures.get('r2'))}. No se propone un alza sobre esta "
            "referencia."
        )
    return ""
