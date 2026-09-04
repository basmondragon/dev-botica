"""One attempt at one document, and the rules that make it safe to repeat.

**A missing document is a retry; a duplicated one is a tax problem.** That
asymmetry is the whole shape of this module (§8): a second fiscal document at
the far end is signed, numbered and filed with the DIAN under the pharmacy's own
resolution, and unwinding it means issuing a credit note against a sale that
never happened.

Three rules follow, and none of them is negotiable.

**1 · A timeout is never resolved by re-sending.** After a timeout, a dropped
connection or a 5xx with no body, the outcome is *unknown* -- the target may have
committed. The next attempt **queries first** by `document_key`; only a query
that comes back empty leads to a second delivery.

**2 · A transport failure is never `failed` before the cap.** Telling a pharmacy
their invoicing system refused a document it never saw is worse than telling them
nothing. `attempts` increments, `next_attempt_at` moves along the ladder, and the
status stays `pending`.

**3 · A target that can neither dedupe nor be queried is delivered to once.** An
ambiguous outcome goes straight to the work list for a human. That is a
deliberately poor experience for a poorly-behaved target, and it is correct.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from core.fiscal import (
    document as canonical,
    service,
    settings as invoicing,
    targets,
)
from core.models import FiscalDocument, FiscalDocumentStatus, Tenant

logger = logging.getLogger(__name__)

#: **1 min · 5 · 15 · 60 · then hourly to the policy cap.** Minutes, and the
#: first step is a minute rather than a second because the overwhelming cause of
#: an unknown outcome is a link that is down, and four attempts in the first
#: four seconds buy nothing and fill a log.
LADDER_MINUTES = (1, 5, 15, 60)

#: How long a credit note waits for its own sale to reach the target. **A void
#: is a credit note and the two go out in order** (§8): a credit note referencing
#: a `sales.number` the target has never seen is a reconciliation nobody can
#: automate.
ORDER_WAIT_MINUTES = 1


def attempt(row_id, *, tenant_id) -> str:
    """One attempt at one document. Returns what it did, for the job's log.

    The queueing lock on `fiscal_document:{id}` means exactly one of these is in
    flight per document, so a forced retry pressed twice is one attempt.
    """
    row = (
        FiscalDocument.objects.select_related(
            "sale", "sale__location", "sale_return", "sale_return__sale"
        )
        .filter(id=row_id, tenant_id=tenant_id)
        .first()
    )
    if row is None:
        return "gone"
    if row.status == FiscalDocumentStatus.ACKNOWLEDGED:
        return "settled"

    tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant is None:
        return "gone"
    options = invoicing.read(tenant)
    spec, target = targets.open_target(tenant, options)
    if spec is None:
        # Disconnected while this was in flight. The row stays exactly as it is
        # and the settings section states how many deliveries are held -- the one
        # bounded exception to §8's silence, because it is a state a person
        # created deliberately two clicks ago.
        return "unconfigured"
    # **A row belongs to the target it was built for**, which is the column, not
    # whatever the settings now name. Switching from an API target to the file
    # export would otherwise strand every document already queued: this path
    # would call it batched and skip it, while the export filters on the column
    # and never sees it. Held, with the reason, is the honest answer -- somebody
    # changed the target and these documents need a decision.
    if row.target != spec.id:
        return _hold(
            row,
            "Este envío se creó para otro sistema de facturación. Vuelva a "
            "conectarlo para enviarlo, o resuélvalo en el sistema anterior.",
        )
    if spec.batched:
        # The file target sends nothing per document: its documents wait for the
        # period's export and are acknowledged when the file lands.
        return "batched"

    waiting = _waiting_for_its_sale(row)
    if waiting:
        return _hold_for_order(row, waiting)

    if row.status == FiscalDocumentStatus.SENT:
        # The dwell. **A re-query, never a second delivery.**
        return _query_only(row, target, spec, options)

    return _deliver(row, target, spec, options, tenant)


def _deliver(row, target, spec, options, tenant) -> str:
    """Render, then either query first or send."""
    if _lines_still_arriving(row):
        # **A credit note whose own lines have not landed yet is waited for, not
        # failed.** A till's outbox is drained in batches capped at
        # `push_batch_max_rows`, so a devolución's header and its lines can
        # arrive in different pushes -- and a document built between the two has
        # no lines through no fault of anyone's. Failing it would put an
        # unactionable row on the work list for a return that is arriving
        # normally, which is the opposite of "reconciles when the connection
        # returns without anyone being asked" (§5).
        return _wait(row, "Esperando las líneas de la devolución.", options)
    try:
        payload = service.render(row, tenant=tenant, options=options)
    except canonical.Incomplete as refusal:
        return fail(row, str(refusal))
    except Exception as failure:  # noqa: BLE001 -- a builder defect is a work-list row
        logger.exception("could not render document %s", row.document_key)
        return fail(
            row,
            "No se pudo construir el documento a partir de esta venta. "
            f"Detalle técnico: {type(failure).__name__}.",
        )

    if row.attempts > 0:
        # **Rule 1.** The previous attempt ended without an answer, so the far
        # end may hold it. Ask before sending.
        outcome = target.query(row.document_key)
        if outcome.state == targets.HELD:
            return _acknowledge(row, outcome, payload)
        if outcome.state == targets.UNKNOWN:
            # The target is unreachable; there is nothing to learn and nothing
            # safe to send.
            return _retry(row, outcome.reason, options)

    outcome = target.deliver(payload)
    row.payload = payload
    row.mapping_version = service.mapping_version(spec, options)
    if outcome.state == targets.HELD:
        return _acknowledge(row, outcome, payload)
    if outcome.state == targets.TAKEN:
        if not _can_resolve_ambiguity(target, spec, options):
            # Taken, and nobody can ever ask whether it landed. Held rather than
            # left in `sent` with a dwell that has nothing to dwell for: a
            # re-query against a target with no query operation would come back
            # empty and read as a refusal of a document it actually holds.
            return _hold(
                row,
                "El sistema de facturación recibió el documento pero no "
                "confirma su estado ni permite consultarlo. Verifíquelo en ese "
                "sistema.",
            )
        return _taken(row, outcome, options)
    if outcome.state == targets.REFUSED:
        row.response = outcome.response or {}
        return fail(row, outcome.reason)
    if not _can_resolve_ambiguity(target, spec, options):
        # **Rule 3.** One attempt, and then a human.
        return _hold(
            row,
            "El sistema de facturación no confirma si recibió el documento y no "
            "permite consultarlo. No se reintenta solo: revíselo antes de "
            "volver a enviarlo.",
        )
    return _retry(row, outcome.reason, options)


def _query_only(row, target, spec, options) -> str:
    """The dwell, and **only where there is something to ask.**

    A target with no query operation cannot answer, and reading its silence as a
    refusal would write `Falló el envío` on a document it holds -- then a forced
    retry would re-send blind to a system that cannot dedupe, which is the
    duplicate this whole stage exists to prevent.
    """
    if not _can_resolve_ambiguity(target, spec, options):
        return _hold(
            row,
            "El sistema de facturación recibió el documento pero no confirma su "
            "estado ni permite consultarlo. Verifíquelo en ese sistema.",
        )
    outcome = target.query(row.document_key)
    if outcome.state == targets.HELD:
        return _acknowledge(row, outcome, row.payload)
    if outcome.state == targets.UNKNOWN:
        return _retry(row, outcome.reason, options, keep_status=True)
    # The target took the delivery and no longer holds it. That is a refusal
    # arriving late, and it belongs on the work list rather than in a loop.
    return fail(
        row,
        "El sistema de facturación aceptó el envío pero no tiene el documento. "
        "Revíselo en ese sistema antes de reintentar.",
    )


def _can_resolve_ambiguity(target, spec, options) -> bool:
    """Whether an unknown outcome can ever be settled without re-sending.

    **The one question asked of every new target, before anything else**, is
    which field carries `document_key`. A target with no such field must declare
    a `query` operation; a target with neither is capped at one attempt.
    """
    del spec
    mapping = getattr(target, "mapping", None)
    if mapping is not None and mapping.idempotency_field:
        return True
    return bool(getattr(target, "supports_query", False))


def _waiting_for_its_sale(row):
    """A credit note whose own sale has not reached the target yet.

    Applied to every credit note and not only to a void's: a credit note that
    arrives at a system which has never seen the `sales.number` it references is
    exactly the hole the void rule exists to close.
    """
    if not service.is_credit_note(row):
        return None
    sale = row.sale or (row.sale_return.sale if row.sale_return_id else None)
    if sale is None:
        return None
    original = FiscalDocument.objects.filter(
        tenant_id=row.tenant_id, document_key=service.base_key(sale)
    ).first()
    if original is None:
        # The sale was closed before a target was configured, so no document was
        # ever due for it (§8, no backfill). The credit note stands alone rather
        # than waiting for something nobody queued.
        return None
    if original.status in (
        FiscalDocumentStatus.SENT,
        FiscalDocumentStatus.ACKNOWLEDGED,
    ):
        return None
    return original


def _lines_still_arriving(row) -> bool:
    """A credit note against a return that has landed no line yet."""
    return row.sale_return_id is not None and not row.sale_return.lines.exists()


def _wait(row, reason, options) -> str:
    """Come back shortly, without spending an attempt.

    `attempts` does not move, because nothing was attempted -- but the policy
    cap still applies, so a document waiting on something that never arrives
    ends on the work list rather than rescheduling itself for ever.
    """
    cap_hours = int((options.get("retry") or {}).get("cap_hours", 24))
    if (timezone.now() - row.created_at).total_seconds() > cap_hours * 3600:
        return fail(row, f"{reason} Se esperó {cap_hours} horas sin recibirlas.")
    row.error = reason
    row.next_attempt_at = timezone.now() + timedelta(minutes=ORDER_WAIT_MINUTES)
    _save(row, "error", "next_attempt_at")
    return "waiting"


def _hold_for_order(row, original) -> str:
    if original.status == FiscalDocumentStatus.FAILED:
        return _hold(
            row,
            f"La venta {original.document_key} todavía no llegó al sistema de "
            "facturación. Resuelva ese envío primero.",
        )
    row.error = "Esperando el envío de la venta original."
    row.next_attempt_at = timezone.now() + timedelta(minutes=ORDER_WAIT_MINUTES)
    _save(row, "error", "next_attempt_at")
    return "waiting"


# ---------------------------------------------------------------------------
# The five landings
# ---------------------------------------------------------------------------


def _acknowledge(row, outcome, payload) -> str:
    """Terminal success, and **a statement about the target, never about the
    DIAN** (§8, A9).

    `external_number`, `cude` and `pdf_url` land here where the target returns
    them; null is normal and is not a failure -- the handoff succeeding is what
    `acknowledged` records.
    """
    row.status = FiscalDocumentStatus.ACKNOWLEDGED
    row.acknowledged_at = timezone.now()
    row.next_attempt_at = None
    row.error = ""
    row.payload = payload or row.payload
    row.external_number = outcome.external_number or row.external_number
    row.cude = outcome.cude or row.cude
    row.pdf_url = outcome.pdf_url or row.pdf_url
    row.response = outcome.response or row.response
    row.attempts += 1
    _save(
        row,
        "status",
        "acknowledged_at",
        "next_attempt_at",
        "error",
        "payload",
        "external_number",
        "cude",
        "pdf_url",
        "response",
        "attempts",
        "mapping_version",
    )
    return "acknowledged"


def _taken(row, outcome, options) -> str:
    """Delivered; the target has not confirmed yet. Re-queried after the dwell."""
    dwell = int((options.get("retry") or {}).get("dwell_minutes", 30))
    row.status = FiscalDocumentStatus.SENT
    row.sent_at = timezone.now()
    row.next_attempt_at = row.sent_at + timedelta(minutes=dwell)
    row.error = ""
    row.response = outcome.response or {}
    row.attempts += 1
    _save(
        row,
        "status",
        "sent_at",
        "next_attempt_at",
        "error",
        "response",
        "attempts",
        "payload",
        "mapping_version",
    )
    return "sent"


def fail(row, reason) -> str:
    """The work list, never a till. `error` says what a person must do.

    **`failed` is not terminal.** This is a handoff, not a filing, and the
    correction is a rebuild rather than a new document: `Reintentar` moves the
    row back to `pending` and the next attempt renders the sale as it then
    stands.
    """
    row.status = FiscalDocumentStatus.FAILED
    row.error = (reason or "")[:1000]
    row.next_attempt_at = None
    row.attempts += 1
    _save(
        row,
        "status",
        "error",
        "next_attempt_at",
        "attempts",
        "payload",
        "response",
        "mapping_version",
    )
    return "failed"


def _hold(row, reason) -> str:
    """`next_attempt_at` null means **held, not queued**.

    Held and stuck look identical from a count, which is why the work list shows
    the reason and the checks assert the null and the empty request log
    together.
    """
    row.error = (reason or "")[:1000]
    row.next_attempt_at = None
    row.attempts += 1
    _save(row, "error", "next_attempt_at", "attempts", "payload", "mapping_version")
    return "held"


def _retry(row, reason, options, *, keep_status=False) -> str:
    """A transport failure. **Never `failed` before the cap.**"""
    row.attempts += 1
    cap_hours = int((options.get("retry") or {}).get("cap_hours", 24))
    age = (timezone.now() - row.created_at).total_seconds()
    if age > cap_hours * 3600:
        return fail(
            row,
            f"{reason or 'No hay conexión con el sistema de facturación.'} "
            f"Se reintentó durante {cap_hours} horas sin respuesta.",
        )
    step = LADDER_MINUTES[min(row.attempts, len(LADDER_MINUTES)) - 1]
    if row.attempts > len(LADDER_MINUTES):
        step = LADDER_MINUTES[-1]
    row.error = reason or "No hay conexión con el sistema de facturación."
    row.next_attempt_at = timezone.now() + timedelta(minutes=step)
    if not keep_status:
        row.status = FiscalDocumentStatus.PENDING
    _save(
        row,
        "status",
        "error",
        "next_attempt_at",
        "attempts",
        "payload",
        "mapping_version",
    )
    return "pending"


def _save(row, *fields) -> None:
    row.save(update_fields=[*fields, "updated_at"])


# ---------------------------------------------------------------------------
# The forced retry
# ---------------------------------------------------------------------------


def requeue(row) -> None:
    """`Reintentar`: move a row back to `pending` and queue it now.

    **Nothing about the fiscal row is edited** beyond its own state -- the
    payload is rebuilt from the sale as it now stands on the next attempt, which
    is what makes fixing the cause where the cause lives the whole of the
    correction.
    """
    row.status = (
        FiscalDocumentStatus.PENDING
        if row.status != FiscalDocumentStatus.SENT
        else row.status
    )
    row.next_attempt_at = timezone.now()
    row.error = ""
    _save(row, "status", "next_attempt_at", "error")
