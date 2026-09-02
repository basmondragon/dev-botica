"""The `inventory` key group of `tenants.settings` (ledger rule 5).

S3 owns exactly one group and writes it through S0's per-group helper, which
issues a single `jsonb_set` and leaves every other group as it stands. A
read-modify-write of the whole column would take out S0's `tenant` group and
S2's `sync` group and would only be noticed on the screen they belong to, weeks
later.

Every key has a default here rather than in a handler, because the `Estado`
derivation, the expiry chip, the digest job and `GET /api/stock/expiring` all
read them and a default that lives at four call sites is four defaults.
"""

from core import tenant_settings

GROUP = "inventory"

#: The seven keys, with the defaults *Data* fixes.
DEFAULTS: dict[str, object] = {
    #: The window the handoff draws, and the denominator of S9's
    #: `4,6% del inventario valorizado` tile.
    "expiry_valuation_days": 90,
    #: The `expiring_urgent` state -- critical, hollow dot.
    "expiry_alert_days": 180,
    #: The `expiring` state -- warning, hollow dot. Outside this window a lot's
    #: expiry contributes no state at all.
    "expiry_notice_days": 365,
    #: Whether a lot chosen against FEFO order requires a reason. **`deny` is
    #: not an available value**: §6 fixes that the override is recorded rather
    #: than prevented, and a setting the architecture forbids is a defect, not a
    #: configuration.
    "fefo_override_policy": "allow_with_reason",
    #: Whether a transfer dispatch or a supplier return may exceed the
    #: projection. **A sale is never subject to this key** (§5 rule 2), and the
    #: check that reads it lives at those two endpoints and never in the ledger
    #: service -- putting it in the service would make the offline sale path
    #: depend on a rule that must not apply to it.
    "negative_stock_block_outbound": True,
    #: How often a location is expected to run a cycle count; drives the
    #: `Conteos` due list.
    "count_cadence_days": 30,
    #: Who receives the daily expiry digest. **Empty means the digest is not
    #: sent and the state still renders** -- the work list is the screen, and
    #: the email is a convenience over it.
    "expiry_digest_recipients": [],
}

#: What `PATCH /api/settings/inventory` may move, and the range each accepts. An
#: alert horizon of zero would empty a badge family; a notice horizon inside the
#: alert one would make the two windows disagree, which the write refuses below
#: rather than rendering.
BOUNDS: dict[str, tuple[int, int]] = {
    "expiry_valuation_days": (1, 3650),
    "expiry_alert_days": (1, 3650),
    "expiry_notice_days": (1, 3650),
    "count_cadence_days": (1, 365),
}

#: One value, and there is no second. `deny` is refused rather than accepted and
#: ignored, because a setting that silently does nothing is worse than one that
#: refuses.
FEFO_POLICIES = ("allow_with_reason",)


class Invalid(ValueError):
    """A settings write the group refuses, named in Spanish for the field."""


def read(tenant) -> dict:
    """The group, with every unwritten key standing at its default."""
    return {**DEFAULTS, **tenant_settings.read_group(tenant, GROUP)}


def write(tenant, values: dict) -> dict:
    """Write the group through S0's helper. Returns what was written."""
    merged = {**read(tenant), **values}
    check(merged)
    tenant_settings.write_group(tenant, GROUP, merged)
    return merged


def check(values: dict) -> None:
    """The bounds, and the one relation between two of them."""
    for key, (low, high) in BOUNDS.items():
        given = values.get(key)
        if not isinstance(given, int) or isinstance(given, bool):
            raise Invalid(f"«{key}» debe ser un número entero de días.")
        if not low <= given <= high:
            raise Invalid(f"«{key}» debe estar entre {low} y {high} días.")

    if values["expiry_alert_days"] > values["expiry_notice_days"]:
        raise Invalid(
            "La ventana de aviso no puede ser más corta que la de alerta: un "
            "lote entraría en «Vence pronto» sin haber pasado antes por "
            "«Vence»."
        )
    if values["fefo_override_policy"] not in FEFO_POLICIES:
        raise Invalid(
            "El único valor de «fefo_override_policy» es "
            "«allow_with_reason». §6 fija que el cambio de lote se registra, "
            "no se impide."
        )
    if not isinstance(values["negative_stock_block_outbound"], bool):
        raise Invalid("«negative_stock_block_outbound» es sí o no.")

    recipients = values["expiry_digest_recipients"]
    if not isinstance(recipients, list) or any(
        not isinstance(one, str) for one in recipients
    ):
        raise Invalid("Los destinatarios del resumen son una lista de correos.")
