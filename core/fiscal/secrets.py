"""The instance's per-tenant secrets store, and it is not the database.

**A credential never reaches `tenants.settings`** (§9, ledger): a credential in
a JSONB column is a credential every `admin` query can read, that every
`audit_log` row recording a settings write would carry, and that a database dump
handed to a contractor would carry too. It lives in the instance's own
environment, keyed per tenant, and the settings screen shows only whether it
resolved -- there is no field to type one into and no endpoint that returns one.

**The key is the tenant's slug**, which is set at provisioning and never
changes, so rotating a credential is an environment change and a restart rather
than a migration:

    BOTICA_INVOICING_CREDENTIAL_LA_45=...     one network
    BOTICA_INVOICING_CREDENTIAL=...           a single-tenant installation

The slug is uppercased with every non-alphanumeric run folded to `_`, so
`demo-default` reads `BOTICA_INVOICING_CREDENTIAL_DEMO_DEFAULT`.

**A target that needs no credential resolves trivially**, and that is not a hole
in the predicate: `handoff_enabled` asks whether the credential this target
needs is available, and for the file export -- which writes to storage the
instance already owns -- the answer is yes with nothing to look up. A target
that declares `needs_credential` and finds none is **not configured**, which is
what the settings screen says rather than reporting success and failing silently
at 3 a.m.
"""

import os
import re

from django.conf import settings

PREFIX = "BOTICA_INVOICING_CREDENTIAL"

_NOT_ALNUM = re.compile(r"[^A-Z0-9]+")


def env_key(slug: str) -> str:
    """The environment variable one tenant's credential lives under."""
    folded = _NOT_ALNUM.sub("_", (slug or "").upper()).strip("_")
    return f"{PREFIX}_{folded}" if folded else PREFIX


def read(tenant) -> str:
    """This tenant's invoicing credential, or `''` where none is set.

    Falls back to the unsuffixed variable, which is what a single-tenant
    installation sets. `settings.BOTICA_INVOICING_CREDENTIALS` is the same map
    held in Django settings, so a test can populate it without mutating the
    process environment.
    """
    slug = getattr(tenant, "slug", "") or str(tenant or "")
    overrides = getattr(settings, "BOTICA_INVOICING_CREDENTIALS", {}) or {}
    given = overrides.get(slug) or overrides.get("")
    if given:
        return str(given)
    return os.environ.get(env_key(slug), "") or os.environ.get(PREFIX, "")


def resolves(tenant, target) -> bool:
    """Whether the credential this target needs is available.

    Half of `handoff_enabled`, and the only half that can be true while the
    other is false -- a target named in the settings group whose key was never
    put on the instance is the state the settings screen reports as a field
    error on the target block.
    """
    if target is None:
        return False
    if not target.needs_credential:
        return True
    return bool(read(tenant))
