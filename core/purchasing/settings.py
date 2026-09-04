"""The `purchasing` key group of `tenants.settings` (ledger rule 5).

S6 owns exactly one group and writes it through S0's per-group helper, which
issues a single `jsonb_set` and leaves every other group as it stands.

**Thirteen keys: the ledger's five and eight coined here.** The four regime keys
-- `learned_min_weeks`, `learned_max_rse`, `learned_demote_rse` and
`category_default_min_items` -- replace what would otherwise have been a single
`minimum_history_weeks`, which assumed one threshold for a whole tenant and
would have promoted an analgesic and a dermocosmetic on the same morning (§1).

Every key has a default here rather than in a handler, because the forecast job,
the generation job, the reason-text job and three screens all read them, and a
default that lives at six call sites is six defaults.
"""

from decimal import Decimal

from core import tenant_settings

GROUP = "purchasing"

#: The thirteen, with the defaults *Data* and *API surface* fix.
DEFAULTS: dict[str, object] = {
    # -- the ledger's five ------------------------------------------------
    #: What `safety_stock` and `reorder_point` plan against where the supplier
    #: has no observed lead time yet. Three days is Coopidrogas' own in the
    #: handoff's story, and it is replaced by an observation the first time a
    #: receipt is confirmed against one of that supplier's orders.
    "default_lead_time_days": 3,
    #: How many days of cover an order aims to leave on the shelf. It is also
    #: the horizon the parametric category default carries an item at, and the
    #: rule the `Recortes vs. pedido manual` counterfactual applies flat.
    "target_coverage_days": 30,
    #: The most one order may be worth, in pesos. It is a guard against a
    #: forecast that has gone wrong in one direction on one morning, not a
    #: budget: the least urgent lines are left off and the next morning's run
    #: proposes them again.
    "order_cap_value": 50_000_000,
    #: The most one line may order, expressed in weeks of that item's own
    #: demand. It does not apply in the `parametric` regime, where there is no
    #: weekly figure to multiply.
    "order_cap_weeks_per_line": 8,
    #: The tenant-local hour `forecast.refresh` is fanned out at. Generation
    #: follows an hour later, so the handoff's own `actualizado hoy 06:00` is
    #: true when an administrator opens the screen.
    "refresh_hour": 4,
    # -- coined here (*Gated on*) -----------------------------------------
    #: The service level `safety_stock` is sized for. 95% is `z = 1,65`.
    "service_level": 0.95,
    #: **Promotion is a measurement, not a calendar.** An item reaches
    #: `learned` on its own signal: this many usable weeks after censoring
    #: *and* a relative standard error at or below the next key.
    "learned_min_weeks": 4,
    "learned_max_rse": 0.35,
    #: Demotion carries hysteresis, so the `Por qué` column does not flicker
    #: between a learned claim and a parametric one from Monday to Tuesday.
    "learned_demote_rse": 0.45,
    #: How many items of a category must already be in `learning` or `learned`
    #: before that category's median weekly figure may carry an item that has
    #: none of its own -- parametric path 2, stated as an assumption about the
    #: category and never as a finding about the item.
    "category_default_min_items": 5,
    #: The year-ago category multiplier. It needs 52 weeks of history to exist
    #: at all, so on a tenant under a year old this switch changes nothing.
    "seasonal_multiplier_enabled": True,
    #: Whether `purchase_order.reason_text` calls the model gateway. It gates
    #: this stage's call and nothing else -- S8's `assistant` group holds the
    #: shared kill switch and the per-tenant spend cap.
    "reason_text_enabled": True,
    #: Whether the refresh writes `stock_policies` rows at `source = model`.
    #: A tenant that wants the forecast but not the automatic thresholds turns
    #: it off, and the never-overwrite-a-manual-row rule stops being the only
    #: protection.
    "write_model_stock_policies": True,
}

#: What `PATCH /api/settings/purchasing` may move, and the range each accepts.
#: A lead time of zero would divide a coverage figure by nothing; a target of
#: zero would propose an order of zero on every line and read as a broken model.
BOUNDS: dict[str, tuple[int, int]] = {
    "default_lead_time_days": (1, 120),
    "target_coverage_days": (1, 365),
    "order_cap_value": (1, 100_000_000_000),
    "order_cap_weeks_per_line": (1, 52),
    "refresh_hour": (0, 23),
    "learned_min_weeks": (1, 52),
    "category_default_min_items": (1, 500),
}

#: The three shares, each strictly inside its own open interval. A service level
#: of 1 is an infinite safety stock and a relative standard error of 0 is a
#: promotion nothing ever earns.
SHARES: dict[str, tuple[float, float]] = {
    "service_level": (0.5, 0.999),
    "learned_max_rse": (0.01, 2.0),
    "learned_demote_rse": (0.01, 2.0),
}

FLAGS = (
    "seasonal_multiplier_enabled",
    "reason_text_enabled",
    "write_model_stock_policies",
)


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
    """The bounds, the three shares, the flags, and the one relation."""
    for key, (low, high) in BOUNDS.items():
        whole = values.get(key)
        if not isinstance(whole, int) or isinstance(whole, bool):
            raise Invalid(f"«{key}» debe ser un número entero.")
        if not low <= whole <= high:
            raise Invalid(f"«{key}» debe estar entre {low} y {high}.")

    for key, (floor, ceiling) in SHARES.items():
        share = values.get(key)
        if isinstance(share, bool) or not isinstance(share, (int, float)):
            raise Invalid(f"«{key}» debe ser un número.")
        if not floor <= float(share) <= ceiling:
            raise Invalid(f"«{key}» debe estar entre {floor} y {ceiling}.")

    for key in FLAGS:
        if not isinstance(values.get(key), bool):
            raise Invalid(f"«{key}» es sí o no.")

    # Hysteresis, or the regime flickers. An item promoted at 0,35 and demoted
    # at anything below it changes basis on alternate mornings, and the `Por
    # qué` column teaches an administrator to distrust both readings.
    if float(values["learned_demote_rse"]) <= float(values["learned_max_rse"]):
        raise Invalid(
            "El error estándar de descenso tiene que ser mayor que el de "
            "ascenso: si son iguales, un producto cambia de base cada mañana y "
            "la columna «Por qué» deja de ser creíble."
        )


#: The normal-distribution z for the service levels a droguería would set. Read
#: by interpolation between the nearest two, which is accurate enough for a
#: safety stock and avoids a `scipy` dependency for one number.
_Z_TABLE = (
    (0.50, Decimal("0.00")),
    (0.75, Decimal("0.67")),
    (0.80, Decimal("0.84")),
    (0.85, Decimal("1.04")),
    (0.90, Decimal("1.28")),
    (0.95, Decimal("1.65")),
    (0.975, Decimal("1.96")),
    (0.99, Decimal("2.33")),
    (0.999, Decimal("3.09")),
)


def z_for(service_level) -> Decimal:
    """The multiplier `safety_stock` scales σ by, from the service level."""
    level = float(service_level)
    if level <= _Z_TABLE[0][0]:
        return _Z_TABLE[0][1]
    for (low, low_z), (high, high_z) in zip(_Z_TABLE, _Z_TABLE[1:]):
        if level <= high:
            span = high - low
            if span <= 0:
                return high_z
            share = Decimal(str((level - low) / span))
            return low_z + (high_z - low_z) * share
    return _Z_TABLE[-1][1]
