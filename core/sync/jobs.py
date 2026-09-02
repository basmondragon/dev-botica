"""One job: the daily stale-device check.

Everything else this stage does is either request-scoped or runs on the client.
**Compaction is a client job**, and it is said here so nobody looks for it in
the worker: it runs in the leader tab on start and every six hours.

**There is no server-side push retry and no server-side queue of device work.**
The outbox lives on the device, which is the only place that survives the
network being gone. A server that queued work *for* a till would be a second
source of truth about what that till has done, and §5 rule 1 exists to prevent
exactly that.
"""

import logging
from datetime import date, timedelta

from django.db import IntegrityError
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core.models import Device, DeviceStatus, SyncConflictType, Tenant
from core.sync import conflicts, settings as sync_settings
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)


@app.task(
    name="sync_stale_device_check",
    queue="sync",
    retry=RetryStrategy(max_attempts=5, wait=30, exponential_wait=2),
)
def stale_device_check(*, tenant_id, run_date):
    """Raise or refresh a `device_silent` conflict for every quiet till.

    The payload carries `tenant_id` and the job pins before touching anything
    (rule 6, context three). The conflict's id is derived from
    `(tenant_id, device_id, date)`, so a re-run on the same day updates the
    existing row rather than adding a second.

    **It never touches a device's data and never revokes anything.** A till that
    is quiet because the shop was closed is not a till to disable, and a job
    that disabled one would be a Monday morning with no counter.
    """
    day = run_date if isinstance(run_date, date) else date.fromisoformat(str(run_date))
    with pinned_job({"tenant_id": tenant_id}):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            logger.info("tenant %s is gone; nothing to check", tenant_id)
            return 0
        hours = int(sync_settings.read(tenant)["stale_device_hours"])
        cutoff = timezone.now() - timedelta(hours=hours)

        quiet = Device.objects.filter(status=DeviceStatus.ACTIVE).exclude(
            last_synced_at__gt=cutoff
        )
        raised = 0
        for device in quiet.select_related("location"):
            conflicts.raise_conflict(
                device=device,
                type=SyncConflictType.DEVICE_SILENT,
                detail={
                    "reason": "device_silent",
                    "hours": hours,
                    "last_synced_at": (
                        device.last_synced_at.isoformat()
                        if device.last_synced_at
                        else None
                    ),
                },
                row_id=conflicts.daily_id(tenant_id, device.id, day),
            )
            raised += 1
        if raised:
            logger.info("raised %s device_silent conflicts for %s", raised, tenant_id)
        return raised


def enqueue_stale_check(tenant_id, run_date=None):
    """Queue one tenant's check under its idempotency key."""
    day = run_date or timezone.localdate()
    try:
        stale_device_check.configure(
            queueing_lock=f"stale_devices:{tenant_id}:{day.isoformat()}",
        ).defer(tenant_id=str(tenant_id), run_date=day.isoformat())
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        logger.info("the stale-device check for %s on %s is queued", tenant_id, day)
        return False
    return True


@app.periodic(cron="40 3 * * *")
@app.task(name="sweep_stale_devices", queue="sync")
def sweep_stale_devices(timestamp):
    """Fan the check out, one job per tenant.

    The dispatcher reads `tenants` through S0's own picker grant rather than
    opening a second widening. It reads ids and nothing else; every write
    happens inside a per-tenant pin.
    """
    del timestamp
    day = timezone.localdate()
    with tenant_picker():
        tenants = list(Tenant.objects.values_list("id", flat=True))
    for tenant_id in tenants:
        enqueue_stale_check(tenant_id, day)
    return len(tenants)
