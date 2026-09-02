"""One key group of `tenants.settings`, written without erasing its neighbours.

Ledger rule 5: one column, one owner per key group. One owner per group does not
by itself stop one stage's write erasing another's -- a read-modify-write of the
whole column does erase it -- so every group goes through one helper that issues
a single `jsonb_set` per group and leaves every other group as it stands.
"""

import json

from django.db import connection

#: The groups this column carries, and the stage that owns each. S0 writes
#: `tenant`; the other seven land empty and are written by their owners.
GROUPS = {
    "tenant": "S0",
    "sync": "S2",
    "inventory": "S3",
    "invoicing": "S5",
    "purchasing": "S6",
    "pricing": "S7",
    "assistant": "S8",
    "compliance": "S10",
}


class UnknownTenant(LookupError):
    """The settings UPDATE matched no tenant row.

    Under RLS a write against the wrong pin updates nothing silently, and a 200
    on a write that touched no row tells an owner their margin goal was saved
    when it was not.
    """


def read_group(tenant, group):
    """One key group, or an empty mapping where the group has not been written."""
    _check(group)
    return (getattr(tenant, "settings", None) or {}).get(group) or {}


def write_group(tenant, group, value):
    """Write one key group on one tenant, leaving every other group untouched."""
    _check(group)
    tenant_id = getattr(tenant, "id", tenant)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE tenants "
            "SET settings = jsonb_set("
            "        coalesce(settings, '{}'::jsonb), %s, %s::jsonb, true), "
            "    updated_at = now() "
            "WHERE id = %s",
            [[group], json.dumps(value), str(tenant_id)],
        )
        written = cursor.rowcount
    if written != 1:
        raise UnknownTenant(
            f"No tenant row matched {tenant_id} in this transaction, so the "
            f"{group!r} settings group was not written."
        )
    if hasattr(tenant, "settings"):
        tenant.settings = {**(tenant.settings or {}), group: value}


def _check(group):
    if group not in GROUPS:
        raise ValueError(
            f"{group!r} is not a settings key group. The register lives in "
            "core.tenant_settings.GROUPS and in ownership.md."
        )
