"""One job: the nightly sweep that expires a lapsed registro INVIMA.

There is no price-activation job and no barcode-reindex job. A future-dated
price becomes current by the resolution rule at read time, and the search
indexes are maintained by the database.
"""

import logging
from datetime import date

from django.db import IntegrityError
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core import audit
from core.models import AuditAction, InvimaStatus, Item, Tenant
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)


@app.task(
    name="expire_invima_registrations",
    queue="catalog",
    retry=RetryStrategy(max_attempts=5, wait=30, exponential_wait=2),
)
def expire_invima_registrations(*, tenant_id, run_date):
    """Move an item from `valid` to `expired` when its registration date passes.

    **It touches nothing else.** `in_process` stays `in_process`, because INVIMA
    has the file and the product is normally still sellable while it is open --
    that is the pharmacy's call, not Botica's. `not_applicable` stays. A status
    an administrator set by hand to `expired` is never flipped back.

    The payload carries `tenant_id` and the job pins before touching anything
    (ledger rule 6, context three). The work is a set-based update on a date
    comparison rather than a delta, so a re-run changes nothing and **a missed
    day is repaired by the next run** rather than lost. A run that never happens
    leaves an item reading `Registro vigente` past its date -- which the grid's
    `Vence` filter still catches, because that filter reads `invima_expires_at`
    and not the status. The status is a convenience for the badge and the enum
    filter; the date is the truth.
    """
    day = run_date if isinstance(run_date, date) else date.fromisoformat(str(run_date))
    with pinned_job({"tenant_id": tenant_id}):
        lapsed = Item.objects.filter(
            invima_status=InvimaStatus.VALID,
            invima_expires_at__isnull=False,
            invima_expires_at__lt=day,
        )
        count = lapsed.update(invima_status=InvimaStatus.EXPIRED)
        if not count:
            return 0
        # One row per run naming the count, with **no actor**, rather than one
        # row per item: a thousand rows for a date passing is noise that buries
        # the edits a person actually made. This is why
        # `audit_log.actor_user_id` has to admit null, which S0 owns -- and if
        # it ever stops admitting null, this fails at the last statement of its
        # transaction and the whole run rolls back.
        audit.record(
            actor=None,
            tenant_id=tenant_id,
            action=AuditAction.UPDATE,
            entity_type="items",
            before={"invima_status": InvimaStatus.VALID},
            after={
                "invima_status": InvimaStatus.EXPIRED,
                "items": count,
                "run_date": day.isoformat(),
            },
            request_id=f"invima_sweep_{day:%Y%m%d}",
        )
        logger.info("expired %s registrations for tenant %s", count, tenant_id)
        return count


def enqueue_sweep(tenant_id, run_date=None):
    """Queue one tenant's sweep under its idempotency key.

    `(tenant_id, run_date)` -- **a duplicate enqueue is a no-op**, which is what
    the queue's own unique index on the lock says and what this catch turns from
    an error into the intended silence.
    """
    day = run_date or timezone.localdate()
    try:
        expire_invima_registrations.configure(
            queueing_lock=f"invima:{tenant_id}:{day.isoformat()}",
        ).defer(tenant_id=str(tenant_id), run_date=day.isoformat())
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        logger.info("the INVIMA sweep for %s on %s is already queued", tenant_id, day)
        return False
    return True


@app.periodic(cron="20 3 * * *")
@app.task(name="sweep_invima_registrations", queue="catalog")
def sweep_invima_registrations(timestamp):
    """Fan the sweep out, one job per tenant.

    The dispatcher is the one thing in this stage that has to see more than one
    network, and it reads `tenants` through **S0's own picker grant** rather
    than opening a second widening (`core.tenancy.tenant_picker`). It reads ids
    and nothing else; every write happens inside a per-tenant pin.
    """
    del timestamp  # the cron tick; the day the work is about is the local one
    day = timezone.localdate()
    with tenant_picker():
        tenants = list(Tenant.objects.values_list("id", flat=True))
    for tenant_id in tenants:
        enqueue_sweep(tenant_id, day)
    return len(tenants)
