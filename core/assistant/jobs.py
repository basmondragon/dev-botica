"""S8's three background jobs, and the one call that deliberately is not among
them.

**The model call is not a job.** It runs inline on `POST
/api/assistant/queries` under `model_timeout_ms`, because a queued call would
need the till to poll for prose it has already rendered a local version of.

All three take `tenant_id` in the payload and **pin inside their own transaction
before touching anything** (ledger rule 6, context three). Under `FORCE ROW
LEVEL SECURITY` an unpinned connection reads and writes zero rows, so a job that
took its pin from anywhere but its payload would complete, log a success and
write nothing at all.

They hold the Procrastinate lock `assistant:{tenant_id}` (§9), so a refresh and
a purge never interleave.
"""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core.assistant import mining, settings as assistant_settings, vocabulary
from core.models import (
    AssistantMode,
    AssistantQuery,
    ItemWarning,
    Tenant,
)
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)

QUEUE = "assistant"

#: The business clock every cron is read against (§1: one country, one timezone,
#: and no `i18n` runtime).
BUSINESS_TIMEZONE = ZoneInfo("America/Bogota")

REFRESH_HOUR = 3
PURGE_HOUR = 2
HEALTH_HOUR = 6
SUNDAY = 6

#: The trailing window the rejection rate is measured over. Two hundred queries
#: is a few days at one sede and a morning across a network, which is short
#: enough to notice a bad prompt and long enough not to alarm on three.
REJECTION_WINDOW = 200

#: How long a warning may sit without its keys ever appearing in an extraction
#: before Ajustes says so.
DORMANT_DAYS = 30


def _lock(tenant_id) -> str:
    return f"assistant:{tenant_id}"


def _local_now(now=None):
    return (now or timezone.now()).astimezone(BUSINESS_TIMEZONE)


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _tenants():
    with tenant_picker():
        return list(Tenant.objects.values_list("id", flat=True))


# ---------------------------------------------------------------------------
# 1 · the weekly mining run
# ---------------------------------------------------------------------------


@app.task(
    name="assistant_cross_sell_refresh",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=120, exponential_wait=2),
)
def assistant_cross_sell_refresh(*, tenant_id, location_id=None, window_end=None):
    """Mine one scope: one sede, or the network where `location_id` is null.

    **Failure:** the previous rows stand and `computed_at` does not move. Tills
    keep the rules they have and the sync panel keeps showing the older date --
    it never shows a fresh timestamp over stale rules. A partial run is not
    published, because each scope is written in one transaction.
    """
    end = _as_date(window_end) if window_end else timezone.localdate()
    at = datetime.combine(end + timedelta(days=1), time.min, BUSINESS_TIMEZONE)
    with pinned_job({"tenant_id": tenant_id}):
        report = mining.refresh_scope(
            tenant_id=tenant_id, location_id=location_id, at=at
        )
    return report.as_dict()


def enqueue_refresh(tenant_id, *, window_end=None, location_id=None) -> bool:
    """Fan one tenant out to one job per sede plus one network-wide pass.

    A `location_id` narrows it to that one scope, which is what
    `POST /api/cross-sell-rules/refresh` passes when a regente asks for one
    sede's rules rather than the network's.

    The queueing lock carries the scope, the window and the algorithm version,
    so a cron retry and a `Recalcular` press landing while the first is still
    pending collapse into one job rather than two.
    """
    end = window_end or timezone.localdate()
    queued = 0
    if location_id is not None:
        scopes = [location_id]
    else:
        with pinned_job({"tenant_id": tenant_id}):
            scopes = [*mining.scopes_of(tenant_id), None]
    for scope in scopes:
        key = (
            f"assistant:cross-sell:{tenant_id}:{scope or 'network'}:"
            f"{end.isoformat()}:{mining.ALGORITHM_VERSION}"
        )
        try:
            # **Its own savepoint.** A colliding queueing lock raises
            # `IntegrityError`, and catching one does not un-poison the
            # transaction it was raised in -- the request that enqueued would
            # then fail on its next statement, which is how a duplicate enqueue
            # becomes a 500 on the button that caused it.
            with transaction.atomic():
                assistant_cross_sell_refresh.configure(
                    queueing_lock=key, lock=_lock(tenant_id)
                ).defer(
                    tenant_id=str(tenant_id),
                    location_id=str(scope) if scope else None,
                    window_end=end.isoformat(),
                )
            queued += 1
        except (queue_exceptions.AlreadyEnqueued, IntegrityError):
            logger.info("a cross-sell refresh for %s is already queued", tenant_id)
    return queued > 0


@app.periodic(cron="0 * * * *")
@app.task(name="assistant_sweep_cross_sell", queue=QUEUE)
def sweep_cross_sell(timestamp):
    """Sunday 03:00, at each tenant's own clock.

    It runs **from the day the tenant exists**, whether or not there is anything
    to mine: a job that only starts once there is history is a job nobody
    remembers to start (§1, *Cold start*).
    """
    del timestamp
    now = _local_now()
    if now.hour != REFRESH_HOUR or now.weekday() != SUNDAY:
        return 0
    return sum(1 for one in _tenants() if enqueue_refresh(one, window_end=now.date()))


# ---------------------------------------------------------------------------
# 2 · the daily transcript purge
# ---------------------------------------------------------------------------


@app.task(
    name="assistant_transcript_purge",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=60, exponential_wait=2),
)
def assistant_transcript_purge(*, tenant_id, purge_date):
    """Take the words off every row retention no longer covers.

    **This job exists and runs whichever way §11.3 is answered**, because the
    transcript exists the moment a cashier types it. Where `retain_transcripts`
    is false it purges everything, immediately, on the first run after the row
    landed -- a till at that setting never pushes a transcript at all, and this
    is what catches the rows written before somebody turned it off.

    **Failure:** the rows stay and the job retries with backoff. Two consecutive
    failures raise in Ajustes, because a retention job that has quietly stopped
    is a Ley 1581 exposure and not an inconvenience.
    """
    day = _as_date(purge_date)
    with pinned_job({"tenant_id": tenant_id}):
        tenant = Tenant.objects.get(id=tenant_id)
        values = assistant_settings.read(tenant)
        closed = datetime.combine(day + timedelta(days=1), time.min, BUSINESS_TIMEZONE)
        if values.get("retain_transcripts"):
            cutoff = closed - timedelta(
                days=int(values.get("transcript_retention_days", 30))
            )
        else:
            cutoff = closed
        purged = (
            AssistantQuery.objects.filter(tenant_id=tenant_id, recorded_at__lt=cutoff)
            .exclude(
                transcript="",
                recommendation="",
                recommendation_secondary="",
                symptoms=[],
            )
            .update(
                # **What survives is the row's shape** -- location, sale, mode,
                # model, cost, latency, its timestamps and its suggestions with
                # `accepted` -- which is exactly what every metric in the product
                # needs and none of what Ley 1581 is about.
                transcript="",
                recommendation="",
                recommendation_secondary="",
                symptoms=[],
                updated_at=timezone.now(),
            )
        )
    return {"purged": purged, "purge_date": day.isoformat()}


def enqueue_purge(tenant_id, purge_date=None) -> bool:
    day = purge_date or timezone.localdate()
    key = f"assistant:purge:{tenant_id}:{day.isoformat()}"
    try:
        with transaction.atomic():
            assistant_transcript_purge.configure(
                queueing_lock=key, lock=_lock(tenant_id)
            ).defer(tenant_id=str(tenant_id), purge_date=day.isoformat())
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="20 * * * *")
@app.task(name="assistant_sweep_purge", queue=QUEUE)
def sweep_purge(timestamp):
    """02:00 daily, at each tenant's own clock."""
    del timestamp
    now = _local_now()
    if now.hour != PURGE_HOUR:
        return 0
    return sum(1 for one in _tenants() if enqueue_purge(one, now.date()))


# ---------------------------------------------------------------------------
# 3 · the daily health check
# ---------------------------------------------------------------------------


@app.task(
    name="assistant_health_check",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=60, exponential_wait=2),
)
def assistant_health_check(*, tenant_id, run_date):
    """The rejection rate, the chipless share, and the warnings nothing fires.

    **It writes nothing outside its own report** and surfaces it in Ajustes. On
    failure the previous report stands with its own date, because a screen that
    blanked its own diagnosis when the diagnosis failed would be the one thing
    worse than a stale one.
    """
    del run_date
    with pinned_job({"tenant_id": tenant_id}):
        report = health_report(Tenant.objects.get(id=tenant_id))
    if report["rejection_alert"]:
        logger.warning(
            "assistant output check rejecting %.1f%% of answers for %s",
            (report["rejection_rate"] or 0) * 100,
            tenant_id,
        )
    return report


def enqueue_health(tenant_id, run_date=None) -> bool:
    day = run_date or timezone.localdate()
    key = f"assistant:health:{tenant_id}:{day.isoformat()}"
    try:
        with transaction.atomic():
            assistant_health_check.configure(
                queueing_lock=key, lock=_lock(tenant_id)
            ).defer(tenant_id=str(tenant_id), run_date=day.isoformat())
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="40 * * * *")
@app.task(name="assistant_sweep_health", queue=QUEUE)
def sweep_health(timestamp):
    """06:00 daily, at each tenant's own clock."""
    del timestamp
    now = _local_now()
    if now.hour != HEALTH_HOUR:
        return 0
    return sum(1 for one in _tenants() if enqueue_health(one, now.date()))


def health_report(tenant) -> dict:
    """The three counts, computed live so the job and the screen agree."""
    tenant_id = getattr(tenant, "id", tenant)
    values = assistant_settings.read(tenant)
    recent = list(
        AssistantQuery.objects.filter(tenant_id=tenant_id)
        .order_by("-recorded_at")
        .values("mode", "output_check_passed", "symptoms")[:REJECTION_WINDOW]
    )
    answered = [
        row
        for row in recent
        if row["mode"] == AssistantMode.MODEL or not row["output_check_passed"]
    ]
    rejected = [row for row in answered if not row["output_check_passed"]]
    rate = (len(rejected) / len(answered)) if answered else None
    threshold = float(values.get("output_check_alert_rate", 0.02))
    chipless = sum(1 for row in recent if not row["symptoms"])
    mapping = values.get("symptom_category_map") or {}
    unmapped = sorted(
        set(vocabulary.SYMPTOM_KEYS) - {key for key, ids in mapping.items() if ids}
    )
    dormant = dormant_warnings(tenant_id)
    return {
        "rejection_rate": rate,
        "rejection_alert": rate is not None and rate > threshold,
        "alert_threshold": threshold,
        "queries_without_chips": chipless,
        "queries_considered": len(recent),
        "unmapped_symptom_keys": unmapped,
        "dormant_warnings": dormant,
    }


def dormant_warnings(tenant_id) -> list[dict]:
    """Active warnings whose trigger keys have not appeared in an extraction in
    `DORMANT_DAYS`.

    **This is the check that keeps the closed vocabulary honest.** A warning
    naming a key the extractor cannot emit never fires and nothing anywhere
    raises, so the screen says *"esta advertencia nunca se ha activado"* and
    somebody looks at it.
    """
    since = timezone.now() - timedelta(days=DORMANT_DAYS)
    seen: set[str] = set()
    for row in AssistantQuery.objects.filter(
        tenant_id=tenant_id, recorded_at__gte=since
    ).values_list("symptoms", flat=True):
        for fact in row or []:
            key = str(fact.get("key") or "")
            if key:
                seen.add(key)
    dormant = []
    for warning in ItemWarning.objects.filter(
        tenant_id=tenant_id, active=True
    ).select_related("item"):
        keys = vocabulary.trigger_keys(warning.triggers)
        if keys and keys & seen:
            continue
        dormant.append(
            {
                "id": str(warning.id),
                "item_name": warning.item.name,
                "text": warning.text,
                "keys": sorted(keys),
            }
        )
    return dormant


def dormant_warning_ids(tenant_id) -> set[str]:
    """The same answer as a set, for the grid's own `nunca se ha activado` dot."""
    return {row["id"] for row in dormant_warnings(tenant_id)}
