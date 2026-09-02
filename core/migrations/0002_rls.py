"""Row-level security, the one unpinned lookup, and the audit-log grant.

A1 is three conditions and all three are established here: the runtime role owns
no tables (docker/initdb/00-roles.sql), every policy table carries FORCE ROW
LEVEL SECURITY, and every context pins the tenant inside a transaction
(core.tenancy).

This file is the second half of `core.models` and every later stage copies
`_plain_policy` verbatim.
"""

from django.conf import settings
from django.db import migrations

#: The tables that carry the plain tenant predicate.
TENANT_TABLES = ["locations", "invitations", "audit_log"]

FORWARD = r"""
CREATE FUNCTION app_current_tenant() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

CREATE FUNCTION app_current_user() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

-- The one permitted unpinned query, and the only function in the system that
-- reads a tenant table outside a pin. It returns two uuids: a user id and a
-- tenant id. If its return type is ever widened to a row, the single audited
-- hole becomes an unpinned read path into a tenant table.
CREATE FUNCTION app_resolve_sign_in(p_email text)
RETURNS TABLE (user_id uuid, tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT id, tenant_id FROM users WHERE lower(email) = lower(p_email) LIMIT 1
$$;

-- `tenants` has no tenant_id -- it *is* the tenant -- so its policy is keyed on
-- `id`. A policy written against a column that is not there is a policy that
-- silently allows everything.
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
    FOR ALL
    USING (id = app_current_tenant())
    WITH CHECK (id = app_current_tenant());

-- The picker, and the only widening in the system. It widens exactly one thing:
-- SELECT on `tenants` itself, so a platform admin can choose a network in
-- Django admin. It reaches no tenant-scoped table, and it is off unless
-- core.tenancy.tenant_picker turned it on for the current transaction.
CREATE POLICY platform_admin_may_list ON tenants
    FOR SELECT
    USING (current_setting('app.platform_admin', true) = 'on');

-- `users` carries the one documented exception. A platform_admin belongs to no
-- network, so their row has a null tenant_id and is invisible under every pin --
-- and Django cannot load `request.user` without reading it. The second branch
-- admits exactly that one row: the acting identity's own, and only when it
-- belongs to no network. WITH CHECK excludes it, so no pinned insert can write
-- a null-tenant row.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON users
    FOR ALL
    USING (
        tenant_id = app_current_tenant()
        OR (tenant_id IS NULL AND id = app_current_user())
    )
    WITH CHECK (tenant_id = app_current_tenant());
"""

REVERSE = r"""
DROP POLICY IF EXISTS tenant_isolation ON users;
ALTER TABLE users NO FORCE ROW LEVEL SECURITY;
ALTER TABLE users DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS platform_admin_may_list ON tenants;
DROP POLICY IF EXISTS tenant_isolation ON tenants;
ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tenants DISABLE ROW LEVEL SECURITY;
DROP FUNCTION IF EXISTS app_resolve_sign_in(text);
DROP FUNCTION IF EXISTS app_current_user();
DROP FUNCTION IF EXISTS app_current_tenant();
"""


def _plain_policy(table):
    """The form every later stage copies verbatim (architecture §3, A1)."""
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


# Append-only enforced by the database, not by the discipline of eleven stage
# documents. A REVOKE refuses; a rule that quietly does nothing would let a
# later stage's "correction" pass its own tests.
APPEND_ONLY = 'REVOKE UPDATE, DELETE ON audit_log FROM "%(runtime)s";\n'

APPEND_ONLY_REVERSE = 'GRANT UPDATE, DELETE ON audit_log TO "%(runtime)s";\n'


def _roles():
    return {"runtime": settings.BOTICA_RUNTIME_DB_USER}


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql=FORWARD + "".join(_plain_policy(t) for t in TENANT_TABLES),
            reverse_sql="".join(_drop_plain_policy(t) for t in TENANT_TABLES)
            + REVERSE,
        ),
        migrations.RunSQL(
            sql=APPEND_ONLY % _roles(),
            reverse_sql=APPEND_ONLY_REVERSE % _roles(),
        ),
    ]
