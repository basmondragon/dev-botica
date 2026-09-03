"""Row-level security on S4's six tables.

The policies are S0's `_plain_policy` verbatim, for the fifth time, because a
table missing one passes every other check in this stage and is a tenant leak --
and the one thing worse than writing the policy five times is writing it five
ways.

**None of the six is append-only, and that is deliberate.** `stock_moves` is
append-only because A3 makes the whole product's stock the sum of it. A sale is
not that shape: a ticket opens, closes and is sometimes voided, and each of
those is one row moving through a state its own CHECK constraints admit. What
makes a sale trustworthy is not that the row never moves -- it is that every
unit it took off a shelf is an append in `stock_moves` that nothing can edit,
and that a void writes reversing moves rather than deleting anything.
"""

from django.db import migrations

COUNTER_TABLES = [
    "shifts",
    "sales",
    "sale_lines",
    "payments",
    "sale_returns",
    "sale_return_lines",
]


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
    dependencies = [("core", "0011_counter")]

    operations = [
        migrations.RunSQL(
            sql="".join(_plain_policy(table) for table in COUNTER_TABLES),
            reverse_sql="".join(
                _drop_plain_policy(table) for table in reversed(COUNTER_TABLES)
            ),
        ),
    ]
