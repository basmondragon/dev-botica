"""Row-level security on S7's two tables.

The policy is S0's `_plain_policy` verbatim, for the eighth time, because a
table missing one passes every other check in this stage and is a tenant leak.
An `elasticity_estimates` row is a reading of one network's own demand and a
`price_proposals` row names what that network charges, what it pays and what it
earns on every reference it sells. Neither is a row another network may see.

**Neither is append-only, and that is deliberate.** `stock_moves` is, because A3
makes the whole product's stock the sum of it, and `audit_log` is, because a
mutation trail somebody can edit is not a trail. These two are recomputed: a run
supersedes the previous run's live proposals in place, and S1's editor stamps a
resolution onto the row a person acted on. The runtime role holds the `UPDATE`
that says so.

**What is structural instead is the absence of a grant, and it lives nowhere in
this file** -- which is the point of A11. This stage needs no write privilege on
`item_prices` at all, so there is no `GRANT` here to describe one, and no
`price_source` value a model could write a price under. Human approval as a
policy is a setting somebody can change; a model with no write path is a
property of the schema, and the suite asserts it by running the whole surface
and reading `item_prices` back unchanged.
"""

from django.db import migrations

TABLES = [
    "elasticity_estimates",
    "price_proposals",
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
    dependencies = [("core", "0017_pricing")]

    operations = [
        migrations.RunSQL(
            sql="".join(_plain_policy(table) for table in TABLES),
            reverse_sql="".join(_drop_plain_policy(table) for table in TABLES),
        ),
    ]
