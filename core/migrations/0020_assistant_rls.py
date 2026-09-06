"""Row-level security on S8's four tables.

The policy is S0's `_plain_policy` verbatim, for the ninth time, because a table
missing one passes every other check in this stage and is a tenant leak. An
`assistant_queries` row carries what a customer said about their own health, an
`assistant_suggestions` row says what a network offers and what it sells, a
`cross_sell_rules` row is a reading of one network's own trading, and an
`item_warnings` row is that network's safety layer. Not one of the four is a row
another network may see.

**None of the four is append-only, and that is deliberate.** `stock_moves` is,
because A3 makes the whole product's stock the sum of it, and `audit_log` is,
because a mutation trail somebody can edit is not a trail. These four are
written and then written again by design: a suggestion is offered and later
accepted, a query is superseded, a warning is deactivated rather than deleted,
and a mining run replaces its own scope's rules in place. The runtime role holds
the `UPDATE` that says so.

**What is structural instead is the absence of a DELETE path in this stage's
code.** `DELETE /api/item-warnings/{id}` sets `active = false`, because a
registry collection that is hard-deleted leaves no row to serve a departure
marker for and lives on every till forever (S2, criterion 14). That is a
property of the endpoint rather than of a grant: `cross_sell_rules` is rewritten
per scope by the miner and genuinely needs the grant.
"""

from django.db import migrations

TABLES = [
    "item_warnings",
    "cross_sell_rules",
    "assistant_queries",
    "assistant_suggestions",
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
    dependencies = [("core", "0019_assistant")]

    operations = [
        migrations.RunSQL(
            sql="".join(_plain_policy(table) for table in TABLES),
            reverse_sql="".join(_drop_plain_policy(table) for table in TABLES),
        ),
    ]
