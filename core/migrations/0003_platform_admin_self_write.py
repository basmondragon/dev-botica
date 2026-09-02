"""Let a platform admin write their own row, and nothing else.

`users`' policy already reads one row outside every pin: the acting identity's
own, when it belongs to no network. Signing in *writes* that row -- Django
stamps `last_login` -- and a `WITH CHECK` that did not admit the same branch
refused it, which locked every platform admin out of Django admin.

Widening the check to the same predicate admits exactly that one row and no
other. An insert cannot use the branch to create a second null-tenant user: to
satisfy it the new row would have to claim the acting identity's own id, and
that id is already taken. With no acting identity pinned, `app_current_user()`
is null and the branch is false.
"""

from django.db import migrations

FORWARD = r"""
DROP POLICY tenant_isolation ON users;
CREATE POLICY tenant_isolation ON users
    FOR ALL
    USING (
        tenant_id = app_current_tenant()
        OR (tenant_id IS NULL AND id = app_current_user())
    )
    WITH CHECK (
        tenant_id = app_current_tenant()
        OR (tenant_id IS NULL AND id = app_current_user())
    );
"""

REVERSE = r"""
DROP POLICY tenant_isolation ON users;
CREATE POLICY tenant_isolation ON users
    FOR ALL
    USING (
        tenant_id = app_current_tenant()
        OR (tenant_id IS NULL AND id = app_current_user())
    )
    WITH CHECK (tenant_id = app_current_tenant());
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0002_rls")]

    operations = [migrations.RunSQL(sql=FORWARD, reverse_sql=REVERSE)]
