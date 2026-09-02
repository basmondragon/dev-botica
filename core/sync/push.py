"""The idempotent client-write service (ledger rule 8, cross-stage services, A5).

**S3, S4, S5 and S8 call this and none of them dedupes by hand.** One batch, one
pinned transaction, a per-row outcome, and exactly two idempotency forms:

  `(tenant_id, client_uuid)`  for every table in rule 8's list
  a declared natural key      for a collection outside it -- at S2, `customers`

A table with neither is not pushable and the registry refuses it. There is no
third form, and a stage that wants one brings it to S2's document first.

**Where the line between a batch rejection and a row rejection is drawn, and
why.** A row naming another tenant, or a location this device is not at, rejects
the **whole batch** with no row applied. It is filtered out of nothing:
filtering would apply the remaining rows from a client we have just established
we cannot trust about which tenant it is in. A row that fails domain validation
is rejected **on its own**, because one malformed customer must not wedge nine
good sales behind it. Draw it too strictly and a single poisoned row stops a
till syncing until an engineer intervenes; draw it too loosely and a broken
client writes into a tenant it does not belong to.

**`duplicate` is a success.** A client that treats it as an error can never
drain a queue whose push timed out after the server committed, which is the
exact failure A5 exists to make safe -- and it is the only failure worth
engineering for, because it is the one that actually happens.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from core.models import Customer, SyncConflictType
from core.sync import conflicts, registry
from core.tenancy import ForeignTenantRow

logger = logging.getLogger(__name__)

APPLIED = "applied"
DUPLICATE = "duplicate"
MERGED = "merged"
REJECTED = "rejected"


class ForeignLocationRow(Exception):
    """A batch element naming a location this device is not at.

    The same shape as `ForeignTenantRow` and for the same reason: a client wrong
    about which sede it is at is a client whose other rows are not evidence of
    anything either.
    """


class BatchTooLarge(Exception):
    """A batch over `push_batch_max_rows` or `push_batch_max_bytes`.

    Refused whole rather than split. Splitting is the client's job, and a server
    that silently halved a batch would make the outbox and the response
    disagree about which rows were sent.
    """


@dataclass
class Outcome:
    """One row's answer. `id` is the server's row id for every success --
    including `merged`, where it is the id the client's local row adopts."""

    client_uuid: str
    outcome: str
    id: str | None = None
    reason: str = ""


@dataclass
class Result:
    batch_id: str
    outcomes: list[Outcome] = field(default_factory=list)
    raised: list = field(default_factory=list)


def check_size(rows, options):
    """The two caps, checked before anything is applied."""
    limit = int(options["push_batch_max_rows"])
    if len(rows) > limit:
        raise BatchTooLarge(
            f"Este envío trae {len(rows)} filas y el máximo es {limit}. "
            "Divídalo en lotes más pequeños."
        )
    size = len(json.dumps(rows, default=str).encode("utf-8"))
    cap = int(options["push_batch_max_bytes"])
    if size > cap:
        raise BatchTooLarge(
            f"Este envío pesa {size} bytes y el máximo es {cap}. Divídalo en "
            "lotes más pequeños."
        )


def check_provenance(device, rows):
    """Rule 6's fourth context, as the whole-batch half of the rejection rule.

    Run over the **entire** batch before a single row is applied, so a foreign
    row in the last position rejects the first as surely as one in the first
    position rejects the last.
    """
    for index, row in enumerate(rows):
        payload = row.get("payload") or {}
        named_tenant = payload.get("tenant_id")
        if named_tenant is not None and str(named_tenant) != str(device.tenant_id):
            raise ForeignTenantRow(
                f"Element {index} of this batch names tenant {named_tenant}, and "
                f"this device's session resolved {device.tenant_id}. The batch "
                "is rejected."
            )
        named_location = payload.get("location_id")
        if named_location is not None and str(named_location) != str(
            device.location_id
        ):
            raise ForeignLocationRow(
                f"Element {index} of this batch names location {named_location}, "
                f"and this device is at {device.location_id}. The batch is "
                "rejected."
            )


def apply_batch(device, batch_id, rows, *, options, request_id=""):
    """Apply one batch and answer a per-row outcome.

    The caller has already opened the pinned transaction -- this runs inside
    `core.tenancy.pinned_batch`, which is rule 6's fourth context and is S0's,
    not this stage's. Rows are applied in `client_uuid` order, which is
    chronological because `client_uuid` is uuid v7.
    """
    result = Result(batch_id=batch_id)
    for row in sorted(rows, key=lambda one: str(one.get("client_uuid") or "")):
        result.outcomes.append(
            _apply_row(device, row, options=options, request_id=request_id)
        )
    return result


def _apply_row(device, row, *, options, request_id):
    client_uuid = str(row.get("client_uuid") or "")
    name = row.get("collection") or ""
    try:
        collection = registry.pushable(name)
    except (LookupError, registry.Unpushable) as refusal:
        # A per-row rejection and not a batch one: an unknown collection is a
        # client that is out of date about the registry, not a client that is
        # wrong about which network it is in.
        conflicts.raise_conflict(
            device=device,
            type=SyncConflictType.UNKNOWN_COLLECTION,
            collection=name[:64],
            client_uuid=client_uuid or None,
            occurred_at=_occurred(row),
            detail={"reason": "unknown_collection", "request_id": request_id},
        )
        return Outcome(client_uuid, REJECTED, reason=str(refusal))

    writer = WRITERS.get(collection.name, _write_by_client_uuid)
    try:
        # A savepoint per row, so a rejected row leaves the nine good ones in
        # the batch applied. Without it the first `IntegrityError` would poison
        # the transaction and every later statement would fail on the
        # connection rather than on its own merits.
        with transaction.atomic():
            return writer(device, collection, row, client_uuid, options)
    except ROW_FAILURES as refusal:
        # A `DatabaseError` reaching here has already rolled back to this row's
        # savepoint, so the rest of the batch is still applicable. It is
        # answered as a rejection rather than as a 500, because **one malformed
        # customer must not wedge nine good sales behind it** -- and a 500 would
        # additionally roll back S0's middleware transaction and lose the
        # conflict row this writes.
        if not isinstance(refusal, Rejected):
            logger.warning(
                "push row %s on %s was refused by the database", client_uuid, name
            )
            refusal = Rejected(
                "El servidor no pudo guardar esta fila.",
                code="database_refused",
            )
        conflicts.raise_conflict(
            device=device,
            type=SyncConflictType.PAYLOAD_REJECTED,
            collection=collection.name,
            client_uuid=client_uuid or None,
            occurred_at=_occurred(row),
            # **Never the payload verbatim.** A rejected `customers` row carries
            # a person's document number, and a conflict queue is not a place to
            # accumulate identifying data nobody asked to store (Ley 1581).
            detail={
                "reason": refusal.code,
                "field": refusal.field,
                "request_id": request_id,
            },
        )
        return Outcome(client_uuid, REJECTED, reason=str(refusal))


def _text(value) -> str:
    """One payload field, as text. `None` is the empty string, and anything else
    is whatever it prints as -- bounded a few lines later."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


class Rejected(Exception):
    """Domain validation refused this row. Per-row, never per-batch."""

    def __init__(self, message, *, code, field=""):
        super().__init__(message)
        self.code = code
        self.field = field


#: What one row being malformed may raise, and what is therefore **that row's
#: problem and not the batch's**. Everything here has already rolled back to the
#: row's own savepoint by the time it is caught, so the rest of the batch is
#: still applicable -- which is the whole line *Batch semantics* draws: one
#: malformed customer must not wedge nine good sales behind it.
ROW_FAILURES = (Rejected, DatabaseError, ValidationError, TypeError, ValueError)


def _occurred(row):
    """The device's clock, exactly as it sent it -- never adjusted (§5 rule 4)."""
    from django.utils.dateparse import parse_datetime

    raw = row.get("occurred_at")
    if not raw:
        return None
    parsed = parse_datetime(str(raw)) if not isinstance(raw, datetime) else raw
    if parsed is None:
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


# ---------------------------------------------------------------------------
# The two idempotency forms.
# ---------------------------------------------------------------------------


def _write_by_client_uuid(device, collection, row, client_uuid, options):
    """Rule 8's first form: dedupe on `(tenant_id, client_uuid)`.

    **No S2 collection takes this path** -- `customers` is S1's master-data
    table and is pushed under a natural key. S3's `stock_moves` and S4's `sales`
    are the first that will, and the reason it is written here rather than there
    is that rule 8 names S2 the owner of the helper: a stage that arrived to
    find no dedupe would write its own, and then there would be two.
    """
    del options
    if not client_uuid:
        raise Rejected(
            "Esta fila no trae `client_uuid`, así que no hay con qué deduplicarla.",
            code="client_uuid_required",
            field="client_uuid",
        )
    model = collection.model
    existing = model._default_manager.filter(
        tenant_id=device.tenant_id, client_uuid=client_uuid
    ).first()
    if existing is not None:
        return Outcome(client_uuid, DUPLICATE, id=str(existing.id))

    payload = dict(row.get("payload") or {})
    payload.pop("tenant_id", None)
    instance = model(
        tenant_id=device.tenant_id,
        client_uuid=client_uuid,
        device_id=device.id,
        occurred_at=_occurred(row),
        recorded_at=timezone.now(),
        **payload,
    )
    try:
        # **Its own savepoint.** A failed `INSERT` marks the enclosing
        # transaction for rollback, and Django then refuses every statement on
        # it -- so the recovery read below would raise
        # `TransactionManagementError` instead of finding the row that beat us.
        # This is the exact race A5 exists for, and it has to be survivable.
        with transaction.atomic():
            instance.save()
    except IntegrityError:
        existing = model._default_manager.filter(
            tenant_id=device.tenant_id, client_uuid=client_uuid
        ).first()
        if existing is None:
            raise
        return Outcome(client_uuid, DUPLICATE, id=str(existing.id))
    return Outcome(client_uuid, APPLIED, id=str(instance.id))


#: The fields a till may set on a customer it registered at the counter. Stated
#: rather than taken from the payload, because a push is the one write path in
#: the product whose caller is a browser somebody could open a console on.
CUSTOMER_FIELDS = (
    "document_type",
    "document",
    "name",
    "phone",
    "email",
    "address",
    "data_consent",
)


def _write_customer(device, collection, row, client_uuid, options):
    """Rule 8's second form: the declared natural key
    `(tenant_id, document_type, document)`.

    Three successes and one refusal. `applied` created the row at the id the
    till chose; `duplicate` found that id already there; `merged` found a
    different row under the same natural key and returns **its** id, which the
    local row adopts silently -- the cashier registered a person who was already
    known, which is not an event worth telling them about.
    """
    del options
    payload = row.get("payload") or {}
    # **Every value is coerced, because every value is a browser's.** A payload
    # field that arrives as a number, a list or `null` would otherwise reach
    # `.strip()` as an `AttributeError` -- which `_apply_row` does not catch, so
    # it would leave the savepoint as a 500 and take the whole batch with it.
    # `data_consent` is the one field read as a flag rather than as text.
    values: dict[str, str] = {
        name: _text(payload.get(name))
        for name in CUSTOMER_FIELDS
        if name != "data_consent"
    }
    #: Read as a flag rather than as text, and the only field that is.
    consent = bool(payload.get("data_consent"))

    document_type = values["document_type"].strip().upper()
    document = values["document"].strip()
    if not document or not document_type:
        # The natural key **is** the idempotency key here. A customer with no
        # document could not be deduplicated, and two tills registering the
        # same walk-in would produce two rows nothing could ever reconcile.
        # S5's document identifies the acquirer by type and number anyway.
        raise Rejected(
            "Un cliente registrado en el mostrador necesita tipo y número de "
            "documento.",
            code="document_required",
            field="document",
        )
    from core.models import DOCUMENT_TYPES

    if document_type not in DOCUMENT_TYPES:
        raise Rejected(
            f"«{document_type}» no es un tipo de documento reconocido.",
            code="document_type_unknown",
            field="document_type",
        )
    if not (values.get("name") or "").strip():
        raise Rejected(
            "Un cliente registrado en el mostrador necesita un nombre.",
            code="name_required",
            field="name",
        )
    # Checked here rather than left to the column, because a `DataError` is not
    # a `Rejected` and would leave the savepoint as a 500 that takes the whole
    # batch with it. Every bound is its own column's.
    proposed_id = payload.get("id")
    if proposed_id is not None and not _is_uuid(proposed_id):
        raise Rejected(
            "El identificador de esta fila no es válido.",
            code="id_not_a_uuid",
            field="id",
        )
    for name, limit in (
        ("document", 32),
        ("document_type", 8),
        ("name", 200),
        ("phone", 40),
        ("email", 254),
        ("address", 300),
    ):
        given = values.get(name) or ""
        if len(str(given)) > limit:
            raise Rejected(
                f"El campo «{name}» de este cliente excede {limit} caracteres.",
                code="value_too_long",
                field=name,
            )

    existing = Customer.objects.filter(
        tenant_id=device.tenant_id, document_type=document_type, document=document
    ).first()
    if existing is not None:
        # `duplicate` when the till already holds this row under this id -- a
        # replayed push, which is the case A5 exists for and where there is
        # nothing for the client to rewrite. `merged` when the natural key
        # landed on a different row: the local row adopts the server's id
        # silently, because the cashier registered a person who was already
        # known and that is not an event worth telling them about.
        outcome = (
            DUPLICATE
            if proposed_id and str(existing.id) == str(proposed_id)
            else MERGED
        )
        # **The row is touched, and only touched.** Nothing the till typed is
        # written over what the office already has -- a cashier's hurried entry
        # is not better data than the record it merged onto. What moves is
        # `updated_at`, which puts the person inside `customer_recency_months`
        # by definition: they were just seen at a counter. That is what makes
        # the row arrive on the till's next pull, which is how a `merged`
        # outcome ends with the cashier looking at the customer they meant.
        existing.save(update_fields=["updated_at"])
        return Outcome(client_uuid, outcome, id=str(existing.id))

    customer = Customer(tenant_id=device.tenant_id)
    if proposed_id:
        customer.id = proposed_id
    customer.document_type = document_type
    customer.document = document
    customer.name = values["name"].strip()[:200]
    customer.phone = values["phone"].strip()[:40]
    customer.email = values["email"].strip()[:254]
    customer.address = values["address"].strip()[:300]
    customer.data_consent = consent
    if customer.data_consent:
        customer.data_consent_at = timezone.now()
    try:
        # Its own savepoint, for the same reason as above: the losing side of a
        # concurrent insert has to be able to read the winner.
        with transaction.atomic():
            customer.save()
    except IntegrityError:
        # Two tills pushed the same person in the same instant. One of them won
        # the unique index; both converge on that row, and neither cashier is
        # shown an error.
        existing = Customer.objects.filter(
            tenant_id=device.tenant_id,
            document_type=document_type,
            document=document,
        ).first()
        if existing is None:
            raise
        outcome = (
            DUPLICATE
            if proposed_id and str(existing.id) == str(proposed_id)
            else MERGED
        )
        return Outcome(client_uuid, outcome, id=str(existing.id))
    return Outcome(client_uuid, APPLIED, id=str(customer.id))


#: One writer per pushable collection. A collection with no entry takes rule 8's
#: first form, which is what S3's and S4's tables will do.
WRITERS = {"customers": _write_customer}
