"""Row-level security on S6's five tables.

The policy is S0's `_plain_policy` verbatim, for the seventh time, because a
table missing one passes every other check in this stage and is a tenant leak --
and the one thing worse than writing the policy seven times is writing it seven
ways. A purchase order names a supplier, its costs and its quantities; a
`demand_forecasts` row is a reading of one network's own rotation. Neither is a
row another network may see.

**None of these five is append-only, and that is deliberate.** `stock_moves` is,
because A3 makes the whole product's stock the sum of it, and `audit_log` is,
because a mutation trail somebody can edit is not a trail. These are documents:
an order moves through `suggested → approved → sent`, a line's
`approved_quantity` is what a buyer edits, a receipt is typed and then confirmed,
and a forecast row is upserted every morning. The runtime role holds the
`UPDATE` that says so.

What is structural instead lives in 0015 and is worth restating, because it is
what this stage's whole measurement rests on: **`suggested_quantity` and
`approved_quantity` are two columns.** No grant enforces that they stay two --
the guarantee is that no endpoint, job or migration in this stage writes the
first after generation, and the suite asserts it.
"""

from django.db import migrations

TABLES = [
    "purchase_orders",
    "purchase_order_lines",
    "goods_receipts",
    "goods_receipt_lines",
    "demand_forecasts",
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
    dependencies = [("core", "0015_purchasing")]

    operations = [
        migrations.RunSQL(
            sql="".join(_plain_policy(table) for table in TABLES),
            reverse_sql="".join(_drop_plain_policy(table) for table in TABLES),
        ),
    ]
