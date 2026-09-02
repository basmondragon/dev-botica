"""Tenant isolation, proved rather than assumed (acceptance 2-4, 18; A1)."""

import pytest
from django.conf import settings
from django.db import connection, transaction

from core.tests.conftest import make_location, make_user
from core.models import Role
from core.tenancy import repin

EXEMPT = ("django_", "auth_", "account_", "socialaccount_", "procrastinate_")


def _tenant_relations():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   pg_get_userbyid(c.relowner)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND (c.relname = 'tenants'
                   OR EXISTS (SELECT 1 FROM pg_attribute a
                              WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                                AND NOT a.attisdropped))
            ORDER BY c.relname
            """
        )
        return cursor.fetchall()


@pytest.mark.django_db
def test_every_tenant_table_is_enabled_and_forced():
    """A table missing either is a table the runtime role reads across tenants."""
    relations = [row for row in _tenant_relations() if not row[0].startswith(EXEMPT)]
    assert relations, "no tenant-scoped tables were found at all"
    for name, enabled, forced, _owner in relations:
        assert enabled, f"{name}: row security not enabled"
        assert forced, f"{name}: row security not FORCED"


@pytest.mark.django_db
def test_the_runtime_role_owns_no_table():
    """A table's owner bypasses its own RLS whatever the policy says (A1)."""
    for name, _enabled, _forced, owner in _tenant_relations():
        assert owner != settings.BOTICA_RUNTIME_DB_USER, f"{name} is owned by runtime"


@pytest.mark.django_db(transaction=False)
def test_a_pinned_read_sees_one_network_and_an_unpinned_one_sees_none(
    tenant_a, tenant_b, as_runtime_role
):
    make_location(tenant_a, "CHA")
    make_user(tenant_a, Role.OWNER, "a@la45.co")
    make_user(tenant_b, Role.OWNER, "b@estrella.co")

    as_runtime_role()

    repin(tenant_a.id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM users")
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT count(*) FROM users WHERE tenant_id <> %s", [str(tenant_a.id)]
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT count(*) FROM tenants")
        assert cursor.fetchone()[0] == 1

    repin(tenant_b.id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM users")
        assert cursor.fetchone()[0] == 1

    # No pin at all: zero rows, not another network's. This is the count that
    # proves the role is a non-owner under FORCE ROW LEVEL SECURITY rather than
    # proving the application remembered to filter.
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")
        for table in ("users", "locations", "invitations", "tenants"):
            cursor.execute(f"SELECT count(*) FROM {table}")
            assert cursor.fetchone()[0] == 0, f"{table} answered an unpinned read"


@pytest.mark.django_db
def test_the_audit_log_refuses_update_and_delete_by_grant(tenant_a, as_runtime_role):
    """Append-only is a grant, not the discipline of eleven stage documents."""
    from django.db.utils import ProgrammingError

    for statement in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(ProgrammingError):
            with transaction.atomic():
                as_runtime_role()
                with connection.cursor() as cursor:
                    cursor.execute(statement)


@pytest.mark.django_db(transaction=False)
def test_a_platform_admin_may_write_their_own_row_and_no_other(
    tenant_a, as_runtime_role
):
    """Their row belongs to no network, so no pin matches it -- and signing in
    writes it, because Django stamps `last_login`. The policy admits that one
    row and cannot be used to mint a second."""
    import uuid

    from django.db.utils import ProgrammingError

    from core.models import User
    from core.tenancy import NO_TENANT

    admin = User.objects.create_platform_admin(
        email="plataforma@particulatech.us", name="Plataforma", password="x" * 14
    )

    as_runtime_role()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.tenant_id', %s, true), "
            "       set_config('app.user_id', %s, true)",
            [str(NO_TENANT), str(admin.id)],
        )
        # Their own row: readable and writable.
        cursor.execute("SELECT count(*) FROM users")
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "UPDATE users SET last_login = now() WHERE id = %s", [str(admin.id)]
        )
        assert cursor.rowcount == 1

        # A second null-tenant row is not reachable through the same branch.
        with pytest.raises(ProgrammingError):
            cursor.execute(
                "INSERT INTO users (id, created_at, updated_at, email, name, role, "
                "status, platform_admin, is_staff, is_superuser, password) "
                "VALUES (%s, now(), now(), 'otro@x.co', 'Otro', 'platform_admin', "
                "'active', true, true, true, 'x')",
                [str(uuid.uuid4())],
            )


@pytest.mark.django_db(transaction=False)
def test_the_two_enums_are_real_types_and_every_status_is_checked(tenant_a, sede_a):
    """Django `choices` are a form-layer convenience: they emit a plain varchar
    and stop nothing. The threat S0 names is not the API -- it is "a management
    command or a bad backfill", which goes nowhere near a serializer."""
    from django.db.utils import DataError, IntegrityError

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT typname FROM pg_type WHERE typname IN ('role', 'location_type')"
        )
        assert {row[0] for row in cursor.fetchall()} == {"role", "location_type"}

    for statement, params in (
        (
            "UPDATE users SET role = %s WHERE tenant_id = %s",
            ["Owner", str(tenant_a.id)],
        ),
        (
            "UPDATE locations SET type = %s WHERE tenant_id = %s",
            ["pharmacy", str(tenant_a.id)],
        ),
    ):
        with pytest.raises(DataError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement, params)

    for statement, params in (
        (
            "UPDATE tenants SET status = %s WHERE id = %s",
            ["muy-activa", str(tenant_a.id)],
        ),
        (
            "UPDATE locations SET status = %s WHERE id = %s",
            ["en_montaje", str(sede_a.id)],
        ),
    ):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement, params)


@pytest.mark.django_db(transaction=False)
def test_a_hard_delete_leaves_the_trail_alone(tenant_a, as_runtime_role):
    """`owner` may hard delete, and the runtime role holds no UPDATE on
    `audit_log` -- so a cascade that nulled the actor would make every delete
    fail with a permission error. The trail stamps `actor_email` instead."""
    from core import audit
    from core.models import AuditAction, AuditLog
    from core.tenancy import repin

    person = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    audit.record(
        actor=person,
        tenant_id=tenant_a.id,
        action=AuditAction.CREATE,
        entity_type="users",
        after={"email": person.email},
    )

    as_runtime_role()
    repin(tenant_a.id, user_id=person.id)
    person_id = person.id
    person.delete()

    row = AuditLog.objects.get(tenant_id=tenant_a.id)
    assert row.actor_user_id == person_id
    assert row.actor_email == "owner@la45.co"


@pytest.mark.django_db(transaction=False)
def test_a_sede_with_a_cashier_homed_to_it_is_not_deletable(tenant_a):
    """SET_NULL would write the exact row `cashier_has_a_home_location`
    forbids. A sede with people on it is closed, not deleted."""
    from django.db.models import ProtectedError

    home = make_location(tenant_a, "CHA")
    make_user(tenant_a, Role.CASHIER, "cashier@la45.co", location=home)
    with pytest.raises(ProtectedError):
        home.delete()
