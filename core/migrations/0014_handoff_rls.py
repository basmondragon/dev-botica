"""Row-level security on S5's one table.

The policy is S0's `_plain_policy` verbatim, for the sixth time, because a table
missing one passes every other check in this stage and is a tenant leak -- and
the one thing worse than writing the policy six times is writing it six ways.

**`fiscal_documents` is not append-only**, and that is deliberate. `stock_moves`
is, because A3 makes the whole product's stock the sum of it, and `audit_log`
is, because a mutation trail somebody can edit is not a trail. A handoff is
neither: one row moves through `pending → sent → acknowledged`, gathers attempts
and a reason, and is rebuilt and re-sent when an administrator fixes the cause.
`failed` is not terminal here -- this is a handoff, not a filing -- so the
runtime role holds the `UPDATE` that says so.

What is structural instead is `UNIQUE (tenant_id, document_key)` in 0013: the
key is derived from the sale, so a second row for one document cannot exist,
which is the invariant this stage is actually shaped around.
"""

from django.db import migrations

TABLE = "fiscal_documents"


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
    dependencies = [("core", "0013_handoff")]

    operations = [
        migrations.RunSQL(
            sql=_plain_policy(TABLE),
            reverse_sql=_drop_plain_policy(TABLE),
        ),
    ]
