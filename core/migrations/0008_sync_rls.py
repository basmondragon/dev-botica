"""Row-level security on S2's two tables.

Copied verbatim from S0's `_plain_policy` and S1's, because a table missing a
policy passes every other check in this stage and is a tenant leak -- and the
one thing worse than writing the policy four times is writing it three ways.

`devices` is the table that matters most here: it holds the hashed credential a
till authenticates with, and the sync endpoints look a device up **by hash**
inside the pinned transaction. Without `FORCE ROW LEVEL SECURITY` that lookup
would be a cross-tenant read on a unique index -- a device key from one network
resolving inside another. With it, the lookup finds nothing, which is the same
answer as a bad key.
"""

from django.conf import settings
from django.db import migrations

SYNC_TABLES = ["devices", "sync_conflicts"]

# ---------------------------------------------------------------------------
# The departure a barcode could not otherwise be served.
#
# `item_barcodes` belongs to the registry under the predicate *its item is
# active*, and a pull serves a departure by re-scanning a row whose
# `updated_at` has moved. Deactivating an item moves `items.updated_at` and
# **not** the barcode's, so every barcode already behind a device's cursor
# would stay on that till forever, resolving a scan to a product the shop no
# longer sells.
#
# This is the mechanism the stage document assumes exists when it says a
# registry row leaves by an update: for `items` that is `active`, for
# `item_prices` it is `effective_to`, and for `item_barcodes` it is this. It is
# a trigger and not application code because the catalog is written by an
# endpoint, by a load tool and by a fixture, and a touch any one of the three
# could forget is a till that quietly keeps a dead barcode.
# ---------------------------------------------------------------------------

BARCODE_FOLLOWS_ITEM = r"""
CREATE FUNCTION app_barcodes_follow_item_activation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- `clock_timestamp()`, not `now()`: `now()` is the **transaction's** start
    -- time, so a load tool deactivating items in one transaction would stamp
    -- every barcode with a timestamp earlier than the rows it is following, and
    -- the departure would land behind the device's own cursor. The stage
    -- document asks for `updated_at` on a registry table to be stamped as late
    -- as possible in its writing transaction, and this is that.
    UPDATE item_barcodes SET updated_at = clock_timestamp() WHERE item_id = NEW.id;
    RETURN NULL;
END;
$$;

CREATE TRIGGER barcodes_follow_item_activation
    AFTER UPDATE OF active ON items
    FOR EACH ROW
    WHEN (OLD.active IS DISTINCT FROM NEW.active)
    EXECUTE FUNCTION app_barcodes_follow_item_activation();
"""

BARCODE_FOLLOWS_ITEM_REVERSE = r"""
DROP TRIGGER IF EXISTS barcodes_follow_item_activation ON items;
DROP FUNCTION IF EXISTS app_barcodes_follow_item_activation();
"""

# The trigger runs as the caller, so its `UPDATE` is confined to the pinned
# tenant like every other write -- which is what we want: it can only ever
# touch barcodes of the item that was just changed, in the same network.
GRANTS = (
    'GRANT EXECUTE ON FUNCTION app_barcodes_follow_item_activation() '
    'TO "%(runtime)s";\n'
)
GRANTS_REVERSE = (
    'REVOKE EXECUTE ON FUNCTION app_barcodes_follow_item_activation() '
    'FROM "%(runtime)s";\n'
)


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
    dependencies = [("core", "0007_sync")]

    operations = [
        migrations.RunSQL(
            sql="".join(_plain_policy(table) for table in SYNC_TABLES),
            reverse_sql="".join(
                _drop_plain_policy(table) for table in reversed(SYNC_TABLES)
            ),
        ),
        migrations.RunSQL(
            sql=BARCODE_FOLLOWS_ITEM + GRANTS % _roles(),
            reverse_sql=GRANTS_REVERSE % _roles() + BARCODE_FOLLOWS_ITEM_REVERSE,
        ),
    ]
