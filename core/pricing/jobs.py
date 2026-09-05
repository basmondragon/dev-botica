"""S7's two background jobs, and the third one that deliberately does not exist.

Both take `tenant_id` in the payload and **pin inside their own transaction
before touching anything** (ledger rule 6, context three). Under `FORCE ROW
LEVEL SECURITY` an unpinned connection reads and writes zero rows, so a job that
took its pin from anywhere but its payload would complete, log a success and
write nothing at all.

Both hold the Procrastinate lock `pricing:{tenant_id}` (§9), so a run and a cap
check never interleave: the check moves a proposal's status and the run
supersedes live proposals wholesale, and the two crossing would leave a
compliance finding superseded by a model run that never looked at it.

**This stage runs no job that writes a price, because there is no such write**
(A11). There was an obvious third job available here -- a cron that applied
approved suggestions on their effective date -- and it is precisely the
mechanism most likely to become *prices moving on their own*, so it does not
exist rather than existing behind a flag somebody could flip. *If that is
wrong*, an owner who agrees with forty suggestions spends forty short
interactions instead of one, and the remedy is a better editor. *If the opposite
were built and wrong*, one configuration change moves every price at every till,
and there is no remedy at all.
"""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core.models import ElasticityEstimate, Tenant
from core.pricing import caps, engine, estimator
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)

QUEUE = "pricing"

#: The business clock both crons are read against (§1: one country, one
#: timezone, and no `i18n` runtime).
BUSINESS_TIMEZONE = ZoneInfo("America/Bogota")

#: Monday 03:00 in the tenant's timezone, and 05:00 daily for the cap check.
#: Both sweeps run hourly and enqueue only where the tenant's local clock has
#: reached its own hour -- the deployment's UTC hour is not the pharmacy's.
RUN_HOUR = 3
CHECK_HOUR = 5
MONDAY = 0


def _lock(tenant_id) -> str:
    return f"pricing:{tenant_id}"


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _local_now(now=None):
    return (now or timezone.now()).astimezone(BUSINESS_TIMEZONE)


def _tenants():
    with tenant_picker():
        return list(Tenant.objects.values_list("id", flat=True))


# ---------------------------------------------------------------------------
# 1 · the weekly run
# ---------------------------------------------------------------------------


@app.task(
    name="pricing_run",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=60, exponential_wait=2),
)
def pricing_run(*, tenant_id, window_end, force=False):
    """The estimator, the margin rule and the suggestion engine as **one job**.

    **Idempotent on `(tenant_id, window_end, model_version)`.** A retried cron
    finds the run it already made and does nothing, so the same morning produces
    one run and one `computed_at`. An owner pressing `Calcular ahora` passes
    `force`, because somebody asking for fresh figures after changing a setting
    is asking for exactly that -- and the only consequence of the answer is that
    a screen changes.

    **Failure:** three attempts with backoff. All three failing leaves the
    previous run's estimates and suggestions exactly as they were -- nothing is
    partially replaced, because the whole run is written in one transaction at
    the end -- and the screen's freshness line goes stale and shows its age. It
    does not blank the table: last week's suggestions are still the best
    information that exists, and every current figure beside them is recomputed
    live on the same read.
    """
    end = _as_date(window_end)
    with pinned_job({"tenant_id": tenant_id}):
        if not force and _already_ran(tenant_id, end):
            logger.info("pricing run for %s at %s already exists", tenant_id, end)
            return {"skipped": True, "window_end": end.isoformat()}
        report = engine.run(tenant_id, today=end)
        report["computed_at"] = report["computed_at"].isoformat()
        logger.info("pricing run %s: %s", end, report)
        return report


def _already_ran(tenant_id, run_date: date) -> bool:
    """Whether this window and this model version have already been computed.

    Read off the rows the run wrote, because **there is no run table**: the
    ledger assigns S7 two tables and no more, and a run is identified by
    `(computed_at, model_version)` on the rows it produced.

    The window ends on the current week's Monday, so two runs on the Tuesday and
    the Wednesday of one week share a key -- which is what makes the weekly cron
    idempotent across a retry that lands a day late.
    """
    end = estimator.window_start(run_date) + timedelta(weeks=estimator.WINDOW_WEEKS)
    opened = timezone.make_aware(datetime.combine(end, time.min), BUSINESS_TIMEZONE)
    return ElasticityEstimate.objects.filter(
        tenant_id=tenant_id,
        model_version=estimator.MODEL_VERSION,
        computed_at__gte=opened,
    ).exists()


def enqueue_run(tenant_id, window_end=None, *, force=False):
    """Queue one run under its idempotency key. Returns whether it was queued.

    The queueing lock carries the window and the model version, so a cron retry
    and a button press landing while the first is still pending collapse into
    one job rather than two.
    """
    end = window_end or timezone.localdate()
    key = f"pricing:run:{tenant_id}:{end.isoformat()}:{estimator.MODEL_VERSION}"
    try:
        # **Its own savepoint.** A colliding queueing lock raises
        # `IntegrityError`, and catching one does not un-poison the transaction
        # it was raised in -- the request that enqueued would then fail on its
        # next statement, which is how a duplicate enqueue becomes a 500 on the
        # button that caused it.
        with transaction.atomic():
            pricing_run.configure(queueing_lock=key, lock=_lock(tenant_id)).defer(
                tenant_id=str(tenant_id),
                window_end=end.isoformat(),
                force=force,
            )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        logger.info("a pricing run for %s is already queued", tenant_id)
        return False
    return True


@app.periodic(cron="0 * * * *")
@app.task(name="pricing_sweep_run", queue=QUEUE)
def sweep_run(timestamp):
    """Monday 03:00, at each tenant's own clock."""
    del timestamp
    now = _local_now()
    if now.hour != RUN_HOUR or now.weekday() != MONDAY:
        return 0
    return sum(1 for one in _tenants() if enqueue_run(one, now.date()))


# ---------------------------------------------------------------------------
# 2 · the daily cap check
# ---------------------------------------------------------------------------


@app.task(
    name="pricing_check_caps",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=60, exponential_wait=2),
)
def pricing_check_caps(*, tenant_id, run_date):
    """Compare every in-force price against its cap. Idempotent by construction.

    Re-running produces the same rows: a reference already carrying an
    `above_cap` finding is left alone, and one that has come back under its cap
    has that finding superseded.

    **Failure:** retried; a persistent failure surfaces on the screen as the
    `Propuestas sobre el tope` tile carrying its own staleness reading, because
    a compliance tile that silently stops updating is worse than one that is
    absent.
    """
    day = _as_date(run_date)
    with pinned_job({"tenant_id": tenant_id}):
        return caps.check(tenant_id, today=day)


def enqueue_check_caps(tenant_id, run_date=None):
    day = run_date or timezone.localdate()
    key = f"pricing:caps:{tenant_id}:{day.isoformat()}"
    try:
        with transaction.atomic():
            pricing_check_caps.configure(
                queueing_lock=key, lock=_lock(tenant_id)
            ).defer(tenant_id=str(tenant_id), run_date=day.isoformat())
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="30 * * * *")
@app.task(name="pricing_sweep_check_caps", queue=QUEUE)
def sweep_check_caps(timestamp):
    """05:00 daily, at each tenant's own clock."""
    del timestamp
    now = _local_now()
    if now.hour != CHECK_HOUR:
        return 0
    return sum(1 for one in _tenants() if enqueue_check_caps(one, now.date()))
