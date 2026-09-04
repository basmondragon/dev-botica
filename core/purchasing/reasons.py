"""The reason vocabulary: one code per line, and the string it renders as.

**The split between arithmetic and prose is the whole point of this module.**
Every line gets exactly one `reason_code`, computed and always present. The
gateway call turns the whole order's codes into one Spanish clause per line,
which is what lets it write a sentence the arithmetic cannot -- `Sustituto del
ibuprofeno en quiebre` requires seeing another line in the same order. When
there is no prose, the code's fixed string renders, and the order is fully
usable (§10).

**A code declares which regimes it is admissible in, and the resolver honours
it.** That is what makes acceptance 25 structural rather than editorial: two of
these codes are facts about a shelf and fire on a tenant's first morning; every
other one is a claim about demand and is admissible only where demand has been
measured. A `parametric` line claiming a pollen season is the single failure
this stage exists to prevent, and a pharmacist who knows the category notices it
inside a minute.
"""

from core.models import ForecastBasis

PARAMETRIC = ForecastBasis.PARAMETRIC
LEARNING = ForecastBasis.LEARNING
LEARNED = ForecastBasis.LEARNED

ANY_BASIS = (PARAMETRIC, LEARNING, LEARNED)
MEASURED = (LEARNING, LEARNED)

#: `code -> (admissible bases, fallback string)`.
#:
#: The strings are the handoff's own where it draws one. `En quiebre, hay 96 en
#: Suba` and `Rotación estable, mantiene 45 días` carry a figure, so they are
#: formats rather than constants and the resolver fills them; the rest are fixed.
CODES: dict[str, tuple[tuple, str]] = {
    "seasonal_peak_recent_stockout": (
        (LEARNED,),
        "Pico de temporada + quiebre reciente",
    ),
    "stable_rotation": ((LEARNED,), "Rotación estable, mantiene {coverage} días"),
    "stockout_available_elsewhere": (
        ANY_BASIS,
        "En quiebre, hay {elsewhere} en {sede}",
    ),
    "seasonal_peak": ((LEARNED,), "Sube con la temporada de polen"),
    "predictable_chronic": ((LEARNED,), "Crónico, demanda predecible"),
    "sufficient_coverage": (MEASURED, "Cobertura suficiente, no pedir"),
    "lot_expiring": (ANY_BASIS, "Lote actual vence en {months} meses"),
    "overstock": (MEASURED, "Sobrestock, liberar capital"),
    "cross_sell_pair": (MEASURED, "Se vende junto con {partner}"),
    # **Coined here**, and it closes a gap the stage document's own table
    # leaves. Every line carries exactly one code, always -- and a `learning`
    # line can never reach `stable_rotation`, whose confidence condition its own
    # 0,65 cap forbids, nor `predictable_chronic` if its demand is noisy. Such a
    # line would otherwise carry no code at all and render an empty `Por qué`,
    # which is the one cell on this screen that must never be blank. It says the
    # least a measured line can honestly say: the quantity came from sales
    # measured at this sede, and nothing more is claimed.
    "measured_demand": (MEASURED, "Reposición por venta medida en la sede"),
    "learning_floor": (
        (LEARNING,),
        "Con pocas semanas de venta · sostenido por el punto de reorden",
    ),
    "parametric_policy": (
        (PARAMETRIC,),
        "Sin histórico · sugerido por el punto de reorden de la sede",
    ),
    "parametric_category_default": (
        (PARAMETRIC,),
        "Sin histórico · cobertura por defecto de la categoría",
    ),
}

#: **First admissible match wins**, and the order is by what a buyer acts on.
#:
#: The two facts come first: a shelf at zero with units at another sede is the
#: most actionable thing on the screen, and a short-dated lot changes what you
#: buy before any claim about rotation does. The seasonal pair follows, then the
#: two zeros, then the floor and the two parametric paths, and the three quiet
#: descriptions of a healthy reference come last -- they are true of most of the
#: catalog most of the time and would otherwise mask everything above them.
PRECEDENCE = (
    "stockout_available_elsewhere",
    "lot_expiring",
    "seasonal_peak_recent_stockout",
    "seasonal_peak",
    "overstock",
    "sufficient_coverage",
    "learning_floor",
    "parametric_policy",
    "parametric_category_default",
    "predictable_chronic",
    "stable_rotation",
    "cross_sell_pair",
    # Last, and it is the floor of the vocabulary: it fires only where nothing
    # more specific is true.
    "measured_demand",
)

#: The codes whose prose the model may rewrite. Only a `learned` line is sent at
#: all, so this is the whole of what the gateway ever sees.
SENDABLE_BASIS = LEARNED


def admits(code: str, basis) -> bool:
    """Whether a code may be attached to a line generated under this regime."""
    entry = CODES.get(code)
    return entry is not None and basis in entry[0]


def resolve(fired: dict, basis) -> tuple[str, str]:
    """The one code this line carries, and its rendered fallback string.

    `fired` maps a candidate code to the substitutions its string needs; a code
    with no substitutions maps to an empty dict. A code the regime does not
    admit is skipped rather than refused -- the arithmetic that fired it is
    still true, it is simply not a thing this line is allowed to claim.

    Returns `("", "")` where nothing fired, which happens only on a manual line:
    a generated line always carries at least one of the codes above.
    """
    for code in PRECEDENCE:
        if code not in fired or not admits(code, basis):
            continue
        return code, render(code, fired[code])
    return "", ""


def render(code: str, values: dict | None = None) -> str:
    """One code's fixed string, with its figures filled in.

    A missing substitution is a defect in the caller, not a reason to render a
    sentence with a hole in it -- so the format is applied strictly and the
    bare template is returned only when the code takes no values at all.
    """
    entry = CODES.get(code)
    if entry is None:
        return ""
    template = entry[1]
    if not values:
        return template
    return template.format(**values)
