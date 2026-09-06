"""The `assistant` key group of `tenants.settings` (ledger rule 5).

S8 owns exactly one group and writes it through S0's per-group helper, which
issues a single `jsonb_set` and leaves every other group as it stands.

**Fourteen keys, and none of them is about the advisory notice.** There is
nowhere to put a flag that removes *"Con fiebre de más de dos días, remitir a
consulta médica. Botica no diagnostica."*, which is A8 as a property of the
schema rather than as a review comment (S8, check 3).

**Two switches, and they are different.** `enabled = false` removes the
assistant column from Mostrador entirely and S4's `BUSCAR PRODUCTO` list returns
to the full-height left column it held before this stage landed.
`model_enabled = false` -- or the spend cap being reached, or §11.3 answering
*no* -- puts every till in `modo local` permanently: the chips, the filter, the
ranking, the cards and the notice all still work, and no transcript ever reaches
a model provider. That is the shape §11.3 needs -- *"a 'no' costs no code"* --
and it is why the switch is two booleans rather than one.

**`model_enabled` ships off.** §11.3 is unanswered until a pilot answers it, and
a default that sent customer symptom text to a vendor on the first morning would
make the gate a thing somebody has to remember to close.
"""

from decimal import Decimal

from core import tenant_settings

GROUP = "assistant"

#: The fourteen, with the defaults *API surface* fixes.
DEFAULTS: dict[str, object] = {
    #: The column on Mostrador. Off is not a degraded assistant -- it is S4's
    #: screen, exactly as it was.
    "enabled": True,
    #: Whether a transcript may reach a model provider at all (§11.3). Off until
    #: somebody decides, and off costs no code.
    "model_enabled": False,
    #: Empty means the deployment's own configured model. A tenant that names
    #: one names it here.
    "model": "",
    #: Enforced on the request path against the month's own `cost_usd` sum, not
    #: by a job: a cap a job enforces is a cap that is a day late.
    "monthly_spend_cap_usd": 25.0,
    #: The card shows `El asistente está tardando más de lo normal.` at 2,5 s
    #: and resolves to the local recommendation here, so this is necessarily
    #: above that.
    "model_timeout_ms": 4000,
    #: Health data (§11.3). Off ships the extracted keys and never the words.
    "retain_transcripts": False,
    "transcript_retention_days": 30,
    #: `symptom_key -> [category_id]`, per tenant, because **no shipped file
    #: knows a tenant's category ids**. It is minutes of an administrator's work
    #: and it is the one precondition of the cold-start floor.
    "symptom_category_map": {},
    #: How many cards card C draws. Three is the handoff's own row and is one
    #: per type.
    "suggestion_card_count": 3,
    #: The miner's floors and its cap. **The cap is enforced by the job that
    #: writes the rows**, never by the predicate that reads them.
    "cross_sell_min_support": 25,
    "cross_sell_min_confidence": 0.15,
    "cross_sell_rules_per_item": 4,
    "cross_sell_window_days": 90,
    #: The share of queries whose model answer the output check discards, above
    #: which `assistant.health_check` raises. **A check that is too tight
    #: discards good recommendations invisibly**, which is why the rate is a
    #: measured number with an alert rather than something somebody notices.
    "output_check_alert_rate": 0.02,
}

FLAGS = ("enabled", "model_enabled", "retain_transcripts")

#: `key -> (low, high)` for the two shares. A cap of zero is a tenant that has
#: turned the model off by another route and is legal; a negative one is not.
SHARE_BOUNDS: dict[str, tuple[float, float]] = {
    "monthly_spend_cap_usd": (0.0, 10_000.0),
    "cross_sell_min_confidence": (0.0, 1.0),
    "output_check_alert_rate": (0.0, 1.0),
}

WHOLE_BOUNDS: dict[str, tuple[int, int]] = {
    #: Below 500 ms nothing but a local network answers, and above 8 s a cashier
    #: has already read the card and moved on.
    "model_timeout_ms": (500, 8000),
    "transcript_retention_days": (1, 365),
    #: One card is not a choice and six do not fit the column at 1280×720.
    "suggestion_card_count": (1, 5),
    "cross_sell_min_support": (1, 10_000),
    "cross_sell_rules_per_item": (1, 20),
    "cross_sell_window_days": (7, 730),
}


class Invalid(ValueError):
    """A settings write the group refuses, named in Spanish for the field."""


def read(tenant) -> dict:
    """The group, with every unwritten key standing at its default."""
    stored = tenant_settings.read_group(tenant, GROUP)
    return {**DEFAULTS, **stored}


def write(tenant, values: dict) -> dict:
    """Write the group through S0's helper. Returns what was written."""
    merged = {**read(tenant), **values}
    check(merged)
    tenant_settings.write_group(tenant, GROUP, merged)
    return merged


def check(values: dict) -> None:
    for key in FLAGS:
        if not isinstance(values.get(key), bool):
            raise Invalid(f"«{key}» es sí o no.")

    for key, (low, high) in SHARE_BOUNDS.items():
        number = values.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise Invalid(f"«{key}» debe ser un número.")
        if not low <= float(number) <= high:
            raise Invalid(f"«{key}» debe estar entre {low} y {high}.")

    for key, (low_whole, high_whole) in WHOLE_BOUNDS.items():
        whole = values.get(key)
        if not isinstance(whole, int) or isinstance(whole, bool):
            raise Invalid(f"«{key}» debe ser un número entero.")
        if not low_whole <= whole <= high_whole:
            raise Invalid(f"«{key}» debe estar entre {low_whole} y {high_whole}.")

    if not isinstance(values.get("model"), str):
        raise Invalid("«model» es el nombre del modelo, o vacío.")
    check_map(values.get("symptom_category_map"))


def check_map(mapping) -> dict:
    """The symptom-to-category map, validated against the closed vocabulary.

    A key outside it is refused here rather than dropped: a map naming a symptom
    the extractor cannot emit seeds nothing, and it does it silently.
    """
    from core.assistant.vocabulary import SYMPTOM_KEYS

    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise Invalid(
            "«symptom_category_map» asocia cada síntoma con una lista de categorías."
        )
    checked: dict[str, list[str]] = {}
    for key, categories in mapping.items():
        if key not in SYMPTOM_KEYS:
            raise Invalid(
                f"«{key}» no es un síntoma que el asistente sepa extraer, así "
                "que asociarlo a una categoría no serviría de nada."
            )
        if not isinstance(categories, list) or any(
            not isinstance(one, str) for one in categories
        ):
            raise Invalid(
                f"Las categorías de «{key}» son una lista de identificadores."
            )
        checked[key] = list(categories)
    return checked


def spend_cap(values) -> Decimal:
    return Decimal(str(values.get("monthly_spend_cap_usd", 0)))


def min_confidence(values) -> Decimal:
    return Decimal(str(values.get("cross_sell_min_confidence", 0)))
