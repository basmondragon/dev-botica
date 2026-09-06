"""The `sync` key group of `tenants.settings` (ledger rule 5).

S2 owns exactly one group and writes it through S0's per-group helper, which
issues a single `jsonb_set` and leaves every other group as it stands. A
read-modify-write of the whole column would take out S0's `tenant` group and
would only be noticed on the screen it belongs to, weeks later.

Every key has a default here rather than in a handler, because the pull loop,
the push cap, the horizon and the retention rule all read them and a default
that lives at three call sites is three defaults.
"""

from core import tenant_settings

GROUP = "sync"

#: The ten keys, with the defaults *Data* fixes. They are a starting point
#: measured against the pilot, not constants: the horizon in particular is
#: measured against the longest observed write transaction on a registry table
#: (§11.5).
DEFAULTS: dict[str, object] = {
    #: How often a visible leader tab pulls.
    "pull_interval_seconds": 8,
    #: Rows per pull page, per collection.
    "pull_page_size": 500,
    #: The push batch caps. A batch over either is refused rather than split by
    #: the server, because splitting is the client's job and a server that
    #: silently halves a batch makes the outbox and the response disagree.
    "push_batch_max_rows": 200,
    "push_batch_max_bytes": 1048576,
    #: `updated_at <= now() - this`. A row is stamped when its statement runs
    #: and becomes visible when its transaction commits, so a pull that served
    #: right up to `now()` would advance a device's cursor past a row that had
    #: not appeared yet -- and that row would never be served again.
    "pull_safety_horizon_seconds": 2,
    #: How long a till keeps a confirmed event locally. **Nothing unpushed is
    #: compacted at any age**, which is a client rule and not a number.
    "local_retention_days": 30,
    #: Beyond this the sync panel and the office list say how far out the
    #: device's clock is. It is never used to correct `occurred_at`.
    "clock_skew_warn_seconds": 90,
    #: The `customers` window. A proxy for recency, because at S2 there are no
    #: sales to key it on; S4 narrows it to *seen at this location*.
    "customer_recency_months": 24,
    #: `warn` lets a browser that refused persistence claim a device and shows
    #: the condition everywhere; `required` refuses the claim.
    "storage_persistence_policy": "warn",
    #: A device quieter than this raises a `device_silent` conflict.
    "stale_device_hours": 48,
}

#: What `PATCH /api/settings/sync` may move, and the range each accepts. A
#: pull interval of zero is a till hammering the server; a horizon of zero
#: reopens the commit-ordering hole the horizon exists to close.
BOUNDS: dict[str, tuple[int, int]] = {
    "pull_interval_seconds": (2, 300),
    "pull_page_size": (50, 2000),
    "push_batch_max_rows": (10, 1000),
    "push_batch_max_bytes": (65536, 8388608),
    "pull_safety_horizon_seconds": (1, 60),
    "local_retention_days": (1, 365),
    "clock_skew_warn_seconds": (10, 86400),
    "customer_recency_months": (1, 240),
    "stale_device_hours": (1, 720),
}

POLICIES = ("warn", "required")


def read(tenant) -> dict:
    """The group, with every unwritten key standing at its default."""
    return {**DEFAULTS, **tenant_settings.read_group(tenant, GROUP)}


#: The keys a **registry predicate** reads that belong to another stage's group.
#: The registry declares one predicate per collection and `pull.py` evaluates it
#: over `options`, so a collection scoped by a setting its owner holds needs that
#: setting in the same mapping. S8's `cross_sell_rules` is the first: its
#: membership floor and its per-anchor cap are two keys of the `assistant` group.
#:
#: **They are copied in rather than read at the predicate**, because the
#: predicate runs once per row of every page and `/api/sync/pull` has a 20 ms
#: p95 budget (§4) -- a settings read inside it would be a query per page per
#: collection.
BORROWED = ("cross_sell_min_support", "cross_sell_rules_per_item")


def options(tenant) -> dict:
    """What every registry predicate is evaluated against: this group, plus the
    handful of keys another stage's group owns and the registry reads."""
    from core.assistant import settings as assistant_settings

    borrowed = assistant_settings.read(tenant)
    return {**read(tenant), **{key: borrowed[key] for key in BORROWED}}


def write(tenant, values: dict) -> dict:
    """Write the group through S0's helper. Returns what was written."""
    merged = {**read(tenant), **values}
    tenant_settings.write_group(tenant, GROUP, merged)
    return merged
