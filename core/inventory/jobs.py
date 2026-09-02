"""Three jobs, all queued on Procrastinate with `tenant_id` in the payload and
pinning before touching anything (ledger rule 6).

**None of the three writes a `stock_moves` row.** The digest raises an expired
lot onto a work list and never writes it off on a schedule -- §12 fixes that
Botica surfaces the state and records the pharmacy's decision, and a job that
wrote off stock would destroy the record of a decision nobody made. The verify
job compares and reports. The rebuild replaces a projection from the ledger and
never the other way round.
"""

import logging
from datetime import date, timedelta

from django.db import IntegrityError
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core import audit, mail
from core.inventory import ledger, settings as inventory_settings
from core.models import AuditAction, Location, LocationStatus, StockOnHand, Tenant
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)


def _locations(tenant_id):
    return list(
        Location.objects.filter(
            tenant_id=tenant_id, status=LocationStatus.ACTIVE
        ).order_by("name")
    )


# ---------------------------------------------------------------------------
# The daily expiry digest
# ---------------------------------------------------------------------------


@app.task(
    name="inventory_expiry_digest",
    queue="inventory",
    retry=RetryStrategy(max_attempts=5, wait=30, exponential_wait=2),
)
def expiry_digest(*, tenant_id, location_id, run_date):
    """What is expiring at one sede, to whoever asked to be told.

    **Writes no `stock_moves` row, ever.** If email is unreachable the digest is
    retried; the state renders on Existencias regardless, which is why an empty
    recipient list is a configuration and not a failure.
    """
    day = run_date if isinstance(run_date, date) else date.fromisoformat(str(run_date))
    with pinned_job({"tenant_id": tenant_id}):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None:
            logger.info("tenant %s is gone; nothing to summarise", tenant_id)
            return 0
        options = inventory_settings.read(tenant)
        recipients = list(options["expiry_digest_recipients"])
        location = Location.objects.filter(id=location_id).first()
        if location is None:
            return 0

        horizon = day + timedelta(days=int(options["expiry_valuation_days"]))
        rows = (
            StockOnHand.objects.filter(
                location_id=location_id,
                quantity__gt=0,
                lot__expires_at__isnull=False,
                lot__expires_at__lte=horizon,
            )
            .select_related("item", "lot")
            .order_by("lot__expires_at", "item__name")
        )
        # The queryset already excludes a null lot and a null expiry; the local
        # names are what makes that visible to a reader and to a type checker,
        # which for a job that emails a pharmacist about dated merchandise is
        # worth two lines.
        lines = []
        for row in rows:
            lot, expires_at = row.lot, row.lot.expires_at if row.lot else None
            if lot is None or expires_at is None:
                continue
            lines.append(
                {
                    "item": row.item.name,
                    "lot": lot.lot_code,
                    "expires_at": expires_at,
                    "quantity": row.quantity,
                    "expired": expires_at < day,
                }
            )
        if not recipients or not lines:
            # Not a failure and not a retry: the work list is the screen, and
            # the email is a convenience over it.
            logger.info(
                "expiry digest for %s: %s lots, %s recipients",
                location.code,
                len(lines),
                len(recipients),
            )
            return len(lines)

        for address in recipients:
            mail.send_expiry_digest(
                email=address,
                tenant_name=tenant.name,
                location_name=location.name,
                horizon_days=int(options["expiry_valuation_days"]),
                lines=lines,
                run_date=day,
            )
        return len(lines)


def enqueue_digest(tenant_id, location_id, run_date=None):
    day = run_date or timezone.localdate()
    try:
        expiry_digest.configure(
            queueing_lock=f"expiry_digest:{tenant_id}:{location_id}:{day.isoformat()}",
        ).defer(
            tenant_id=str(tenant_id),
            location_id=str(location_id),
            run_date=day.isoformat(),
        )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="10 11 * * *")
@app.task(name="inventory_sweep_expiry_digests", queue="inventory")
def sweep_expiry_digests(timestamp):
    """Fan the digest out, one job per tenant per sede, at the tenant's local
    morning -- 11:10 UTC is 06:10 in Bogotá, which is before a droguería opens
    and after the night's writes have settled."""
    del timestamp
    day = timezone.localdate()
    with tenant_picker():
        tenants = list(Tenant.objects.values_list("id", flat=True))
    queued = 0
    for tenant_id in tenants:
        with pinned_job({"tenant_id": tenant_id}):
            locations = [one.id for one in _locations(tenant_id)]
        for location_id in locations:
            queued += 1 if enqueue_digest(tenant_id, location_id, day) else 0
    return queued


# ---------------------------------------------------------------------------
# The nightly projection verify
# ---------------------------------------------------------------------------


@app.task(
    name="inventory_projection_verify",
    queue="inventory",
    retry=RetryStrategy(max_attempts=5, wait=30, exponential_wait=2),
)
def projection_verify(*, tenant_id, location_id, run_date):
    """Recompute `stock_on_hand` from `stock_moves` and compare **without
    writing**.

    Zero drift writes one `audit_log` row saying so; non-zero drift writes the
    differing keys into that row's `before`/`after`. **Drift is a defect signal,
    not a correction event** -- a job that silently repaired the projection would
    also silently hide the code path that broke it (rule 7).
    """
    day = run_date if isinstance(run_date, date) else date.fromisoformat(str(run_date))
    with pinned_job({"tenant_id": tenant_id}):
        location = Location.objects.filter(id=location_id).first()
        if location is None:
            return 0
        drift = ledger.verify(tenant_id, location_id)
        audit.record(
            actor=None,
            tenant_id=tenant_id,
            action=AuditAction.UPDATE,
            entity_type="stock_on_hand",
            entity_id=location_id,
            before={"checked": location.code, "date": day.isoformat()},
            after={
                "drift": len(drift),
                # Bounded: a projection that drifted on four thousand keys is a
                # defect somebody reads the first ten of and then goes to the
                # code, and an audit row carrying all four thousand is a row
                # nobody opens.
                "keys": [f"{item}:{lot}" for item, lot in list(drift)[:10]],
            },
            request_id=f"verify:{location.code}:{day.isoformat()}",
        )
        if drift:
            logger.error(
                "projection drift at %s: %s key(s) disagree with the ledger",
                location.code,
                len(drift),
            )
        return len(drift)


def enqueue_verify(tenant_id, location_id, run_date=None):
    day = run_date or timezone.localdate()
    try:
        projection_verify.configure(
            queueing_lock=f"verify:{tenant_id}:{location_id}:{day.isoformat()}",
        ).defer(
            tenant_id=str(tenant_id),
            location_id=str(location_id),
            run_date=day.isoformat(),
        )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="50 3 * * *")
@app.task(name="inventory_sweep_projection_verify", queue="inventory")
def sweep_projection_verify(timestamp):
    del timestamp
    day = timezone.localdate()
    with tenant_picker():
        tenants = list(Tenant.objects.values_list("id", flat=True))
    queued = 0
    for tenant_id in tenants:
        with pinned_job({"tenant_id": tenant_id}):
            locations = [one.id for one in _locations(tenant_id)]
        for location_id in locations:
            queued += 1 if enqueue_verify(tenant_id, location_id, day) else 0
    return queued


# ---------------------------------------------------------------------------
# The on-demand projection rebuild
# ---------------------------------------------------------------------------


@app.task(
    name="inventory_projection_rebuild",
    queue="inventory",
    retry=RetryStrategy(max_attempts=3, wait=30, exponential_wait=2),
)
def projection_rebuild(*, tenant_id, location_id, requested_at):
    """Recompute and replace the projection for one sede.

    Inside one transaction holding the advisory lock the ledger service also
    takes for that location, so appends during the rebuild are serialised rather
    than lost. Naturally idempotent: running it twice produces the same rows.
    """
    with pinned_job({"tenant_id": tenant_id}):
        location = Location.objects.filter(id=location_id).first()
        if location is None:
            return {}
        report = ledger.rebuild(tenant_id, location_id)
        audit.record(
            actor=None,
            tenant_id=tenant_id,
            action=AuditAction.UPDATE,
            entity_type="stock_on_hand",
            entity_id=location_id,
            before={"rebuilt": location.code, "requested_at": str(requested_at)},
            after=report,
            request_id=f"rebuild:{location.code}:{requested_at}",
        )
        logger.info("rebuilt %s: %s", location.code, report)
        return report


def enqueue_rebuild(tenant_id, location_id, requested_at=None):
    stamp = (requested_at or timezone.now()).isoformat()
    try:
        projection_rebuild.configure(
            queueing_lock=f"rebuild:{tenant_id}:{location_id}:{stamp}",
        ).defer(
            tenant_id=str(tenant_id),
            location_id=str(location_id),
            requested_at=stamp,
        )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True
