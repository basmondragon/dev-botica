"""The invitation token: derived, hashed at rest, never in a path."""

import pytest
from django.utils import timezone
from datetime import timedelta

from core import invitations as service
from core.models import Invitation, InvitationStatus, Role
from core.tests.conftest import make_user


def _invitation(tenant, sede, **overrides):
    fields = {
        "tenant": tenant,
        "email": "nueva@la45.co",
        "role": Role.CASHIER,
        "location": sede,
        "expires_at": service.default_expiry(),
        "token_hash": "placeholder",
    }
    fields.update(overrides)
    invitation = Invitation(**fields)
    invitation.token_hash = service.hash_token(service.token_for(invitation))
    invitation.save()
    return invitation


@pytest.mark.django_db
def test_the_token_is_stable_so_a_resend_does_not_rotate_it(tenant_a, sede_a):
    invitation = _invitation(tenant_a, sede_a)
    assert service.token_for(invitation) == service.token_for(invitation)
    assert service.find_by_token(service.token_for(invitation)) == invitation


@pytest.mark.django_db
def test_only_the_hash_is_stored(tenant_a, sede_a):
    invitation = _invitation(tenant_a, sede_a)
    token = service.token_for(invitation)
    assert token not in invitation.token_hash
    assert invitation.token_hash == service.hash_token(token)


@pytest.mark.django_db
def test_a_forged_signature_matches_no_row(tenant_a, sede_a):
    invitation = _invitation(tenant_a, sede_a)
    tenant_hex, invitation_hex, _signature = service.token_for(invitation).split(".")
    assert service.find_by_token(f"{tenant_hex}.{invitation_hex}.forged") is None


@pytest.mark.django_db
def test_the_token_carries_its_own_tenant_so_the_accept_path_pins_first(
    tenant_a, sede_a
):
    invitation = _invitation(tenant_a, sede_a)
    claimed = service.parts(service.token_for(invitation))
    assert claimed == (tenant_a.id, invitation.id)


@pytest.mark.django_db
def test_the_link_puts_the_token_in_a_fragment_and_never_in_a_path(tenant_a, sede_a):
    invitation = _invitation(tenant_a, sede_a)
    token = service.token_for(invitation)
    url = invitation.accept_url(token)
    assert url.endswith(f"/accept#{token}")
    assert f"/accept/{token}" not in url


@pytest.mark.django_db
def test_each_refusal_names_its_own_reason(tenant_a, sede_a):
    used = _invitation(
        tenant_a, sede_a, email="usada@la45.co", status=InvitationStatus.ACCEPTED
    )
    with pytest.raises(service.InvitationRefused) as refusal:
        service.check_consumable(used)
    assert "ya fue usada" in str(refusal.value)

    stale = _invitation(
        tenant_a,
        sede_a,
        email="vencida@la45.co",
        expires_at=timezone.now() - timedelta(days=2),
    )
    with pytest.raises(service.InvitationRefused) as refusal:
        service.check_consumable(stale)
    assert "venció el" in str(refusal.value)

    with pytest.raises(service.InvitationRefused) as refusal:
        service.check_consumable(None)
    assert "No reconocemos" in str(refusal.value)


@pytest.mark.django_db
def test_expiry_is_derived_and_never_stored(tenant_a, sede_a):
    stale = _invitation(tenant_a, sede_a, expires_at=timezone.now() - timedelta(days=2))
    assert stale.status == InvitationStatus.PENDING
    assert stale.state == "expired"


@pytest.mark.django_db
def test_a_failed_delivery_is_a_channel_failure_and_not_an_invalid_invitation(
    tenant_a, sede_a
):
    failed = _invitation(
        tenant_a, sede_a, last_delivery_error="550 después de 5 intentos"
    )
    assert failed.state == "delivery_failed"
    assert service.check_consumable(failed) is failed


@pytest.mark.django_db
def test_who_may_invite_at_what_role(tenant_a, sede_a):
    from core.permissions import may_invite_at

    owner = make_user(tenant_a, Role.OWNER, "owner@la45.co")
    admin = make_user(tenant_a, Role.ADMIN, "admin@la45.co")

    assert may_invite_at(owner, Role.ADMIN)
    assert may_invite_at(owner, Role.CASHIER)
    assert not may_invite_at(owner, Role.PLATFORM_ADMIN)
    assert may_invite_at(admin, Role.CASHIER)
    # Letting an `admin` mint an `owner` is a privilege escalation no audit row
    # undoes.
    assert not may_invite_at(admin, Role.OWNER)
    assert not may_invite_at(admin, Role.ADMIN)
