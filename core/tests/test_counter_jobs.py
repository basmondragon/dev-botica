"""The one job, and the count is the point.

Everything on the counter's critical path is synchronous by construction:
applying a pushed sale happens inside the push transaction, because rule 7
requires the projection to be maintained in the same transaction as the moves.
What is left is a notice, and the thing worth checking about it is that **it
closes nothing**.
"""

from datetime import timedelta

import pytest
from django.core import mail as django_mail
from django.utils import timezone

from core.counter import jobs as counter_jobs
from core.models import Shift, ShiftStatus, UserStatus
from core.tenancy import pin_tenant
from core.tests.test_counter_push import Till, apply
from core.tests.test_sync_pull import make_device

pytestmark = pytest.mark.django_db


def open_shift(tenant, location, user, *, hours_ago=0, label="Caja 1"):
    device, _key = make_device(tenant, location, label=label)
    till = Till(device, user)
    apply(device, [till.open_shift()], batch_id=f"open-{label}")
    with pin_tenant(tenant.id):
        shift = Shift.objects.get(tenant=tenant, device=device)
        if hours_ago:
            Shift.objects.filter(id=shift.id).update(
                opened_at=timezone.now() - timedelta(hours=hours_ago)
            )
            shift.refresh_from_db()
        return shift


def test_a_turno_open_more_than_a_day_is_notified(
    tenant_a, sede_a, owner_a, admin_a, cashier_a, settings
):
    """The notice names the till, who opened it and when, and goes to the
    tenant's `owner` and `admin` through S0's transactional email."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    shift = open_shift(tenant_a, sede_a, cashier_a, hours_ago=30)

    notified = counter_jobs.stale_shift_notice(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert notified == 1
    assert len(django_mail.outbox) == 2
    recipients = {message.to[0] for message in django_mail.outbox}
    assert recipients == {owner_a.email, admin_a.email}
    assert "turnos sin cerrar" in django_mail.outbox[0].subject

    # **The job closes nothing.** Closing a cash session without a count
    # destroys the count, which is the one number the session exists to produce.
    with pin_tenant(tenant_a.id):
        shift.refresh_from_db()
        assert shift.status == ShiftStatus.OPEN
        assert shift.closed_at is None
        assert shift.declared_total is None


def test_a_turno_opened_this_morning_is_not_news(
    tenant_a, sede_a, owner_a, cashier_a, settings
):
    """A shop's longest legitimate shift is a working day. A notice about every
    open drawer is a notice nobody reads."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    open_shift(tenant_a, sede_a, cashier_a, hours_ago=6)
    assert (
        counter_jobs.stale_shift_notice(
            tenant_id=str(tenant_a.id),
            location_id=str(sede_a.id),
            run_date=timezone.localdate().isoformat(),
        )
        == 0
    )
    assert django_mail.outbox == []


def test_a_network_with_nobody_to_tell_is_a_configuration_and_not_a_failure(
    tenant_a, sede_a, owner_a, cashier_a, settings
):
    """The surface still shows the shifts. A job that raised here would retry
    five times over a state no retry can change."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    open_shift(tenant_a, sede_a, cashier_a, hours_ago=30)
    with pin_tenant(tenant_a.id):
        owner_a.status = UserStatus.SUSPENDED
        owner_a.save(update_fields=["status"])

    assert (
        counter_jobs.stale_shift_notice(
            tenant_id=str(tenant_a.id),
            location_id=str(sede_a.id),
            run_date=timezone.localdate().isoformat(),
        )
        == 1
    )
    assert django_mail.outbox == []


def test_the_same_day_is_queued_once(tenant_a, sede_a, owner_a, cashier_a):
    """`(tenant_id, location_id, date)` is the idempotency key — a re-run on the
    same day produces the same notice and sends nothing twice."""
    open_shift(tenant_a, sede_a, cashier_a, hours_ago=30)
    day = timezone.localdate()
    assert counter_jobs.enqueue_stale_shift_notice(tenant_a.id, sede_a.id, day) is True
    assert counter_jobs.enqueue_stale_shift_notice(tenant_a.id, sede_a.id, day) is False


def test_the_job_refuses_to_start_without_its_tenant(tenant_a, sede_a):
    """*What this stage would break* · S0's tenant pin. A job reaching the
    database unpinned is the failure rule 6's third context exists for."""
    with pytest.raises(TypeError):
        counter_jobs.stale_shift_notice(  # type: ignore[call-arg]
            location_id=str(sede_a.id), run_date=timezone.localdate().isoformat()
        )
