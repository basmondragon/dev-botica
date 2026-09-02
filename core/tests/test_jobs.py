"""The one job S0 ships, and the property Personas depends on."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core import mail, tasks
from core.models import Invitation, InvitationStatus, Role
from core.tests.conftest import make_location, make_tenant


class _Context:
    """What procrastinate hands a `pass_context` task."""

    def __init__(self, attempts):
        self.job = type("Job", (), {"attempts": attempts})()


def _invitation(tenant, sede, **overrides):
    fields = {
        "tenant": tenant,
        "email": "nueva@la45.co",
        "role": Role.CASHIER,
        "location": sede,
        "token_hash": "a" * 64,
        "expires_at": timezone.now() + timedelta(days=7),
    }
    fields.update(overrides)
    return Invitation.objects.create(**fields)


@pytest.mark.django_db(transaction=True)
def test_the_last_failure_survives_the_retry_that_follows_it(monkeypatch):
    """Acceptance 11 · an invitation whose delivery has failed five times still
    works, because the owner presses `Copiar enlace`. That affordance only
    renders on a row carrying `last_delivery_error` -- and an exception leaving
    the job's pinned transaction would roll the stamp back with it.
    """
    tenant = make_tenant("Droguerías La 45", "la-45")
    sede = make_location(tenant, "CHA")
    invitation = _invitation(tenant, sede)

    def refuse(**_kwargs):
        raise mail.DeliveryFailed("El servidor de correo respondió 550.")

    monkeypatch.setattr(mail, "send_invitation", refuse)

    with pytest.raises(mail.DeliveryFailed):
        tasks.send_invitation_email.func(
            _Context(tasks.MAX_ATTEMPTS - 1),
            tenant_id=str(tenant.id),
            invitation_id=str(invitation.id),
            token="t",
            issued_at=1,
        )

    invitation.refresh_from_db()
    assert "550" in invitation.last_delivery_error
    assert invitation.state == "delivery_failed"
    # And the invitation itself is untouched: email is a delivery channel, not
    # the credential.
    assert invitation.status == InvitationStatus.PENDING


@pytest.mark.django_db(transaction=True)
def test_an_earlier_attempt_does_not_stamp_the_row(monkeypatch):
    """`Envío fallido` is the *exhausted* state. Stamping it on attempt one
    would put a critical badge on a row the queue is about to deliver."""
    tenant = make_tenant("Droguerías La 45", "la-45")
    sede = make_location(tenant, "CHA")
    invitation = _invitation(tenant, sede)

    monkeypatch.setattr(
        mail,
        "send_invitation",
        lambda **_k: (_ for _ in ()).throw(mail.DeliveryFailed("timeout")),
    )

    with pytest.raises(mail.DeliveryFailed):
        tasks.send_invitation_email.func(
            _Context(0),
            tenant_id=str(tenant.id),
            invitation_id=str(invitation.id),
            token="t",
            issued_at=1,
        )

    invitation.refresh_from_db()
    assert invitation.last_delivery_error == ""


@pytest.mark.django_db(transaction=True)
def test_a_delivery_that_succeeds_clears_an_earlier_failure(monkeypatch):
    tenant = make_tenant("Droguerías La 45", "la-45")
    sede = make_location(tenant, "CHA")
    invitation = _invitation(tenant, sede, last_delivery_error="550 hace un rato")

    monkeypatch.setattr(mail, "send_invitation", lambda **_k: mail.SENT)

    tasks.send_invitation_email.func(
        _Context(0),
        tenant_id=str(tenant.id),
        invitation_id=str(invitation.id),
        token="t",
        issued_at=1,
    )

    invitation.refresh_from_db()
    assert invitation.last_delivery_error == ""


@pytest.mark.django_db(transaction=True)
def test_a_revoked_invitation_is_not_delivered(monkeypatch):
    tenant = make_tenant("Droguerías La 45", "la-45")
    sede = make_location(tenant, "CHA")
    invitation = _invitation(tenant, sede, status=InvitationStatus.REVOKED)

    sent = []
    monkeypatch.setattr(mail, "send_invitation", lambda **k: sent.append(k))

    tasks.send_invitation_email.func(
        _Context(0),
        tenant_id=str(tenant.id),
        invitation_id=str(invitation.id),
        token="t",
        issued_at=1,
    )
    assert sent == []
