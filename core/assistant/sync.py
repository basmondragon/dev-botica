"""What a till writes back, and how each of the two collections arrives.

**The registry amendment itself lives in `core/sync/registry.py`** (ledger
rule 9): one declared artefact naming every collection that reaches a device,
amended rather than shadowed. What lives here is the two push writers, because
both encode an assistant rule and S2 owns neither.

**Both are push-only.** A till writes an offer and an acceptance and never reads
either back -- it already knows what it sent, and a query log is not a snapshot.

**The envelope key identifies the event and the payload's `client_uuid`
identifies the row**, which is S4's second form exactly. A query receives two
events -- the offer, and the `attach` that names the ticket once the sale is on
its way -- and a suggestion receives one, the acceptance. Each is idempotent on
its own envelope key and all of them converge on one row under
`UNIQUE (tenant_id, client_uuid)`.

**The order is the whole of the correctness here.** `client_uuid` is uuid v7 and
the push applies a batch in that order, so the till queues an acceptance
**after** the sale line it credits -- which is what makes *"a batch that applies
the line and not the flag is a batch that under-reports the assistant forever"*
a property of the queue rather than a hope about it.
"""

import uuid

from django.utils import timezone

from core.assistant import service
from core.models import (
    AssistantMode,
    AssistantQuery,
    AssistantSuggestion,
    Sale,
    SaleLine,
)
from core.sync import push as push_service


def _uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _text(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _row_key(payload):
    """The row this event is about, which is **not** the envelope's key."""
    key = _uuid(payload.get("client_uuid"))
    if key is None:
        raise push_service.Rejected(
            "Este evento no dice de qué consulta del asistente habla.",
            code="row_key_missing",
            field="client_uuid",
        )
    return key


def _when(row, payload=None):
    stamped = (payload or {}).get("occurred_at") or row.get("occurred_at")
    if not stamped:
        return timezone.now()
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(str(stamped))
    return parsed or timezone.now()


def _write_query(device, collection, row, client_uuid, options):
    """The offer, and the `attach` that names the ticket it was asked during."""
    del collection, options, client_uuid
    payload = row.get("payload") or {}
    key = _row_key(payload)
    event = _text(payload.get("event")) or "offer"

    held = AssistantQuery.objects.filter(
        tenant_id=device.tenant_id, client_uuid=key
    ).first()

    if event == "attach":
        if held is None:
            raise push_service.Rejected(
                "Esta consulta del asistente no ha llegado todavía.",
                code="query_missing",
                field="client_uuid",
            )
        sale = _sale_for(device, payload)
        service.attach(held, sale=sale)
        return push_service.Outcome(str(key), push_service.APPLIED, id=str(held.id))

    if event == "supersede":
        if held is None:
            raise push_service.Rejected(
                "Esta consulta del asistente no ha llegado todavía.",
                code="query_missing",
                field="client_uuid",
            )
        service.supersede(held, at=_when(row, payload))
        return push_service.Outcome(str(key), push_service.APPLIED, id=str(held.id))

    if held is not None:
        return push_service.Outcome(str(key), push_service.DUPLICATE, id=str(held.id))

    # **A till in `modo local` wrote this row itself**, prose and all: the
    # recommendation it carries was written by the template over `reason_code`
    # and the mode is `local` whatever this instance's switches say, because
    # what happened at that counter is not a thing the server gets to restate.
    query, _rows, _created = service.record(
        tenant_id=device.tenant_id,
        location_id=device.location_id,
        client_uuid=key,
        device=device,
        # **Who asked**, sent by the till exactly as S4's own sale payload sends
        # `sold_by_user_id`. Without it every query written during a blackout
        # would land with an empty `Cajero` column -- which is the one column
        # per-cashier acceptance is unanswerable without, and the offline case
        # is precisely the one this stage exists for.
        user=_user_for(device, payload),
        user_name=_text(payload.get("user_name")),
        sale_id=_optional_sale(device, payload),
        transcript=_text(payload.get("transcript")),
        symptoms=payload.get("symptoms") or [],
        candidates=payload.get("candidates") or [],
        candidate_count=int(payload.get("candidate_count") or 0),
        bundle_version=_text(payload.get("bundle_version")),
        occurred_at=_when(row, payload),
        mode=AssistantMode.LOCAL,
        recommendation=_text(payload.get("recommendation")),
        recommendation_secondary=_text(payload.get("recommendation_secondary")),
        excluded=payload.get("excluded") or [],
    )
    return push_service.Outcome(str(key), push_service.APPLIED, id=str(query.id))


def _user_for(device, payload):
    from core.models import User

    key = _uuid(payload.get("user_id"))
    if key is None:
        return None
    return User.objects.filter(tenant_id=device.tenant_id, id=key).first()


def _sale_for(device, payload):
    sale = _optional_sale(device, payload)
    if sale is None:
        raise push_service.Rejected(
            "El tiquete de esta consulta no ha llegado todavía.",
            code="sale_missing",
            field="sale_client_uuid",
        )
    return sale


def _optional_sale(device, payload):
    key = _uuid(payload.get("sale_client_uuid"))
    if key is None:
        return None
    return Sale.objects.filter(tenant_id=device.tenant_id, client_uuid=key).first()


def _write_suggestion(device, collection, row, client_uuid, options):
    """The acceptance, and the line it credits.

    **A suggestion row is never created here.** It arrives with its query, in
    that event's own payload, so an acceptance for a card the server has never
    seen is a client that pushed out of order -- which is refused rather than
    invented, because inventing it would create an accepted offer with no offer
    behind it and inflate exactly the numerator this stage exists to keep
    honest.
    """
    del collection, options, client_uuid
    payload = row.get("payload") or {}
    key = _row_key(payload)
    suggestion = AssistantSuggestion.objects.filter(
        tenant_id=device.tenant_id, client_uuid=key
    ).first()
    if suggestion is None:
        raise push_service.Rejected(
            "Esta sugerencia no ha llegado todavía.",
            code="suggestion_missing",
            field="client_uuid",
        )
    line_id = _uuid(payload.get("sale_line_id"))
    line = (
        SaleLine.objects.filter(tenant_id=device.tenant_id, id=line_id).first()
        if line_id
        else None
    )
    if line is None:
        raise push_service.Rejected(
            "La línea del tiquete que esta sugerencia acredita no ha llegado.",
            code="sale_line_missing",
            field="sale_line_id",
        )
    try:
        held = service.accept(suggestion, sale_line=line, at=_when(row, payload))
    except service.Refused as refusal:
        raise push_service.Rejected(
            str(refusal), code="already_credited", field="sale_line_id"
        ) from refusal
    return push_service.Outcome(str(key), push_service.APPLIED, id=str(held.id))


def register():
    """Wire this stage's two writers into S2's push endpoint."""
    push_service.register_writer("assistant_queries", _write_query)
    push_service.register_writer("assistant_suggestions", _write_suggestion)
