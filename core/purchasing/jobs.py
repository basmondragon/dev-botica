"""S6's background jobs, all on Procrastinate's Postgres queue.

Every one of them carries `tenant_id` in its payload and **pins before touching
anything** (ledger rule 6, context three). That matters more here than anywhere
else in the stage: under `FORCE ROW LEVEL SECURITY` an unpinned connection reads
and writes zero rows, so a job that took its pin from somewhere other than its
payload would complete, log a success and write nothing at all.

*The stage document's Jobs table names four; there are five.* The fifth is the
supplier dispatch, which the same document requires in two other places --
`Aprobar y enviar` "enqueues the supplier dispatch", and the failure state names
the reason, the retry count and `[Reintentar ahora]` beside
`[Marcar como enviada]`. A dispatch that ran inside the approval request would
make an administrator wait on an SMTP relay to find out whether their own
approval was recorded.

**The refresh's hour is the tenant's, not the deployment's.** `refresh_hour`
lives in the `purchasing` group, so the two sweeps run hourly and enqueue only
where the tenant's local clock has reached its own hour -- a fixed cron would be
a setting the group advertises and nothing honours.
"""

import logging
from datetime import date
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import exceptions as queue_exceptions
from procrastinate.contrib.django import app

from core import audit, gateway, mail
from core.models import (
    AuditAction,
    ForecastBasis,
    Location,
    LocationStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    StockOnHand,
    Tenant,
)
from core.purchasing import (
    forecast,
    orders as order_service,
    reason_text,
    reasons,
    receiving,
    settings as purchasing_settings,
)
from core.tenancy import pinned_job, tenant_picker

logger = logging.getLogger(__name__)

QUEUE = "purchasing"

#: The business clock every `refresh_hour` is read against (§1: one country, one
#: timezone, and no `i18n` runtime).
BUSINESS_TIMEZONE = ZoneInfo("America/Bogota")

#: How long after the refresh generation runs. One hour, which is what makes the
#: handoff's own `actualizado hoy 06:00` true when an administrator opens the
#: screen at a `refresh_hour` of 4.
GENERATION_OFFSET_HOURS = 1


def _locations(tenant_id):
    return list(
        Location.objects.filter(
            tenant_id=tenant_id, status=LocationStatus.ACTIVE
        ).order_by("name")
    )


def _local_hour(now=None) -> int:
    return (now or timezone.now()).astimezone(BUSINESS_TIMEZONE).hour


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# 1 · the forecast refresh
# ---------------------------------------------------------------------------


@app.task(
    name="purchasing_forecast_refresh",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=60, exponential_wait=2),
)
def forecast_refresh(*, tenant_id, location_id, run_date):
    """Recompute one sede's whole forecast.

    **On failure the previous run's rows stand.** The screen shows the older
    `computed_at` and the provenance line says so -- it never shows a fresh
    timestamp over stale numbers, which would be the one lie a buyer could not
    detect.
    """
    day = _as_date(run_date)
    with pinned_job({"tenant_id": tenant_id}):
        report = forecast.refresh(tenant_id, location_id, today=day)
        logger.info("forecast refresh %s at %s: %s", day, location_id, report)
        return report


def enqueue_refresh(tenant_id, location_id, run_date=None):
    day = run_date or timezone.localdate()
    try:
        # **Its own savepoint.** A colliding queueing lock raises
        # `IntegrityError`, and catching one does not un-poison the transaction
        # it was raised in -- the request that enqueued would then fail on its
        # next statement, which is how a duplicate enqueue becomes a 500 on the
        # button that caused it. S0's own `enqueue_invitation_email` takes the
        # same shape for the same reason.
        with transaction.atomic():
            forecast_refresh.configure(
                queueing_lock=(f"forecast:{tenant_id}:{location_id}:{day.isoformat()}"),
            ).defer(
                tenant_id=str(tenant_id),
                location_id=str(location_id),
                run_date=day.isoformat(),
            )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="5 * * * *")
@app.task(name="purchasing_sweep_forecast_refresh", queue=QUEUE)
def sweep_forecast_refresh(timestamp):
    """Fan the refresh out at each tenant's own `refresh_hour`."""
    del timestamp
    return _sweep(
        offset=0,
        enqueue=lambda tenant_id, location_id, day: enqueue_refresh(
            tenant_id, location_id, day
        ),
    )


# ---------------------------------------------------------------------------
# 2 · the order generation
# ---------------------------------------------------------------------------


@app.task(
    name="purchasing_order_generate",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=60, exponential_wait=2),
)
def order_generate(*, tenant_id, location_id, run_date):
    """One `suggested` order per supplier at one sede.

    **A second run on the same day updates the existing order in place and never
    creates a second one**, and it never touches an order past `suggested`. On
    failure no order exists for that supplier and the nav counter is simply
    lower; the empty state says the model has not produced one.
    """
    day = _as_date(run_date)
    with pinned_job({"tenant_id": tenant_id}):
        built = order_service.generate(tenant_id, location_id, today=day)
        order_ids = [str(order.id) for order in built]
    for order_id in order_ids:
        enqueue_reason_text(tenant_id, order_id)
    return {"orders": len(order_ids)}


def enqueue_generate(tenant_id, location_id, run_date=None):
    day = run_date or timezone.localdate()
    try:
        with transaction.atomic():
            order_generate.configure(
                queueing_lock=(f"generate:{tenant_id}:{location_id}:{day.isoformat()}"),
            ).defer(
                tenant_id=str(tenant_id),
                location_id=str(location_id),
                run_date=day.isoformat(),
            )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


@app.periodic(cron="10 * * * *")
@app.task(name="purchasing_sweep_order_generate", queue=QUEUE)
def sweep_order_generate(timestamp):
    """Generation follows the refresh by an hour, at each tenant's own clock."""
    del timestamp
    return _sweep(
        offset=GENERATION_OFFSET_HOURS,
        enqueue=lambda tenant_id, location_id, day: enqueue_generate(
            tenant_id, location_id, day
        ),
    )


def _sweep(*, offset, enqueue):
    """The shape both sweeps share: every tenant whose local hour has come."""
    hour = _local_hour()
    day = timezone.now().astimezone(BUSINESS_TIMEZONE).date()
    with tenant_picker():
        tenants = list(Tenant.objects.values_list("id", flat=True))
    queued = 0
    for tenant_id in tenants:
        with pinned_job({"tenant_id": tenant_id}):
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if tenant is None:
                continue
            wanted = (
                int(purchasing_settings.read(tenant)["refresh_hour"]) + offset
            ) % 24
            if wanted != hour:
                continue
            locations = [one.id for one in _locations(tenant_id)]
        for location_id in locations:
            queued += 1 if enqueue(tenant_id, location_id, day) else 0
    return queued


# ---------------------------------------------------------------------------
# 3 · the reason text
# ---------------------------------------------------------------------------


@app.task(
    name="purchasing_reason_text",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=3, wait=30, exponential_wait=2),
)
def order_reason_text(*, tenant_id, purchase_order_id):
    """Turn one order's reason codes into one Spanish clause per `learned` line.

    **The order is fully usable without this.** After the last attempt `reason`
    stays null on every line, the screen renders each line's `reason_code`
    string, and the filter bar's right slot appends `· sin redacción del
    modelo`. No banner, no modal: saying it loudly would teach people to
    distrust a working screen (§10).
    """
    with pinned_job({"tenant_id": tenant_id}):
        tenant = Tenant.objects.filter(id=tenant_id).first()
        order = PurchaseOrder.objects.filter(id=purchase_order_id).first()
        if tenant is None or order is None:
            return 0
        options = purchasing_settings.read(tenant)
        if not options["reason_text_enabled"]:
            logger.info("reason text is off for tenant %s", tenant_id)
            return 0
        try:
            return reason_text.write(order, tenant=tenant, facts=_facts(order))
        except gateway.Unavailable as unreachable:
            logger.info("gateway unavailable for order %s: %s", order.id, unreachable)
            return 0


def _facts(order):
    """The order's `learned` lines as the model sees them.

    **Only `learned` lines are read at all.** A language model asked to dress up
    *we have no history* writes a finding, and shipping an invented finding is
    the one thing this stage must never do (§1) -- so the filter is here, in the
    only query that feeds the gateway, and again in the table's own CHECK.
    """
    lines = list(
        PurchaseOrderLine.objects.filter(
            purchase_order=order, basis=ForecastBasis.LEARNED
        ).select_related("item")
    )
    held = dict(
        StockOnHand.objects.filter(
            tenant_id=order.tenant_id,
            location_id=order.location_id,
            item_id__in=[line.item_id for line in lines],
        )
        .values_list("item_id")
        .annotate(total=Sum("quantity"))
    )
    return [
        reason_text.Fact(
            line_id=str(line.id),
            item=line.item.name,
            presentation=line.item.presentation,
            quantity=line.approved_quantity,
            stock=int(held.get(line.item_id) or 0),
            coverage_days=(
                float(line.coverage_days) if line.coverage_days is not None else None
            ),
            reason_code=line.reason_code,
            reason_text=reasons.render(line.reason_code),
        )
        for line in lines
    ]


def enqueue_reason_text(tenant_id, purchase_order_id):
    try:
        with transaction.atomic():
            order_reason_text.configure(
                queueing_lock=f"reason_text:{tenant_id}:{purchase_order_id}",
            ).defer(
                tenant_id=str(tenant_id),
                purchase_order_id=str(purchase_order_id),
            )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


# ---------------------------------------------------------------------------
# 4 · the supplier's observed lead time
# ---------------------------------------------------------------------------


@app.task(
    name="purchasing_lead_time_refresh",
    queue=QUEUE,
    retry=RetryStrategy(max_attempts=5, wait=30, exponential_wait=2),
)
def lead_time_refresh(*, tenant_id, goods_receipt_id, supplier_id):
    """Rewrite `suppliers.lead_time_days` from what the last ten deliveries took.

    On failure the lead time keeps its previous value and the next receipt
    recomputes it: a supplier's lead time is a rolling observation, so a missed
    recomputation costs one observation and never a wrong number.
    """
    del goods_receipt_id  # part of the queueing lock, not of the work
    with pinned_job({"tenant_id": tenant_id}):
        observed = receiving.refresh_lead_time(tenant_id, supplier_id)
        logger.info("supplier %s lead time: %s", supplier_id, observed)
        return observed


def enqueue_lead_time(tenant_id, goods_receipt_id, supplier_id):
    try:
        with transaction.atomic():
            lead_time_refresh.configure(
                queueing_lock=f"lead_time:{tenant_id}:{goods_receipt_id}",
            ).defer(
                tenant_id=str(tenant_id),
                goods_receipt_id=str(goods_receipt_id),
                supplier_id=str(supplier_id),
            )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True


# ---------------------------------------------------------------------------
# 5 · the supplier dispatch
# ---------------------------------------------------------------------------


@app.task(
    name="purchasing_order_dispatch",
    queue=QUEUE,
    retry=RetryStrategy(
        max_attempts=5,
        wait=30,
        exponential_wait=2,
        retry_exceptions=(mail.DeliveryFailed,),
    ),
)
def order_dispatch(*, tenant_id, purchase_order_id):
    """Send one approved order to its supplier, by email with the lines on it.

    **Dispatch is email and nothing else** (§10's transactional email): there is
    no EDI, no supplier portal and no supplier API at v1, and a supplier with no
    address on file is dispatched by a person who then presses `Marcar como
    enviada`.
    """
    with pinned_job({"tenant_id": tenant_id}):
        order = (
            PurchaseOrder.objects.filter(id=purchase_order_id)
            .select_related("supplier", "location", "tenant")
            .first()
        )
        if order is None:
            return None
        if order.status != PurchaseOrderStatus.APPROVED:
            # Already sent, already discarded, or already received. A retry that
            # re-sent an order a supplier has shipped is worse than one that
            # does nothing.
            return order.status

        address = _supplier_address(order.supplier)
        lines = list(
            PurchaseOrderLine.objects.filter(
                purchase_order=order, approved_quantity__gt=0
            ).select_related("item")
        )
        if not address:
            order_service.record_dispatch_failure(
                order,
                "El proveedor no tiene correo registrado. Envíe la orden por su "
                "canal habitual y márquela como enviada.",
            )
            return "no_address"
        try:
            mail.send_purchase_order(
                email=address,
                tenant_name=order.tenant.name,
                location_name=order.location.name,
                supplier_name=order.supplier.name,
                number=order.number,
                total=order.total,
                lines=[
                    {
                        "item": line.item.name,
                        "presentation": line.item.presentation,
                        "quantity": line.approved_quantity,
                        "unit_cost": line.unit_cost,
                    }
                    for line in lines
                ],
            )
        except mail.DeliveryFailed as failure:
            order_service.record_dispatch_failure(order, str(failure))
            raise
        order_service.mark_sent(order)
        audit.record(
            actor=None,
            tenant_id=tenant_id,
            action=AuditAction.SEND,
            entity_type="purchase_orders",
            entity_id=order.id,
            before={"status": PurchaseOrderStatus.APPROVED},
            after={"status": PurchaseOrderStatus.SENT, "to": address},
            request_id=f"dispatch:{order.id}",
        )
        return PurchaseOrderStatus.SENT


def _supplier_address(supplier) -> str:
    """The address on the supplier's `contact` field, where there is one.

    S1 models a supplier's contact as one free-text line, so this reads an
    address out of it rather than inventing a column S6 does not own.
    """
    for token in (supplier.contact or "").replace(",", " ").split():
        if "@" in token and "." in token.split("@")[-1]:
            return token.strip().strip("<>")
    return ""


def enqueue_dispatch(tenant_id, purchase_order_id, *, attempt=0):
    try:
        with transaction.atomic():
            order_dispatch.configure(
                queueing_lock=f"dispatch:{tenant_id}:{purchase_order_id}:{attempt}",
            ).defer(
                tenant_id=str(tenant_id),
                purchase_order_id=str(purchase_order_id),
            )
    except (queue_exceptions.AlreadyEnqueued, IntegrityError):
        return False
    return True
