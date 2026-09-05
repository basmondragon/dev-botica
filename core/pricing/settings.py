"""The `pricing` key group of `tenants.settings` (ledger rule 5).

S7 owns exactly one group and writes it through S0's per-group helper, which
issues a single `jsonb_set` and leaves every other group as it stands.

**Five keys: the register's three and two coined here.** The two additions are
`min_days_between_changes`, which is the cooldown that keeps this screen from
asking for 3% a week -- 42% a year -- and `rounding_unit`, because `$15.637` is
not a price a droguería charges.

**`margin_goal_pct` ships unset, and that is the day-one state rather than an
edge case.** A number we chose would arrive on the screen wearing the same badge
as the owner's own goal, and `22,0` would look like a finding about their
business rather than a placeholder we picked. Until an owner sets it the margin
rule suggests nothing and says why, S9 renders `Sin meta definida`, and the
elasticity engine carries on regardless. Setting it is an onboarding step and
takes about ten seconds.
"""

from decimal import Decimal

from core import tenant_settings

GROUP = "pricing"

#: The five, with the defaults *API surface* fixes. `margin_goal_pct` is absent
#: from this mapping on purpose: a key with no default is a key `read()` answers
#: `None` for, and every consumer has to render its absence rather than a
#: fallback number.
DEFAULTS: dict[str, object] = {
    #: The largest move a suggestion from **either** engine may propose. For an
    #: inelastic reference, and for every margin-rule reference, this *is* the
    #: shape of the advice -- which is exactly why it belongs to a human and not
    #: to the model.
    "max_single_step_pct": 3.0,
    #: Whether an item at `cap_status = unknown` may be suggested upward, by
    #: either engine. **Off**, so on a tenant where nobody has loaded a cap the
    #: margin rule proposes nothing upward either -- the day-one behaviour
    #: §11.4 actually governs.
    "allow_raise_without_cap": False,
    #: A reference whose price changed within this window is not suggested
    #: again -- whoever changed it, and whether or not a suggestion informed it.
    "min_days_between_changes": 30,
    #: Suggestions round to this many pesos.
    "rounding_unit": 50,
}

#: **Deliberately shipped with no default** (*API surface*). It is the Panel's
#: `≥ 22%` reference, the `Margen proyectado` tile's target marker and the
#: margin rule's only input, and S9 reads the same key.
GOAL_KEY = "margin_goal_pct"

#: What a PATCH may move, and the range each accepts. A margin goal at or above
#: 100% has no finite price that reaches it; a step of zero proposes nothing and
#: reads as a broken engine; a rounding unit of zero divides by nothing.
BOUNDS: dict[str, tuple[float, float]] = {
    GOAL_KEY: (0.1, 95.0),
    "max_single_step_pct": (0.1, 50.0),
}

WHOLE_BOUNDS: dict[str, tuple[int, int]] = {
    "min_days_between_changes": (0, 365),
    "rounding_unit": (1, 10_000),
}

FLAGS = ("allow_raise_without_cap",)


class Invalid(ValueError):
    """A settings write the group refuses, named in Spanish for the field."""


def read(tenant) -> dict:
    """The group, with every unwritten key standing at its default.

    `margin_goal_pct` answers **`None`** where nobody has set it, and every
    caller branches on that rather than substituting a number.
    """
    stored = tenant_settings.read_group(tenant, GROUP)
    values: dict = {**DEFAULTS, GOAL_KEY: None, **stored}
    return values


def write(tenant, values: dict) -> dict:
    """Write the group through S0's helper. Returns what was written."""
    merged = {**read(tenant), **values}
    check(merged)
    tenant_settings.write_group(tenant, GROUP, merged)
    return merged


def check(values: dict) -> None:
    """The two shares, the two whole numbers and the one flag.

    `margin_goal_pct` is checked **only when it is set**: clearing it is a legal
    write and is how a tenant returns to the first-morning state.
    """
    for key, (low, high) in BOUNDS.items():
        number = values.get(key)
        if number is None:
            if key == GOAL_KEY:
                continue
            raise Invalid(f"«{key}» es obligatorio.")
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

    for key in FLAGS:
        if not isinstance(values.get(key), bool):
            raise Invalid(f"«{key}» es sí o no.")


def goal(values) -> Decimal | None:
    """The margin goal as a `Decimal` in **points**, or `None` where unset.

    Every margin figure in this stage is in points -- `32.80` is 32,8% -- so the
    settings group, the columns and the screen cannot disagree about a factor of
    a hundred.
    """
    raw = values.get(GOAL_KEY)
    return None if raw is None else Decimal(str(raw))
