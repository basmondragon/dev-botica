"""The catalog's security, search and depth layers, in SQL.

Three things live here and each is invisible in every functional check:

1. **Row-level security on all nine tables**, copied verbatim from S0's
   `_plain_policy`. A table missing a policy passes every other check in this
   stage and is a tenant leak.
2. **The trigram indexes** over `items.search_name` and
   `manufacturers.search_name` -- the generated, accent-free, lowercased columns
   0005 declares -- which is what lets the catalog's one search field answer
   inside §4's 400ms budget over four thousand rows.
3. **The two-level category rule**, as a trigger. A row CHECK cannot see the
   parent row it would have to read, so the depth limit is a trigger or it is
   only a convention -- and "never three levels" is a claim S9's rollups will
   rely on.

The four enum types, the immutable `app_unaccent` wrapper and every table are in
0005, created before anything here names them.
"""

from django.conf import settings
from django.db import migrations

#: The nine tables S1 creates. Every one carries the plain tenant predicate.
#: Stated literally here rather than read from the models, because a migration
#: that imported app code would change meaning the day that code did.
CATALOG_TABLES = [
    "manufacturers",
    "categories",
    "suppliers",
    "items",
    "item_barcodes",
    "supplier_items",
    "item_prices",
    "customers",
    "imports",
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


# The search field matches four things: the product name, the laboratorio's
# name, any barcode and the registro INVIMA. A barcode and a registration are
# matched exactly and are served by their own indexes; a name and a laboratorio
# are matched as a prefix and as a trigram, so `losar` finds `Losartán 50 mg ×
# 30` -- and an unanchored ILIKE over four thousand rows without these is a
# sequential scan on every keystroke.
#
# pg_trgm is a trusted extension, so the migration role -- which owns the
# database but is not a superuser -- may create it.
SEARCH = r"""
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX items_search_name_trigram
    ON items USING gin (search_name gin_trgm_ops);
CREATE INDEX manufacturers_search_name_trigram
    ON manufacturers USING gin (search_name gin_trgm_ops);
"""

SEARCH_REVERSE = r"""
DROP INDEX IF EXISTS manufacturers_search_name_trigram;
DROP INDEX IF EXISTS items_search_name_trigram;
"""

# Two levels, enforced, never three. The trigger fires on the child rather than
# on the parent, so it refuses the row that would create the third level; a
# category that already has children cannot then be re-parented, which is the
# other direction of the same rule and is checked in the same function.
CATEGORY_DEPTH = r"""
CREATE FUNCTION app_categories_are_two_levels() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        IF NEW.parent_id = NEW.id THEN
            RAISE EXCEPTION 'a category cannot be its own parent';
        END IF;
        IF EXISTS (SELECT 1 FROM categories
                    WHERE id = NEW.parent_id AND parent_id IS NOT NULL) THEN
            RAISE EXCEPTION
                'categories are two levels deep and this row would be a third';
        END IF;
        IF EXISTS (SELECT 1 FROM categories WHERE parent_id = NEW.id) THEN
            RAISE EXCEPTION
                'this category already has children and cannot become a child';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER categories_are_two_levels
    BEFORE INSERT OR UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION app_categories_are_two_levels();
"""

CATEGORY_DEPTH_REVERSE = r"""
DROP TRIGGER IF EXISTS categories_are_two_levels ON categories;
DROP FUNCTION IF EXISTS app_categories_are_two_levels();
"""

# The trigger reads `categories` to decide, and under FORCE ROW LEVEL SECURITY a
# plpgsql function runs as the caller -- so the parent lookup is confined to the
# pinned tenant like every other read. That is the behaviour we want: a parent
# in another network is not found, and the row is refused for naming a parent
# that does not exist rather than accepted for reaching across.

GRANTS = 'GRANT EXECUTE ON FUNCTION app_categories_are_two_levels() TO "%(runtime)s";\n'
GRANTS_REVERSE = (
    'REVOKE EXECUTE ON FUNCTION app_categories_are_two_levels() FROM "%(runtime)s";\n'
)


def _roles():
    return {"runtime": settings.BOTICA_RUNTIME_DB_USER}


class Migration(migrations.Migration):
    dependencies = [("core", "0005_catalog")]

    operations = [
        migrations.RunSQL(
            sql="".join(_plain_policy(table) for table in CATALOG_TABLES),
            reverse_sql="".join(
                _drop_plain_policy(table) for table in reversed(CATALOG_TABLES)
            ),
        ),
        migrations.RunSQL(sql=SEARCH, reverse_sql=SEARCH_REVERSE),
        migrations.RunSQL(
            sql=CATEGORY_DEPTH + GRANTS % _roles(),
            reverse_sql=GRANTS_REVERSE % _roles() + CATEGORY_DEPTH_REVERSE,
        ),
    ]
