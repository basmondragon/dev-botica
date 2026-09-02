"""Row-level security on S3's eight tables, and the grant that makes
`stock_moves` append-only.

The policies are S0's `_plain_policy` verbatim, for the fourth time, because a
table missing one passes every other check in this stage and is a tenant leak --
and the one thing worse than writing the policy four times is writing it four
ways.

**The second operation is the one this stage exists for.** A3 says no code
updates a quantity in place, and `stock_moves` is append-only. Eleven stage
documents saying so is a convention; a `REVOKE` is a property. The runtime role
holds INSERT and SELECT on `stock_moves` and holds neither UPDATE nor DELETE, so
an endpoint, a job, a management command or a future stage's "correction" that
tried to edit a movement fails at the database with a permission error rather
than succeeding quietly and corrupting the ledger and the projection
consistently -- which is the one failure the rebuild check cannot see, because
it makes both sides agree on the same wrong number.

`check_rls` asserts both halves, so a later migration that granted them back
turns red on the next run rather than at an inspection.

**This is also why `stock_moves.user_id` is a stamped uuid and not a foreign
key**: `ON DELETE SET NULL` would issue an `UPDATE` on this table when an owner
hard-deletes a person, and there is no grant for it -- so every such delete
would fail with a permission error. `audit_log` reached the same conclusion at
S0 and took the same answer.

**`stock_on_hand` is deliberately not append-only.** It is a projection: the
rebuild deletes and rewrites it, which is exactly what makes it disposable.
"""

from django.conf import settings
from django.db import migrations

INVENTORY_TABLES = [
    "lots",
    "stock_moves",
    "stock_on_hand",
    "stock_policies",
    "transfers",
    "transfer_lines",
    "stock_counts",
    "stock_count_lines",
]

APPEND_ONLY = 'REVOKE UPDATE, DELETE ON stock_moves FROM "%(runtime)s";\n'
APPEND_ONLY_REVERSE = 'GRANT UPDATE, DELETE ON stock_moves TO "%(runtime)s";\n'


def _roles():
    return {"runtime": settings.BOTICA_RUNTIME_DB_USER}


def _plain_policy(table):
    """S0's form, copied verbatim (architecture §3, A1)."""
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = app_current_tenant());\n"
    )


def _drop_plain_policy(table):
    return (
        f"DROP POLICY IF EXISTS tenant_isolation ON {table};\n"
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;\n"
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0009_inventory")]

    operations = [
        migrations.RunSQL(
            sql="".join(_plain_policy(table) for table in INVENTORY_TABLES),
            reverse_sql="".join(
                _drop_plain_policy(table) for table in reversed(INVENTORY_TABLES)
            ),
        ),
        migrations.RunSQL(
            sql=APPEND_ONLY % _roles(),
            reverse_sql=APPEND_ONLY_REVERSE % _roles(),
        ),
    ]
