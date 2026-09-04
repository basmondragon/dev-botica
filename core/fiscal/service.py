"""The sale handoff service — the ledger's sixth cross-stage service.

**Given a closed sale or a return and the caller's already-open pinned
transaction, it writes at most one `pending` row keyed by `document_key` and
enqueues delivery behind the commit.** S4 passes in the row and its transaction;
no payload, no target knowledge, no delivery call. S4 gets back the row, or
**nothing at all when no target is configured, which S4 treats as the normal
case rather than an error** (§8).

This module is the **only writer of `fiscal_documents`** (ledger).

**Nothing here may raise into a sale close.** The service runs inside the
transaction that lands the sale, so an unconfigured tenant, an invalid payload,
an unresolvable credential or an unreachable target must all leave through the
return statement rather than through an exception -- a handoff that could take a
sale down with it would put a legal obligation that moved to the client's system
back onto the counter's critical path, which is the one thing §8 bought us out
of.
"""

import logging
import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.counter import money
from core.fiscal import document as canonical, settings as invoicing, targets
from core.models import (
    FiscalDocument,
    FiscalDocumentStatus,
    Item,
    ItemBarcode,
    Sale,
    SaleSource,
    SaleStatus,
    Tenant,
)

logger = logging.getLogger(__name__)

#: A credit note's ordinal suffix, and the only part of a `document_key` that is
#: not a copy of the sale's own identity.
NC = re.compile(r"-NC(\d+)$")


class Refused(Exception):
    """The service will not build a document for this row.

    Raised only where a **caller** is wrong -- an `imported` sale, an open
    ticket -- and never where the *state of the world* is wrong. The first is a
    defect worth a stack trace in a shell; the second is a `failed` row on a work
    list.
    """


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


def handoff_enabled(tenant) -> bool:
    """**One predicate**, true when the `invoicing` settings group names a
    target **and** that target's credential resolves in the secrets store.

    Called in exactly three places -- this service, the sweep job, and the
    summary endpoint -- and a fourth place that decided for itself is how a demo
    instance grows a badge (§8).
    """
    spec, _target = targets.open_target(tenant, invoicing.read(tenant))
    return spec is not None


def configured_at(tenant):
    """The boundary that makes "no backfill" a fact rather than a convention.

    Nothing closed before it is queued, and the orphan check is bounded by the
    same timestamp.
    """
    from django.utils.dateparse import parse_datetime

    raw = invoicing.read(tenant).get("configured_at") or ""
    return parse_datetime(raw) if raw else None


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def base_key(sale) -> str:
    """`{location.code}-{sales.number}`, and it is **reconstructible from the
    sale alone by any process at any time**.

    `sales.number` is already unique within a location and composed
    `{device code}-{per-device sequence}` (S4), so the pair is tenant-unique,
    human-readable and stable across every retry. It contains no timestamp, no
    attempt counter and no random component, because anything that varies per
    attempt destroys the only guarantee it exists to provide (§8).
    """
    return f"{sale.location.code}-{sale.number}"


def credit_note_key(sale, ordinal: int) -> str:
    """`{location.code}-{sales.number}-NC{n}` for the n-th credit note.

    The ordinal is allocated once, stored on the row and never recomputed: what
    has to be reconstructible is the **sale's** key, because that is what a
    retry re-derives and what the far end dedupes on. A credit note carries its
    own key from the row instead.

    `UNIQUE (tenant_id, document_key)` is what makes two credit notes sharing an
    ordinal impossible -- and `_credit_note` is what makes a collision mean *take
    the next number* rather than *you already have one*, because two reversals
    racing against one sale are two documents and not a replay of one.
    """
    return f"{base_key(sale)}-NC{ordinal}"


def is_credit_note(row) -> bool:
    """**Derived, never stored** (*Data*).

    A document is a credit note exactly when it is not the sale's own base key.
    No `fiscal_document_type` enum is created: a column duplicating the fact the
    key already carries would eventually disagree with it, and a document whose
    type said `sale` while it reversed one is a defect no constraint catches.
    """
    return row.sale_return_id is not None or bool(NC.search(row.document_key or ""))


# ---------------------------------------------------------------------------
# The three entry points S4 calls
# ---------------------------------------------------------------------------


def hand_off_sale(sale, *, tenant=None):
    """One closed sale. Returns the row, or `None` when the handoff is off."""
    return _guarded(sale, tenant, _sale_document)


def hand_off_return(sale_return, *, tenant=None):
    """One devolución, as a credit note referencing the original.

    **Its payload is validated at delivery rather than at creation**, because a
    return's lines arrive *after* its header -- usually in the same batch, and
    not always, since a till's outbox drains in batches capped at
    `push_batch_max_rows`. A document built at the header would be a credit note
    with no lines either way, so the delivery job **waits** for them rather than
    failing (`core.fiscal.delivery`); the row is still written in the header's
    own pinned transaction, which is what the orphan check and the exactly-once
    constraint both rest on.
    """
    return _guarded(
        sale_return.sale, tenant, lambda *args: _return_document(sale_return, *args)
    )


def hand_off_void(sale, *, tenant=None):
    """A voided sale, as a credit note. **Always** (§8).

    The alternative -- cancelling a queued document so the target never hears of
    that sale -- was rejected: it produces a state where our record says a sale
    exists and the target has never seen the `sales.number` both systems
    reconcile on, and a sale number that reaches the target only sometimes is a
    reconciliation nobody can automate.
    """
    return _guarded(sale, tenant, _void_document)


def _guarded(sale, tenant, build):
    """The predicate, the refusals, and the promise never to raise into a close.

    Every exception this could produce is caught here and turned into either
    silence -- when nothing is configured -- or a `failed` row with a sentence
    on it, because the caller is a sale committing at a counter.
    """
    if sale is None:
        return None
    # **The tenant is the sale's, and a caller that says otherwise is refused.**
    # The `tenant` argument exists so a caller that already loaded the row does
    # not load it twice, not so it can name a different network: a document
    # written under one tenant for another network's sale would be a row RLS
    # hides from the only people who could notice it. Under a pin this is
    # unreachable; a management command or a job running as the migration role
    # holds BYPASSRLS, and this is what makes it unreachable there too.
    if tenant is not None and str(tenant.id) != str(sale.tenant_id):
        raise Refused(
            f"La venta {sale.number} es de otra droguería que la indicada, así "
            "que no se puede construir su documento."
        )
    tenant = tenant or Tenant.objects.filter(id=sale.tenant_id).first()
    if tenant is None:
        return None
    if sale.source != SaleSource.COUNTER:
        # **An imported sale never produces a document** (ledger, disputed
        # columns). It was rung up and invoiced in the client's previous system
        # long before Botica existed, and handing a month of history to an
        # invoicing system is a month of duplicate invoices (§8).
        raise Refused(
            f"La venta {sale.number} es historial cargado y no se envía a "
            "ningún sistema de facturación."
        )
    options = invoicing.read(tenant)
    spec, _target = targets.open_target(tenant, options)
    if spec is None:
        # **The default, and it is not an error.** No row, no queue, no job, no
        # figure, no badge (§8).
        return None
    try:
        # **Its own savepoint.** A database error inside the build would
        # otherwise mark the sale's transaction for rollback, and Django would
        # then refuse every statement on it -- so catching the exception would
        # save the handoff and lose the sale anyway.
        with transaction.atomic():
            return build(sale, tenant, options, spec)
    except Exception:  # noqa: BLE001 -- a sale is committing; nothing escapes
        logger.exception("the handoff service failed on sale %s", sale.id)
        return None


# ---------------------------------------------------------------------------
# Row creation
# ---------------------------------------------------------------------------


def _sale_document(sale, tenant, options, spec):
    if sale.status != SaleStatus.CLOSED:
        return None
    return _create(
        sale=sale,
        sale_return=None,
        tenant=tenant,
        options=options,
        spec=spec,
        document_key=base_key(sale),
        validate=True,
    )


def _return_document(sale_return, sale, tenant, options, spec):
    existing = FiscalDocument.objects.filter(
        tenant_id=sale.tenant_id, sale_return_id=sale_return.id
    ).first()
    if existing is not None:
        return existing
    return _credit_note(
        sale=sale,
        tenant=tenant,
        options=options,
        spec=spec,
        sale_return=sale_return,
        validate=False,
    )


def _void_document(sale, tenant, options, spec):
    existing = (
        FiscalDocument.objects.filter(
            tenant_id=sale.tenant_id, sale_id=sale.id, sale_return__isnull=True
        )
        .exclude(document_key=base_key(sale))
        .first()
    )
    if existing is not None:
        return existing
    return _credit_note(
        sale=sale, tenant=tenant, options=options, spec=spec, validate=True
    )


#: How many ordinals to walk past before giving up. A sale accumulating this
#: many credit notes in one instant is not a concurrency window, it is a defect
#: worth surfacing rather than looping over.
ORDINAL_ATTEMPTS = 20


def _credit_note(*, sale, tenant, options, spec, sale_return=None, validate):
    """Allocate the n-th credit note against a sale, **and mean the n-th**.

    `_next_ordinal` is an unlocked count, so two credit notes racing against one
    sale -- two tills pushing devoluciones, or a devolución and a void -- both
    compose `-NC1` and the second insert loses the unique constraint. Recovering
    by key alone would hand the loser the *winner's* row, and the caller would
    discard it: that return's refund would never reach the invoicing system and
    would surface only as an orphan.

    So the loser takes the next ordinal instead. The uniqueness stays in the
    database, which is where the ledger puts it; what this adds is that a
    collision means *pick another number*, not *you already have one*.
    """
    for _step in range(ORDINAL_ATTEMPTS):
        key = credit_note_key(sale, _next_ordinal(sale))
        row = _create(
            sale=None if sale_return is not None else sale,
            sale_return=sale_return,
            tenant=tenant,
            options=options,
            spec=spec,
            document_key=key,
            validate=validate,
            location_id=(sale_return.location_id if sale_return is not None else None),
        )
        if _is_ours(row, sale_return):
            return row
    raise Refused(
        f"No se pudo asignar un consecutivo de nota crédito para la venta "
        f"{sale.number} después de {ORDINAL_ATTEMPTS} intentos."
    )


def _is_ours(row, sale_return) -> bool:
    """Whether the row that came back describes the reversal we asked for.

    `_create` answers an existing row on a key collision, which is exactly right
    for a sale -- its key is reconstructible, so the row *is* the same document.
    A credit note's ordinal is allocated rather than derived, so the same key can
    name somebody else's reversal, and that is the case this tells apart.
    """
    if row is None:
        return False
    if sale_return is not None:
        return str(row.sale_return_id) == str(sale_return.id)
    return row.sale_return_id is None


def _next_ordinal(sale) -> int:
    """`n` for the n-th credit note against this sale."""
    return (
        FiscalDocument.objects.filter(tenant_id=sale.tenant_id)
        .filter(document_key__startswith=f"{base_key(sale)}-NC")
        .count()
        + 1
    )


def _create(
    *,
    sale,
    sale_return,
    tenant,
    options,
    spec,
    document_key,
    validate,
    location_id=None,
):
    """Write at most one row, and enqueue its delivery inside this transaction.

    The enqueue is in the same transaction as the row rather than after it: a
    job deferred through the same connection commits with the row and is
    invisible to a worker until it does, so there is no window in which a
    document exists with nothing coming for it. `next_attempt_at` is the
    backstop the sweep reads either way.
    """
    row = FiscalDocument.objects.filter(
        tenant_id=tenant.id, document_key=document_key
    ).first()
    if row is not None:
        # **Two concurrent calls for one sale produce one row.** The read is not
        # a lock, so the insert below can still lose the race -- and the unique
        # constraint is what makes the loser a no-op rather than an error.
        return row

    # The sede, denormalised so the work list and the per-sede count are one
    # indexed read. Exactly one of the two paths supplies it, and the assertion
    # is the caller's contract rather than a runtime possibility.
    sede = location_id if location_id is not None else sale.location_id
    version = mapping_version(spec, options)
    row = FiscalDocument(
        tenant_id=tenant.id,
        sale=sale,
        sale_return=sale_return,
        location_id=sede,
        document_key=document_key,
        target=spec.id,
        mapping_version=version,
        status=FiscalDocumentStatus.PENDING,
        next_attempt_at=timezone.now(),
    )

    if validate:
        try:
            render(row, tenant=tenant, options=options)
        except canonical.Incomplete as refusal:
            # **Never a refused sale and never an error at a counter.** The sale
            # is closed and the customer has left; this lands on the work list
            # with a sentence an administrator can act on, and `Reintentar`
            # rebuilds the payload from the sale as it then stands.
            row.status = FiscalDocumentStatus.FAILED
            row.error = str(refusal)
            row.next_attempt_at = None

    skew = _skew_beyond_tolerance(row, sale, sale_return, options)
    if skew and row.status == FiscalDocumentStatus.PENDING:
        # **Held rather than delivered**: a document dated two days wrong at the
        # far end is a correction someone makes by hand, and it is cheaper to
        # hold one than to unwind one (*Offline · clock skew*).
        row.next_attempt_at = None
        row.error = skew

    try:
        with transaction.atomic():
            row.save()
    except IntegrityError:
        winner = FiscalDocument.objects.filter(
            tenant_id=tenant.id, document_key=document_key
        ).first()
        if winner is None:
            raise
        return winner

    if row.next_attempt_at is not None:
        _enqueue(row)
    return row


def mapping_version(spec, options) -> str:
    """`{mapping}.{version}`, stamped on every row a mapping produces.

    Public because the delivery job stamps it too: a delivery that succeeded a
    year ago and fails today is otherwise indistinguishable from a target that
    changed its API.
    """
    from core.fiscal import mappings

    mapping_id = (options.get("mapping") or "") or spec.default_mapping
    if mapping_id not in spec.mappings:
        mapping_id = spec.default_mapping
    return f"{mapping_id}.{mappings.get(mapping_id).version}"


def _enqueue(row) -> None:
    from core.fiscal import jobs

    jobs.enqueue_delivery(row)


def _skew_beyond_tolerance(row, sale, sale_return, options) -> str:
    """`occurred_at` against `recorded_at`, in the tenant's own tolerance.

    Both clocks travel in the payload and neither is corrected (§5 rule 4). What
    this decides is only whether the document goes out now.
    """
    subject = sale_return or sale
    if subject is None or subject.occurred_at is None:
        return ""
    hours = int((options.get("retry") or {}).get("clock_skew_hours", 24))
    drift = abs((subject.occurred_at - subject.recorded_at).total_seconds())
    if drift <= hours * 3600:
        return ""
    del row
    return (
        f"El reloj del equipo está {int(drift // 3600)} horas fuera del "
        "servidor. El documento queda retenido hasta que alguien lo revise."
    )


# ---------------------------------------------------------------------------
# Rendering, which every attempt does again
# ---------------------------------------------------------------------------


def render(row, *, tenant=None, options=None) -> dict:
    """The canonical document for one row, **as it renders now**.

    A pure function of the sale (or the return) and the mapping version in
    force. Nothing reads `fiscal_documents.payload` back to re-send: that column
    is evidence of what was sent on the last attempt, and re-rendering the
    current truth is the *desired* behaviour on a retry, which is what makes a
    correction upstream -- an attached customer, a fixed NIT, a corrected
    description -- land without anyone editing a fiscal row.
    """
    tenant = tenant or Tenant.objects.filter(id=row.tenant_id).first()
    del options  # the mapping renames; the canonical document never varies
    source = source_for(row)
    items, barcodes = _catalog(row.tenant_id, source.lines)
    return canonical.build(
        source,
        tenant=tenant,
        location=source.sale.location,
        customer=source.sale.customer,
        items=items,
        barcodes=barcodes,
    )


def source_for(row) -> canonical.Source:
    """What this row describes: the sale, the return, or the void."""
    if row.sale_return_id is not None:
        return _return_source(row)
    sale = _sale_of(row)
    if is_credit_note(row):
        return _void_source(row, sale)
    return canonical.Source(
        sale=sale,
        type=canonical.SALE,
        document_key=row.document_key,
        occurred_at=sale.occurred_at,
        recorded_at=sale.recorded_at,
        lines=tuple(sale.lines.order_by("position")),
        payments=tuple(sale.payments.order_by("method")),
    )


def _sale_of(row) -> Sale:
    return _sale_by_id(row.sale_id)


def _sale_by_id(sale_id) -> Sale:
    """The sale a document describes, with everything the builder reads.

    A document is never written without one -- `sales` is `PROTECT` from
    `fiscal_documents` -- so a miss here is a corrupted database rather than a
    state the builder should carry a branch for, and it is worth the exception.
    """
    sale = (
        Sale.objects.select_related("location", "customer", "device")
        .filter(id=sale_id)
        .first()
    )
    if sale is None:
        raise Refused(
            f"El envío nombra la venta {sale_id}, que ya no está en la base de datos."
        )
    return sale


def _return_source(row) -> canonical.Source:
    sale_return = row.sale_return
    sale = _sale_by_id(sale_return.sale_id)
    lines = tuple(sale_return.lines.order_by("id"))
    total = sum(
        money.cents(line.unit_price) * line.quantity - money.cents(line.discount)
        for line in lines
    )
    return canonical.Source(
        sale=sale,
        type=canonical.CREDIT_NOTE,
        document_key=row.document_key,
        occurred_at=sale_return.occurred_at,
        recorded_at=sale_return.recorded_at,
        lines=lines,
        references={
            "sale_number": sale.number,
            "document_key": base_key(sale),
        },
        # One refund, by the method the money actually went back through.
        payments=(
            _Refund(method=sale_return.refund_method, amount=total, reference=""),
        ),
    )


def _void_source(row, sale) -> canonical.Source:
    """A void's credit note reverses **what the void actually reversed**.

    `returnable` is the same arithmetic S4's own reversal used, so the document
    and the `customer_return` moves describe one event rather than two -- a
    credit note for units a devolución had already put back would credit the
    customer twice for merchandise that came back once.
    """
    from core.counter import sales as sale_service

    outstanding = sale_service.returnable(sale)
    lines = tuple(
        _Reversal(line, outstanding.get(line.id, int(line.quantity)))
        for line in sale.lines.order_by("position")
        if outstanding.get(line.id, int(line.quantity)) > 0
    )
    total = sum(
        money.cents(line.unit_price) * line.quantity - money.cents(line.discount)
        for line in lines
    )
    return canonical.Source(
        sale=sale,
        type=canonical.CREDIT_NOTE,
        document_key=row.document_key,
        occurred_at=sale.voided_at or sale.occurred_at,
        recorded_at=sale.voided_at or sale.recorded_at,
        lines=lines,
        references={"sale_number": sale.number, "document_key": base_key(sale)},
        payments=tuple(_prorated(sale, total)),
    )


class _Reversal:
    """One reversed line, at the money the original line was charged at.

    Not a database row and never saved: a void writes no `sale_return_lines`,
    because the units came back through S4's own reversal and inventing return
    rows to describe a void would put two records of one event in two tables.
    """

    __slots__ = (
        "item_id",
        "quantity",
        "unit_price",
        "discount",
        "vat_class",
        "tax_amount",
    )

    def __init__(self, line, quantity: int):
        share = money.cents(line.discount) * quantity / max(1, int(line.quantity))
        self.item_id = line.item_id
        self.quantity = quantity
        self.unit_price = money.cents(line.unit_price)
        self.discount = money.cents(share)
        self.vat_class = line.vat_class
        self.tax_amount = canonical.line_tax(
            self.unit_price, quantity, self.discount, line.vat_class
        )


class _Refund:
    """A payment line on a credit note: the money that went back, by which
    means. Not a `payments` row -- that table records what was applied to a
    sale, and a refund is not one."""

    __slots__ = ("method", "amount", "reference")

    def __init__(self, method, amount, reference=""):
        self.method = method
        self.amount = amount
        self.reference = reference


def _prorated(sale, total):
    """The sale's own payment split, scaled to what the credit note reverses.

    A whole void is the overwhelming case and reproduces the split exactly. A
    void of a partially-returned sale reverses less than the sale, and stating
    the proportion is honest arithmetic where naming one arbitrary method would
    not be. The rounding remainder lands on the largest method, so the payments
    sum to the total to the peso -- which the builder asserts before anything is
    sent.
    """
    applied = list(sale.payments.order_by("-amount", "method"))
    if not applied:
        return []
    charged = sum(money.cents(payment.amount) for payment in applied)
    if charged <= 0:
        return []
    target = money.cents(total)
    shares = [
        _Refund(
            method=payment.method,
            amount=money.cents(money.cents(payment.amount) * target / charged),
            reference=payment.reference or "",
        )
        for payment in applied
    ]
    remainder = target - sum(share.amount for share in shares)
    shares[0].amount = money.cents(shares[0].amount + remainder)
    return [share for share in shares if share.amount > 0]


def _catalog(tenant_id, lines):
    """The items and primary barcodes one document needs, in two queries.

    Passed into the builder rather than queried inside it, because this runs on
    the push's own transaction and a lookup per line would put a join per ticket
    line on the one path §4 budgets.
    """
    ids = {line.item_id for line in lines}
    items = {
        item.id: item
        for item in Item.objects.filter(tenant_id=tenant_id, id__in=list(ids))
    }
    barcodes: dict = {}
    for code in ItemBarcode.objects.filter(
        tenant_id=tenant_id, item_id__in=list(ids)
    ).order_by("-is_primary", "code"):
        barcodes.setdefault(code.item_id, code.code)
    return items, barcodes
