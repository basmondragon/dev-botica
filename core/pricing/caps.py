"""The regulated maximum price: the guardrail, and the one column S7 writes on
another stage's table.

**A null cap means *unknown*, never *uncapped*** (§11.4, A11). Colombian
medicines under CNPMDM control carry a legal ceiling, and whether a maintained
source of those ceilings exists is §11.4 and unanswered. This stage takes the
only safe reading: by default no reference whose cap status is unknown is ever
proposed upward by **either** engine. It may be proposed downward, and it may be
held. An owner may lift that default for the tenant, deliberately, with the act
recorded.

**The cap lives on the item, not on a price row.** It is a regulatory property
of the product, and holding it on `item_prices` would oblige every new price row
to carry the previous one's cap forward -- a guardrail that fails silently on
exactly the reference somebody just repriced. A cap is a constraint, not a
price: it opens no price window and closes none, and setting one writes no
`item_prices` row at all.

*If §11.4 comes back "no maintained source exists"*, every item stays `unknown`,
the default stays off, and this stage reduces to a downward-and-diagnostics
tool. That is the intended failure mode and it is not a defect. The escape that
makes the stage useful anyway is this module's manual path: a regente enters
caps for the few dozen references they actually sell under control, or loads
them as a CSV, and those references become priceable immediately.
"""

import csv
import io
import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from core import audit
from core.models import (
    AuditAction,
    CapStatus,
    Item,
    ItemPrice,
    PriceProposal,
    PriceProposalStatus,
)

logger = logging.getLogger(__name__)

#: The most rows one synchronous load accepts. Above it the request is refused
#: rather than held open: a cap file is a few dozen references a regente typed,
#: and a five-thousand-row upload is a file somebody meant to send somewhere
#: else.
MAX_IMPORT_ROWS = 5_000

#: The column names the loader accepts, in Spanish and in English, because the
#: file is assembled by a person in a spreadsheet and `precio_maximo` and
#: `max_price` are both what they will type.
COLUMNS = {
    "item": ("codigo", "código", "external_code", "item", "referencia"),
    "price": ("precio_maximo", "precio máximo", "max_price", "tope", "precio"),
    "source": ("fuente", "source", "circular", "referencia_fuente"),
    "effective": ("vigencia", "fecha", "effective_date", "desde"),
}


class CapRefused(ValueError):
    """A cap the loader or the editor will not take, named for the field."""


def set_cap(*, item, actor, tenant_id, price, status, source="", request_id=""):
    """Set or clear one item's cap. **Writes no price and touches no
    `item_prices` row** (A11).

    Clearing a cap returns the item to `unknown` -- not to `not_regulated`,
    which is a claim somebody made rather than the absence of one.
    """
    before = _snapshot(item)
    if status not in CapStatus.values:
        raise CapRefused(f"«{status}» no es un estado de tope regulado.")
    if status == CapStatus.CAPPED:
        if price is None:
            raise CapRefused("Un tope regulado necesita un precio máximo.")
        price = Decimal(price).quantize(Decimal("0.01"))
        if price <= 0:
            raise CapRefused("El precio máximo tiene que ser mayor que cero.")
    else:
        # A reference stated to be outside price control, or one nobody has
        # ruled on, holds no ceiling -- and leaving a stale number behind would
        # bind an engine against a cap the tenant has just said does not apply.
        price = None

    item.regulated_max_price = price
    item.cap_status = status
    document = dict(item.custom or {})
    pricing = dict(document.get("pricing") or {})
    pricing["cap_status"] = status
    if source:
        pricing["cap_source"] = source[:200]
    elif status != CapStatus.CAPPED:
        pricing.pop("cap_source", None)
    document["pricing"] = pricing
    item.custom = document
    item.save(
        update_fields=["regulated_max_price", "cap_status", "custom", "updated_at"]
    )

    audit.record(
        actor=actor,
        tenant_id=tenant_id,
        action=AuditAction.UPDATE,
        entity_type="items.regulated_max_price",
        entity_id=item.id,
        before=before,
        after=_snapshot(item),
        request_id=request_id,
    )
    return item


def _snapshot(item) -> dict:
    return {
        "regulated_max_price": (
            str(item.regulated_max_price)
            if item.regulated_max_price is not None
            else None
        ),
        "cap_status": item.cap_status or CapStatus.UNKNOWN,
    }


def load_csv(*, tenant_id, actor, payload: str, request_id=""):
    """Bulk-load caps from a file a person assembled. Synchronous, and refused
    above `MAX_IMPORT_ROWS`.

    **It writes no `imports` row.** The ledger gives `imports` to S1 and S6
    only, so a cap load records one `audit_log` row for the whole file and the
    caps themselves. If a cap load should appear in the imports history beside
    the catalog and sales loads, the ledger has to grant S7 that write; this
    stage does not assume it.
    """
    reader = csv.DictReader(io.StringIO(payload))
    if reader.fieldnames is None:
        raise CapRefused("El archivo no tiene encabezados.")
    heads = {_normalise(name): name for name in reader.fieldnames}
    columns = {}
    for field, names in COLUMNS.items():
        match = next(
            (heads[_normalise(one)] for one in names if _normalise(one) in heads), None
        )
        if match is None and field in ("item", "price"):
            raise CapRefused(
                "El archivo necesita una columna de código de referencia y otra "
                "de precio máximo."
            )
        columns[field] = match

    rows = list(reader)
    if len(rows) > MAX_IMPORT_ROWS:
        raise CapRefused(
            f"El archivo trae {len(rows)} filas. El máximo de una carga es "
            f"{MAX_IMPORT_ROWS}."
        )

    codes = [str(row.get(columns["item"]) or "").strip() for row in rows]
    by_code = {
        code: item
        for code, item in (
            (one.external_code, one)
            for one in Item.objects.filter(
                tenant_id=tenant_id, external_code__in=[c for c in codes if c]
            )
        )
    }
    loaded_rows = 0
    unmatched: list[dict] = []
    refused: list[dict] = []
    with transaction.atomic():
        for number, row in enumerate(rows, start=2):
            code = str(row.get(columns["item"]) or "").strip()
            item = by_code.get(code)
            if item is None:
                unmatched.append({"line": number, "code": code})
                continue
            try:
                price = _money(row.get(columns["price"]))
            except CapRefused as refusal:
                refused.append({"line": number, "detail": str(refusal)})
                continue
            source = (
                str(row.get(columns["source"]) or "").strip()
                if columns["source"]
                else ""
            )
            effective = (
                str(row.get(columns["effective"]) or "").strip()
                if columns["effective"]
                else ""
            )
            set_cap(
                item=item,
                actor=actor,
                tenant_id=tenant_id,
                price=price,
                status=CapStatus.CAPPED
                if price is not None
                else CapStatus.NOT_REGULATED,
                source=" · ".join(one for one in (source, effective) if one),
                request_id=request_id,
            )
            loaded_rows += 1
        audit.record(
            actor=actor,
            tenant_id=tenant_id,
            action=AuditAction.CREATE,
            entity_type="pricing.cap_import",
            before=None,
            after={
                "rows": len(rows),
                "loaded": loaded_rows,
                "unmatched": len(unmatched),
                "refused": len(refused),
            },
            request_id=request_id,
        )
    return {"loaded": loaded_rows, "unmatched": unmatched, "refused": refused}


def _normalise(name: str) -> str:
    return (
        str(name)
        .strip()
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace(" ", "_")
    )


def _money(raw):
    """A Colombian figure from a spreadsheet: `18.900`, `18900`, `18900,00`."""
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace("$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") >= 1 and len(text.rsplit(".", 1)[1]) == 3:
        text = text.replace(".", "")
    try:
        value = Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as error:
        raise CapRefused(f"«{raw}» no es un precio.") from error
    if value <= 0:
        raise CapRefused("El precio máximo tiene que ser mayor que cero.")
    return value


# ---------------------------------------------------------------------------
# The daily check
# ---------------------------------------------------------------------------


def check(tenant_id, *, today=None) -> dict:
    """Raise `above_cap` for any item now charging above a loaded cap.

    **This exists because a cap can move without us**: a CNPMDM circular lowers
    a ceiling and a price that was legal on Tuesday is not on Wednesday. Tying
    that check to a weekly model run would leave a pharmacy up to six days over
    the line -- so it runs daily, on its own schedule, and survives the model
    being switched off entirely. It is the one thing in this stage that reports
    a problem rather than proposing an improvement.

    It writes no price and reads no forecast: it compares the in-force price
    against the cap and moves a proposal's status, which is the whole of it.
    """
    today = today or timezone.localdate()
    from core.pricing.engine import current_prices, cost_bases, margin_pct

    prices = current_prices(tenant_id, today)
    costs = cost_bases(tenant_id)
    capped = list(
        Item.objects.filter(
            tenant_id=tenant_id, regulated_max_price__isnull=False
        ).only("id", "vat_class", "regulated_max_price")
    )
    over = [
        item
        for item in capped
        if prices.get(item.id) is not None
        and prices[item.id] > item.regulated_max_price
    ]
    raised = 0
    cleared = 0
    now = timezone.now()
    with transaction.atomic():
        over_ids = {item.id for item in over}
        # A reference that has come back under its cap since the last check is
        # no longer a compliance finding, and leaving the badge up would teach
        # an owner that the tile lies.
        cleared = (
            PriceProposal.objects.filter(
                tenant_id=tenant_id, status=PriceProposalStatus.ABOVE_CAP
            )
            .exclude(item_id__in=over_ids)
            .update(status=PriceProposalStatus.SUPERSEDED, updated_at=now)
        )
        for item in over:
            cap = item.regulated_max_price
            if cap is None:  # narrowing; the queryset already excluded them
                continue
            live = (
                PriceProposal.objects.filter(
                    tenant_id=tenant_id,
                    item_id=item.id,
                    status__in=(
                        PriceProposalStatus.PROPOSED,
                        PriceProposalStatus.ABOVE_CAP,
                    ),
                )
                .order_by("-computed_at")
                .first()
            )
            price = prices[item.id]
            cost, source = costs.get(item.id, (None, None))
            if live is not None and live.status == PriceProposalStatus.ABOVE_CAP:
                continue
            if live is not None:
                live.status = PriceProposalStatus.SUPERSEDED
                live.save(update_fields=["status", "updated_at"])
            if cost is None:
                # Without a cost there is no margin to state, and the finding is
                # still worth making -- so the cost basis is the price itself
                # and the margin reads zero rather than the row being dropped.
                cost, source = price, "supplier"
            PriceProposal.objects.create(
                tenant_id=tenant_id,
                item_id=item.id,
                basis="margin_rule",
                status=PriceProposalStatus.ABOVE_CAP,
                current_price=price,
                suggested_price=cap,
                current_margin=margin_pct(price, cost, item.vat_class),
                projected_margin=margin_pct(cap, cost, item.vat_class),
                estimated_monthly_impact=None,
                trailing_monthly_units=None,
                respects_regulated_cap=False,
                confidence="low",
                cost_basis=cost,
                cost_source=source,
                step_pct=((cap - price) / price * Decimal("100")).quantize(
                    Decimal("0.01")
                ),
                margin_gap_pp=None,
                reason_code="above_regulated_cap",
                regulated_max_price_at_proposal=cap,
                computed_at=now,
                model_version="cap-check-v1",
            )
            raised += 1
    report = {
        "checked": len(capped),
        "above_cap": len(over),
        "raised": raised,
        "cleared": cleared,
    }
    logger.info("cap check for %s: %s", tenant_id, report)
    return report


def loaded(tenant_id):
    """Every item carrying a cap, with its source reference and its date, for
    the settings section's own list."""
    return (
        Item.objects.filter(tenant_id=tenant_id, regulated_max_price__isnull=False)
        .only(
            "id",
            "name",
            "presentation",
            "regulated_max_price",
            "custom",
            "cap_status",
            "updated_at",
        )
        .order_by("name")
    )


def price_is_above_cap(item) -> bool:
    """Whether this item's in-force network price sits above its loaded cap.

    Used by the editor and by the grid; the daily job uses the bulk read above,
    because asking this question four thousand times is four thousand queries.
    """
    if item.regulated_max_price is None:
        return False
    row = (
        ItemPrice.objects.filter(item=item, location__isnull=True)
        .order_by("-effective_from", "-id")
        .first()
    )
    return row is not None and row.price > item.regulated_max_price
