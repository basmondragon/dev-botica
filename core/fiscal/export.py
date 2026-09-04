"""The file export. **A client whose system has no API gets the same payload on
a schedule.**

A transport, not a second design: same canonical document, same `document_key`,
same mapping, same validation, same states. What changes is that a period's
documents land in one file instead of one request each, and that
**`acknowledged` here means the file exists** -- not that anyone imported it.
The work list labels the target so nobody reads more into the badge than that.

**Exactly-once survives the transport.** A document already written into a
period's file is never written into another, because the period a document
belongs to is derived from its own `created_at` and never from what is still
outstanding. And re-running an export overwrites the same file with the same
content rather than appending, so a re-run is a no-op at the far end.

**A partial file is never published.** The whole period is rendered in memory,
the manifest and the file are written together, and only then are the documents
moved to `acknowledged`. A storage failure leaves every document exactly where
it was and the next run does it again.
"""

import csv
import io
import json
import logging

from django.utils import timezone

from core.fiscal import (
    document as canonical,
    mappings,
    service,
    settings as invoicing,
    storage,
    targets,
)
from core.models import FiscalDocument, FiscalDocumentStatus, Tenant

logger = logging.getLogger(__name__)


def period_of(row) -> str:
    """The day a document belongs to, in the pharmacy's own calendar.

    `created_at` and not `occurred_at`: a document written today from a sale a
    till rang three days ago while it was offline belongs to today's file, or a
    period already exported would have to be reopened -- which is precisely the
    "a document in an earlier period's file never appears in a later one" rule
    read backwards.
    """
    return timezone.localtime(row.created_at).date().isoformat()


def file_name(options: dict, slug: str, period: str) -> str:
    delivery = options.get("delivery") or {}
    prefix = str(delivery.get("prefix") or "fiscal-exports").strip("/")
    suffix = "csv" if delivery.get("format") == "csv" else "json"
    return f"{prefix}/{slug}/{period}.{suffix}"


def manifest_name(options: dict, slug: str, period: str) -> str:
    """The keys a period's file holds, beside the file itself.

    It is what `query` reads, so exactly-once is answerable without parsing a
    CSV -- and it is what makes "no key from an earlier period appears in a
    later one" a set comparison rather than a text search.
    """
    delivery = options.get("delivery") or {}
    prefix = str(delivery.get("prefix") or "fiscal-exports").strip("/")
    return f"{prefix}/{slug}/{period}.keys.json"


def holds(context, document_key: str) -> bool:
    """Whether any period's file already carries this key."""
    from core.models import Tenant as TenantModel

    tenant = TenantModel.objects.filter(id=context.tenant_id).first()
    if tenant is None:
        return False
    for _period, keys in _manifests(context.options, tenant.slug):
        if document_key in keys:
            return True
    return False


def _manifests(options: dict, slug: str):
    delivery = options.get("delivery") or {}
    prefix = str(delivery.get("prefix") or "fiscal-exports").strip("/")
    for name in storage.names(f"{prefix}/{slug}"):
        if not name.endswith(".keys.json"):
            continue
        raw = storage.get(f"{prefix}/{slug}/{name}")
        if not raw:
            continue
        try:
            held = json.loads(raw)
        except ValueError:
            continue
        yield held.get("period", name.split(".")[0]), set(held.get("keys") or [])


def run(tenant, period: str) -> dict:
    """Render one period into one file and acknowledge what it holds.

    `(tenant_id, period)` is the idempotency key: a re-run includes exactly the
    same documents and produces the same bytes.
    """
    options = invoicing.read(tenant)
    spec, target = targets.open_target(tenant, options)
    if spec is None or not spec.batched:
        return {"period": period, "written": 0, "skipped": "not_a_file_target"}
    del target

    rows = [
        row
        for row in FiscalDocument.objects.select_related(
            "sale", "sale__location", "sale_return", "sale_return__sale"
        )
        .filter(tenant_id=tenant.id, target=spec.id)
        .order_by("created_at", "document_key")
        if period_of(row) == period
    ]
    if not rows:
        return {"period": period, "written": 0}

    mapping = mappings.get((options.get("mapping") or "") or spec.default_mapping)
    rendered: list[tuple] = []
    for row in rows:
        try:
            rendered.append((row, service.render(row, tenant=tenant)))
        except canonical.Incomplete as refusal:
            # It stays out of the file and lands on the work list, exactly as it
            # would with an API target. A file with a hole in it is worse than a
            # file with one fewer document and a reason on a screen.
            from core.fiscal import delivery

            delivery.fail(row, str(refusal))

    if not rendered:
        return {"period": period, "written": 0}

    body = _render_file(mapping, [payload for _row, payload in rendered])
    keys = [payload["document"]["document_key"] for _row, payload in rendered]
    storage.put(file_name(options, tenant.slug, period), body)
    storage.put(
        manifest_name(options, tenant.slug, period),
        json.dumps({"period": period, "keys": keys, "count": len(keys)}).encode(
            "utf-8"
        ),
    )

    at = timezone.now()
    for row, payload in rendered:
        row.payload = payload
        row.status = FiscalDocumentStatus.ACKNOWLEDGED
        row.acknowledged_at = at
        row.next_attempt_at = None
        row.error = ""
        row.attempts += 1
        row.save(
            update_fields=[
                "payload",
                "status",
                "acknowledged_at",
                "next_attempt_at",
                "error",
                "attempts",
                "updated_at",
            ]
        )
    return {"period": period, "written": len(keys)}


def _render_file(mapping, payloads) -> bytes:
    """CSV at line grain where the mapping declares columns, JSON otherwise.

    Deterministic in both forms -- sorted keys, a fixed column order, `\\n` line
    endings -- because "a re-run overwrites the file with identical content" has
    to be checkable by comparing bytes rather than by parsing.
    """
    if mapping.columns:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(list(mapping.columns))
        for payload in payloads:
            for row in mappings.csv_rows(mapping, payload):
                writer.writerow(row)
        return buffer.getvalue().encode("utf-8")
    rendered = [mappings.render(mapping, payload) for payload in payloads]
    return json.dumps(rendered, ensure_ascii=False, sort_keys=True, indent=2).encode(
        "utf-8"
    )


def listing(tenant) -> list[dict]:
    """The generated files, with their period, their count and a link.

    Derived from the manifests rather than from a table: the ledger grants this
    stage one table, and a period's file is a fact about object storage that the
    manifest beside it already records.
    """
    options = invoicing.read(tenant)
    rows = []
    for period, keys in _manifests(options, tenant.slug):
        name = file_name(options, tenant.slug, period)
        rows.append(
            {
                "period": period,
                "document_count": len(keys),
                "file": name,
                "url": storage.link(name),
            }
        )
    return sorted(rows, key=lambda row: row["period"], reverse=True)


def due_periods(tenant) -> list[str]:
    """Which periods hold a document that is not in a file yet.

    Every period with unsettled work, not merely today's: a run that was missed
    while storage was unreachable has to be picked up by the next one, and a job
    that only ever looked at today would leave yesterday's documents pending for
    ever.
    """
    options = invoicing.read(tenant)
    spec, _target = targets.open_target(tenant, options)
    if spec is None or not spec.batched:
        return []
    periods = {
        period_of(row)
        for row in FiscalDocument.objects.filter(
            tenant_id=tenant.id,
            target=spec.id,
            status__in=(FiscalDocumentStatus.PENDING, FiscalDocumentStatus.SENT),
        ).only("created_at")
    }
    return sorted(periods)


def slug_of(tenant_id) -> str:
    tenant = Tenant.objects.filter(id=tenant_id).first()
    return tenant.slug if tenant else ""
