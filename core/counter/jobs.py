"""One job, and the count is the point.

**Everything on the counter's critical path is synchronous by construction, and
everything asynchronous is off that path entirely.** Applying a pushed sale --
deduping it, appending its `sale` moves through S3's service and maintaining the
projection -- happens inside the push transaction and not in a job, because
rule 7 requires the projection to be maintained in the same transaction as the
moves.

**The job closes nothing.** Closing a cash session without a count destroys the
count, which is the one number the session exists to produce. A shift left open
is a fact an owner needs told, not a row a job should tidy -- and the office
already has `POST /api/shifts/{id}/force-close` for the case where a person
decides.
"""

import logging
from datetime import date, timedelta

from django.db import IntegrityError
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core import mail
from core.models import (
    Location,
    LocationStatus,
    Role,
    Shift,
    ShiftStatus,
    Tenant,
    User,
    UserStatus,
)
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)

#: How long a turno may stay open before it is worth telling somebody about. A
#: shop's longest legitimate shift is a working day; a day and a night means the
#: cashier went home without counting, which is exactly the fact an owner wants
#: told.
STALE_AFTER_HOURS = 24


@app.task(
    name="counter_stale_shift_notice",
    queue="counter",
    retry=RetryStrategy(max_attempts=5, wait=30, exponential_wait=2),
)
def stale_shift_notice(*, tenant_id, location_id, run_date):
    """Turnos still open more than a day after they were opened, to the tenant's
    `owner` and `admin` through S0's transactional email.

    A missed run costs one day's notice and loses nothing: the shifts are still
    open, still listed on the Turnos surface, and still counted by tomorrow's
    run.
    """
    day = run_date if isinstance(run_date, date) else date.fromisoformat(str(run_date))
    with pinned_job({"tenant_id": tenant_id}):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        location = Location.objects.filter(id=location_id).first()
        if tenant is None or location is None:
            logger.info("tenant or sede is gone; nothing to notify about")
            return 0

        cutoff = timezone.now() - timedelta(hours=STALE_AFTER_HOURS)
        rows = list(
            Shift.objects.filter(
                tenant_id=tenant_id,
                location_id=location_id,
                status=ShiftStatus.OPEN,
                opened_at__lt=cutoff,
            )
            .select_related("device")
            .order_by("opened_at")
        )
        if not rows:
            return 0

        recipients = list(
            User.objects.filter(
                tenant_id=tenant_id,
                role__in=(Role.OWNER, Role.ADMIN),
                status=UserStatus.ACTIVE,
            ).values_list("email", flat=True)
        )
        if not recipients:
            # A network with nobody to tell is a configuration and not a
            # failure: the surface still shows the shifts.
            logger.info("no owner or admin at %s to notify", tenant.slug)
            return len(rows)

        mail.send_stale_shifts(
            recipients=recipients,
            tenant_name=tenant.name,
            location_name=location.name,
            run_date=day,
            shifts=[
                {
                    "device": row.device.label if row.device else "—",
                    "opened_at": row.opened_at,
                    "user_name": row.user_name,
                }
                for row in rows
            ],
        )
        return len(rows)


def enqueue_stale_shift_notice(tenant_id, location_id, run_date=None):
    """`(tenant_id, location_id, date)` is the idempotency key -- a re-run on the
    same day produces the same notice and sends nothing twice."""
    day = run_date or timezone.localdate()
    try:
        stale_shift_notice.configure(
            queueing_lock=f"stale-shifts:{tenant_id}:{location_id}:{day.isoformat()}",
        ).defer(
            tenant_id=str(tenant_id),
            location_id=str(location_id),
            run_date=day.isoformat(),
        )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="15 12 * * *")
@app.task(name="counter_sweep_stale_shifts", queue="counter")
def sweep_stale_shifts(timestamp):
    """Midday, per tenant, per sede.

    Midday rather than dawn: a turno opened yesterday morning and still open now
    is news an administrator can act on today, and a notice that lands at 03:00
    is read at 09:00 anyway.
    """
    del timestamp
    day = timezone.localdate()
    with tenant_picker():
        tenants = list(Tenant.objects.values_list("id", flat=True))
    queued = 0
    for tenant_id in tenants:
        with pinned_job({"tenant_id": tenant_id}):
            locations = list(
                Location.objects.filter(
                    tenant_id=tenant_id, status=LocationStatus.ACTIVE
                ).values_list("id", flat=True)
            )
        for location_id in locations:
            queued += (
                1 if enqueue_stale_shift_notice(tenant_id, location_id, day) else 0
            )
    return queued
