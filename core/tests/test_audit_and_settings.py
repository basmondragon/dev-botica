"""The audit write path and the settings helper (acceptance 15-17)."""

import pytest

from core import audit, tenant_settings
from core.models import AuditAction, AuditLog, Role
from core.tests.conftest import make_user


@pytest.mark.django_db
def test_a_row_records_actor_action_entity_and_one_of_before_or_after(tenant_a):
    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    row = audit.record(
        actor=owner,
        tenant_id=tenant_a.id,
        action=AuditAction.UPDATE,
        entity_type="users",
        entity_id=owner.id,
        before={"role": "cashier"},
        after={"role": "admin"},
        request_id="req_0001",
    )
    assert row.actor_email == owner.email
    assert row.request_id == "req_0001"


@pytest.mark.django_db
def test_an_action_outside_the_closed_vocabulary_is_refused(tenant_a):
    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    with pytest.raises(ValueError):
        audit.record(
            actor=owner,
            tenant_id=tenant_a.id,
            action="role_changed",
            entity_type="users",
            after={"role": "admin"},
        )


@pytest.mark.django_db
def test_a_row_with_neither_a_before_nor_an_after_is_a_defect(tenant_a):
    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    with pytest.raises(ValueError):
        audit.record(
            actor=owner,
            tenant_id=tenant_a.id,
            action=AuditAction.UPDATE,
            entity_type="users",
        )


@pytest.mark.django_db
def test_actor_email_survives_a_hard_delete(tenant_a):
    """`owner` may hard delete, and a mutation attributed to nobody is not a
    record.

    Every stage referencing `users` stamps the human-readable identity it needs
    at write time, and `audit_log` is where that half of the rule carries the
    whole weight: the actor id is a stamped column rather than a reference,
    because the runtime role holds no UPDATE on this table and a cascade could
    not run even if one were declared.
    """
    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    audit.record(
        actor=owner,
        tenant_id=tenant_a.id,
        action=AuditAction.CREATE,
        entity_type="users",
        after={"email": owner.email},
    )
    owner_id = owner.id
    owner.delete()
    row = AuditLog.objects.get(tenant_id=tenant_a.id)
    assert row.actor_user_id == owner_id
    assert row.actor_email == "owner@la45.co"


@pytest.mark.django_db
def test_writing_one_settings_group_leaves_its_neighbours_byte_identical(tenant_a):
    """Ledger rule 5: one `jsonb_set` per group, never a read-modify-write."""
    tenant_settings.write_group(tenant_a, "pricing", {"margin_goal": 22})
    tenant_settings.write_group(
        tenant_a, "tenant", {"legal_name": "Droguerías La 45 S.A.S."}
    )
    tenant_a.refresh_from_db()
    assert tenant_a.settings["pricing"] == {"margin_goal": 22}
    assert tenant_a.settings["tenant"]["legal_name"] == "Droguerías La 45 S.A.S."


@pytest.mark.django_db
def test_a_write_that_matches_no_row_raises_rather_than_passing_quietly(tenant_a):
    """Under RLS a write against the wrong pin updates nothing silently, and a
    200 would tell an owner their margin goal was saved when it was not."""
    import uuid

    with pytest.raises(tenant_settings.UnknownTenant):
        tenant_settings.write_group(uuid.uuid4(), "tenant", {"legal_name": "x"})


@pytest.mark.django_db
def test_an_unregistered_group_is_refused(tenant_a):
    with pytest.raises(ValueError):
        tenant_settings.write_group(tenant_a, "invented", {})
