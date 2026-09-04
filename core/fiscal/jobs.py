"""Four jobs and one dispatcher, all on Procrastinate, all pinning the tenant from the job payload
before touching anything (ledger rule 6), and **all of them exit immediately
when the tenant has no target configured** (§8).

**Document creation is not a job.** The row is written in the same pinned
transaction that applies the sale on push, through the sale handoff service;
only delivery is asynchronous. A sale that lands with no document row while a
target is configured is the one failure this stage cannot detect from the
outside -- every count here is a count of documents -- so creation is
transactional and the orphan check exists as the backstop. If that is wrong, the
symptom is a marginally slower push; if the alternative is wrong, the symptom is
a day of sales the client's invoicing system never heard of, discovered at month
end.

The dispatcher is the shape S3 and S4 already use: Procrastinate's periodic
tasks carry no payload, so a per-tenant cron is a cron that fans out. The
five-minute sweep is both -- it is periodic *and* it is the work -- and
`sweep_failed_delivery_digests` is the one extra, because a digest that rode the
five-minute sweep would send itself again as soon as the previous one finished.

**The ladder is ours, not Procrastinate's.** A job that raised on a transport
failure would retry on the queue's own schedule beside `next_attempt_at`, and
two ladders on one document is two ladders to reason about. So a failed delivery
returns normally, records what happened, and schedules itself.
"""

import logging
from datetime import date, datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core import mail
from core.fiscal import (
    delivery,
    export,
    service,
    settings as invoicing,
)
from core.models import (
    FiscalDocument,
    FiscalDocumentStatus,
    Sale,
    SaleSource,
    SaleStatus,
    Tenant,
)
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)

QUEUE = "fiscal"


# ---------------------------------------------------------------------------
# 1 · One attempt at one document
# ---------------------------------------------------------------------------


@app.task(
    name="deliver_fiscal_document",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=30, exponential_wait=2),
)
def deliver_fiscal_document(*, tenant_id, document_id):
    """One attempt, under a queueing lock so exactly one is in flight.

    The retry strategy above is the queue's own, for an infrastructure failure
    -- the database being briefly unreachable -- and not for the target's:
    everything the target can do to us is an outcome this returns normally on.
    """
    with pinned_job({"tenant_id": tenant_id}):
        return delivery.attempt(document_id, tenant_id=tenant_id)


def enqueue_delivery(row) -> bool:
    """`fiscal_document:{id}` is the lock -- **a forced retry pressed twice
    enqueues one attempt.**

    Deferred inside the caller's transaction rather than after it: the job row
    commits with the document row and is invisible to a worker until it does, so
    there is no window in which a document exists with nothing coming for it.
    """
    try:
        # **Its own savepoint.** A duplicate queueing lock is a `UniqueViolation`
        # in the queue's own table, and catching it without a savepoint leaves
        # the caller's transaction marked for rollback -- so the sale that was
        # committing, or the endpoint that pressed `Reintentar`, would fail on
        # its next statement rather than on its own merits.
        with transaction.atomic():
            deliver_fiscal_document.configure(
                queueing_lock=f"fiscal_document:{row.id}",
            ).defer(tenant_id=str(row.tenant_id), document_id=str(row.id))
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        logger.info("document %s is already queued for delivery", row.document_key)
        return False
    return True


# ---------------------------------------------------------------------------
# 2 · The sweep
# ---------------------------------------------------------------------------


@app.periodic(cron="*/5 * * * *")
@app.task(name="sweep_fiscal_documents", queue=QUEUE)
def sweep_fiscal_documents(timestamp):
    """Every five minutes: what is due, what has dwelled, and what is orphaned.

    **It exits on the predicate before it runs a query** for a tenant with no
    target: no delivery job exists to fail, no ladder ticks, no digest is sent
    (§8).
    """
    del timestamp
    queued = 0
    for tenant_id in _tenants():
        with pinned_job({"tenant_id": tenant_id}):
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if tenant is None or not service.handoff_enabled(tenant):
                continue
            queued += _sweep_tenant(tenant)
    return queued


def _sweep_tenant(tenant) -> int:
    """Due documents and dwelled ones, enqueued per document.

    Their own locks dedupe, so an overlapping sweep is a no-op.
    """
    due = FiscalDocument.objects.filter(
        tenant_id=tenant.id,
        status__in=(
            FiscalDocumentStatus.PENDING,
            FiscalDocumentStatus.SENT,
            FiscalDocumentStatus.FAILED,
        ),
        next_attempt_at__isnull=False,
        next_attempt_at__lte=timezone.now(),
    ).only("id", "tenant_id", "document_key")
    queued = 0
    for row in due:
        queued += 1 if enqueue_delivery(row) else 0
    # The file target's cadence rides the same sweep rather than a second cron:
    # a period stops being due the moment its documents are `acknowledged`, so
    # re-enqueueing it every five minutes is a no-op with nothing left to do.
    for period in export.due_periods(tenant):
        queued += 1 if enqueue_export(tenant.id, period) else 0
    return queued


def orphans(tenant, *, limit=200) -> list[dict]:
    """**Reported on the work list as a defect and never silently repaired.**

    A closed counter sale recorded after `configured_at` that holds no document
    row, and a return of such a sale that holds no credit note. Bounded by the
    same timestamp that makes "no backfill" a fact: nothing closed before a
    target was connected was ever due, so nothing before it is an orphan.

    **The predicate is the partial index's, verbatim** -- `source = 'counter'
    AND status = 'closed'`, the index S5 migrated onto `sales` under rule 4. A
    filter that widened it by one value would read the same rows and use no
    index at all, on the largest table in the product.

    Repairing it here would hide the defect that produced it -- and the one
    thing worse than a missing document is a mechanism that quietly manufactures
    documents nobody can account for.
    """
    from core.models import SaleReturn

    since = service.configured_at(tenant)
    if since is None:
        return []
    rows: list[dict] = []
    sales = (
        Sale.objects.select_related("location")
        .filter(
            tenant_id=tenant.id,
            source=SaleSource.COUNTER,
            status=SaleStatus.CLOSED,
            recorded_at__gte=since,
        )
        .exclude(
            id__in=FiscalDocument.objects.filter(
                tenant_id=tenant.id, sale__isnull=False
            ).values("sale_id")
        )
        .order_by("recorded_at")[:limit]
    )
    for sale in sales:
        rows.append(
            {
                "kind": "sale",
                "id": str(sale.id),
                "number": sale.number,
                "sale_id": str(sale.id),
                "sale_number": sale.number,
                "location_id": str(sale.location_id),
                "location_name": sale.location.name,
                "recorded_at": sale.recorded_at,
                "reason": (
                    "Esta venta se cerró después de conectar el sistema de "
                    "facturación y no tiene ningún envío."
                ),
            }
        )
    returns = (
        SaleReturn.objects.select_related("location", "sale")
        .filter(
            tenant_id=tenant.id, recorded_at__gte=since, sale__source=SaleSource.COUNTER
        )
        .exclude(
            id__in=FiscalDocument.objects.filter(
                tenant_id=tenant.id, sale_return__isnull=False
            ).values("sale_return_id")
        )
        .order_by("recorded_at")[:limit]
    )
    for row in returns:
        rows.append(
            {
                "kind": "credit_note",
                "id": str(row.id),
                "number": row.number,
                "sale_id": str(row.sale_id),
                "sale_number": row.sale.number,
                "location_id": str(row.location_id),
                "location_name": row.location.name,
                "recorded_at": row.recorded_at,
                "reason": (
                    f"La devolución {row.number} de la venta {row.sale.number} no "
                    "tiene nota crédito."
                ),
            }
        )
    return sorted(rows, key=_recorded_at, reverse=True)[:limit]


def _recorded_at(row: dict) -> datetime:
    """The sort key, named rather than inlined: the rows are heterogeneous by
    design -- a sale and a return in one list -- and a lambda over a `dict` of
    mixed values is not a thing a type checker can read."""
    return row["recorded_at"]


# ---------------------------------------------------------------------------
# 3 · The file export
# ---------------------------------------------------------------------------


@app.task(
    name="export_fiscal_documents",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=5, wait=60, exponential_wait=2),
)
def export_fiscal_documents(*, tenant_id, period):
    """One period into one file. `(tenant_id, period)` is the idempotency key.

    A storage failure leaves every document where it was and retries on the next
    run; a partial file is never published.
    """
    day = period if isinstance(period, str) else date.fromisoformat(str(period))
    with pinned_job({"tenant_id": tenant_id}):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None or not service.handoff_enabled(tenant):
            return 0
        return export.run(tenant, str(day))["written"]


def enqueue_export(tenant_id, period) -> bool:
    """`(tenant_id, period)` is the lock. A re-run of a period includes exactly
    the same documents and produces the same bytes, so an overlapping sweep is a
    no-op rather than a second file."""
    try:
        with transaction.atomic():
            export_fiscal_documents.configure(
                queueing_lock=f"fiscal_export:{tenant_id}:{period}",
            ).defer(tenant_id=str(tenant_id), period=str(period))
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


# ---------------------------------------------------------------------------
# 4 · The daily digest
# ---------------------------------------------------------------------------


@app.task(
    name="notify_failed_deliveries",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=5, wait=30, exponential_wait=2),
)
def notify_failed_deliveries(*, tenant_id, run_date):
    """One digest, naming the count and linking to the work list.

    **The work list is the record and the email is a pointer to it**: a failure
    that exists only in an inbox is a failure nobody resolved. An empty
    recipient list is a configuration and not a failure -- the list still
    renders.
    """
    day = run_date if isinstance(run_date, date) else date.fromisoformat(str(run_date))
    with pinned_job({"tenant_id": tenant_id}):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if tenant is None or not service.handoff_enabled(tenant):
            return 0
        rows = list(
            FiscalDocument.objects.select_related("location")
            .filter(tenant_id=tenant.id, status=FiscalDocumentStatus.FAILED)
            .order_by("created_at")[:50]
        )
        if not rows:
            return 0
        recipients = _recipients(tenant)
        if not recipients:
            logger.info("no invoicing digest recipients at %s", tenant.slug)
            return len(rows)
        mail.send_failed_deliveries(
            recipients=recipients,
            tenant_name=tenant.name,
            run_date=day,
            documents=[
                {
                    "document_key": row.document_key,
                    "location": row.location.name if row.location_id else "—",
                    "error": row.error,
                    "created_at": row.created_at,
                }
                for row in rows
            ],
        )
        return len(rows)


def _recipients(tenant) -> list:
    """Whoever asked to be told, and nobody by default.

    Falls back to no one rather than to every `owner` and `admin`: an
    integration nobody configured must not start mailing people, which is §8's
    rule read one step further out.
    """
    stated = [
        one.strip()
        for one in (invoicing.read(tenant).get("notifications") or [])
        if isinstance(one, str) and one.strip()
    ]
    return stated


@app.periodic(cron="45 12 * * *")
@app.task(name="sweep_failed_delivery_digests", queue=QUEUE)
def sweep_failed_delivery_digests(timestamp):
    """Midday, per tenant. `(tenant_id, date)` -- a re-run on the same day
    notifies nobody twice."""
    del timestamp
    day = timezone.localdate()
    queued = 0
    for tenant_id in _tenants():
        with pinned_job({"tenant_id": tenant_id}):
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if tenant is None or not service.handoff_enabled(tenant):
                continue
        queued += 1 if enqueue_digest(tenant_id, day) else 0
    return queued


def enqueue_digest(tenant_id, run_date=None) -> bool:
    day = run_date or timezone.localdate()
    try:
        with transaction.atomic():
            notify_failed_deliveries.configure(
                queueing_lock=f"fiscal-digest:{tenant_id}:{day.isoformat()}",
            ).defer(tenant_id=str(tenant_id), run_date=day.isoformat())
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


def _tenants():
    with tenant_picker():
        return list(Tenant.objects.values_list("id", flat=True))
