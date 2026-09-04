"""The sales-history loader (§11.2, *Scope · In* 11).

**Optional by decision.** The forecast runs `parametric` on day one from the
sede's own `stock_policies`, every screen in this stage fills, and a pilot opens
Compras on its first morning without a single imported row. What an export
changes is *how fast* an item reaches `learned`: with eighteen months loaded most
references are `learned` immediately and the seasonal multiplier exists at once;
with nothing loaded, fast movers promote in four or five weeks. That is a quality
and timing variable -- free accuracy, worth chasing early, never worth blocking a
pilot for.

**The rows it writes are quarantined by construction, not by discipline.** An
imported sale was issued by another system years ago: it has no shift, no device,
no payment, no `stock_moves` row and no fiscal document, and the `CHECK` this
stage migrated onto `sales` refuses the first two at the database. What this
module adds on top is the rest of the shape: `status = closed`, `source =
imported`, a number under its own prefix, and a deterministic `client_uuid` so
that re-running the same export file is a no-op rather than a duplicated history.

**It maps rather than guesses** (§11.6). A legacy location code that no
`locations` row answers to, and a legacy item code that no `items` row answers
to, are rows the loader **refuses and reports** -- a placeholder sede or a
placeholder product invented to make a file load is debris somebody finds six
months later on a screen that matters.
"""

import csv
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    Device,
    ImportRun,
    ImportStatus,
    Item,
    Location,
    Sale,
    SaleLine,
    SaleSource,
    SaleStatus,
    VatClass,
)

#: The `imports.kind` this loader records its runs under, alongside S1's
#: master-data runs. Text rather than an enum value, which is why S1 made that
#: column text: a stage adds a kind without migrating a type it does not own.
KIND = "sales_history"

#: The one file, and its header. A throwaway translation script turns whatever
#: the fifteen-year-old system exports into this; the loader never guesses at an
#: export's own shape, so a wrong guess costs that script and never this.
FILE = "sales_history.csv"
HEADER = (
    "external_id",
    "location",
    "occurred_at",
    "number",
    "item",
    "quantity",
    "unit_price",
    "discount",
    "unit_cost",
    "vat_class",
)

#: **The prefix that makes an imported number uncollidable.** S4 composes a
#: counter number as `{device code}-{sequence}` over `[A-Z0-9]{1,16}`, so a
#: number under this prefix can only collide with a device actually coded
#: `HIST` -- which the run refuses outright rather than discovering at a counter
#: with a customer waiting.
NUMBER_PREFIX = "HIST-"

#: The namespace every imported row's `client_uuid` is derived from. **Stable
#: across runs**, which is the whole gain: re-running the same export file
#: collides with `UNIQUE (tenant_id, client_uuid)` and writes nothing, instead of
#: doubling a tenant's history. `occurred_at` is the legacy sale's own timestamp;
#: `recorded_at` is the moment of import.
NAMESPACE = uuid.UUID("3f7c1d92-6b40-5a8e-9d17-4c25e0b98af3")


class RowRefused(Exception):
    """One row could not be placed. The run continues; the row is reported."""


class BadHeader(Exception):
    """A file whose header the contract does not admit."""


class DryRun(Exception):
    """Roll the work back, keep the report."""


@dataclass
class Report:
    rows_read: int = 0
    sales_created: int = 0
    lines_created: int = 0
    rows_failed: int = 0
    duplicates: int = 0
    errors: list = field(default_factory=list)

    def failed(self, line, value, reason):
        self.rows_failed += 1
        self.errors.append(
            {"file": FILE, "line": line, "value": value, "reason": str(reason)}
        )

    def summary(self):
        return (
            f"leídas {self.rows_read} · ventas {self.sales_created} · "
            f"líneas {self.lines_created} · repetidas {self.duplicates} · "
            f"fallidas {self.rows_failed}"
        )


def key_uuid(tenant_id, source, *parts) -> uuid.UUID:
    return uuid.uuid5(
        NAMESPACE, f"{tenant_id}:{source}:{':'.join(str(one) for one in parts)}"
    )


class Loader:
    """One run, over one file, against one pinned tenant."""

    def __init__(self, *, tenant_id, path, apply, source="", locations=None):
        self.tenant_id = tenant_id
        self.path = Path(path)
        self.apply = apply
        #: What the `client_uuid` derivation is keyed on besides the tenant and
        #: the legacy id. It defaults to the file's own name, so two different
        #: exports of two different periods do not have to coordinate their ids
        #: while one export re-run twice still collides with itself.
        self.source = source or self.path.name
        #: Legacy location code -> `locations.code`, from `--location`. Absent
        #: means the export already speaks the network's own codes.
        self.mapping = dict(locations or {})
        self.report = Report()

    # -- the run ----------------------------------------------------------

    def run(self):
        started = timezone.now()
        try:
            with transaction.atomic():
                self._guard_number_prefix()
                self._read()
                if not self.apply:
                    raise DryRun
        except DryRun:
            pass
        except (BadHeader, OSError, UnicodeDecodeError, csv.Error) as bad:
            self.report.failed(1, "", bad)

        return ImportRun.objects.create(
            tenant_id=self.tenant_id,
            kind=KIND,
            source=str(self.path),
            status=(
                ImportStatus.FAILED
                if self.report.rows_failed
                else ImportStatus.COMPLETED
            ),
            dry_run=not self.apply,
            started_at=started,
            finished_at=timezone.now(),
            rows_read=self.report.rows_read,
            rows_created=self.report.sales_created + self.report.lines_created,
            rows_updated=0,
            rows_failed=self.report.rows_failed,
            errors=self.report.errors,
        )

    def _guard_number_prefix(self):
        """Refuse the whole run where the prefix could collide with a till.

        A device coded `HIST` would let a counter sale reach a number this
        import has taken, and `UNIQUE (tenant_id, location_id, number)` would
        then fail at a counter with a customer waiting -- weeks later, on a
        number nobody would connect to an import.
        """
        clash = Device.objects.filter(
            tenant_id=self.tenant_id, code=NUMBER_PREFIX.rstrip("-")
        ).first()
        if clash is not None:
            raise BadHeader(
                f"Hay un equipo con el código «{clash.code}», que es el prefijo "
                "que usan los números importados. Renombre el equipo antes de "
                "cargar el histórico."
            )

    def _read(self):
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = [
                column for column in HEADER if column not in (reader.fieldnames or [])
            ]
            if missing:
                raise BadHeader(
                    f"{FILE} necesita las columnas: {', '.join(HEADER)}. "
                    f"Faltan: {', '.join(missing)}."
                )
            self._load(reader)

    def _load(self, reader):
        locations = {
            one.code: one for one in Location.objects.filter(tenant_id=self.tenant_id)
        }
        items = {
            one.external_code: one
            for one in Item.objects.filter(tenant_id=self.tenant_id).exclude(
                external_code=""
            )
        }
        by_name = {
            one.name.strip().lower(): one
            for one in Item.objects.filter(tenant_id=self.tenant_id)
        }
        held = set(
            Sale.objects.filter(
                tenant_id=self.tenant_id, source=SaleSource.IMPORTED
            ).values_list("client_uuid", flat=True)
        )

        # **A sale is written with its lines or not at all** (*Data*): a `sales`
        # row with no `sale_lines` teaches a per-item forecast nothing, so the
        # file is grouped by document before anything is written.
        documents: dict = {}
        for number, row in enumerate(reader, start=2):
            self.report.rows_read += 1
            try:
                parsed = self._parse(row, locations, items, by_name)
            except RowRefused as refusal:
                self.report.failed(number, row.get("external_id", ""), refusal)
                continue
            parsed["line_number"] = number
            documents.setdefault(parsed["external_id"], []).append(parsed)

        for external_id, lines in documents.items():
            client_uuid = key_uuid(self.tenant_id, self.source, external_id)
            if client_uuid in held:
                self.report.duplicates += 1
                continue
            try:
                # **Its own savepoint.** A legacy system that reused a document
                # number at one sede -- or an export whose numbers collide with
                # a previous import's -- refuses that document and nothing
                # else. A run that failed all nine thousand rows because the
                # ninth was broken is as wrong as one that loads eight thousand
                # and exits zero, and both are quiet.
                with transaction.atomic():
                    self._write(external_id, client_uuid, lines)
            except IntegrityError as clash:
                self.report.failed(
                    lines[0].get("line_number", 0),
                    external_id,
                    "la base de datos rechazó este documento: "
                    f"{_reason(clash)}. Revise que el número no esté repetido "
                    "en el archivo ni cargado ya bajo otro origen.",
                )

    def _parse(self, row, locations, items, by_name):
        external_id = (row.get("external_id") or "").strip()
        if not external_id:
            raise RowRefused(
                "la fila no trae «external_id», que es lo único que hace "
                "idempotente una recarga del mismo archivo"
            )
        code = (row.get("location") or "").strip()
        code = self.mapping.get(code, code)
        location = locations.get(code)
        if location is None:
            raise RowRefused(
                f"«{code}» no corresponde a ninguna sede de esta droguería. "
                "Use --location CODIGO_LEGADO=CODIGO_BOTICA para mapearla."
            )
        reference = (row.get("item") or "").strip()
        item = items.get(reference) or by_name.get(reference.lower())
        if item is None:
            raise RowRefused(
                f"«{reference}» no corresponde a ningún producto del catálogo. "
                "Cargue primero el catálogo con load_catalog."
            )
        quantity = _whole(row.get("quantity"), "quantity")
        if quantity <= 0:
            raise RowRefused("una línea de venta con cantidad cero o negativa")
        vat_class = (row.get("vat_class") or "").strip() or item.vat_class
        if vat_class not in VatClass.values:
            raise RowRefused(f"«{vat_class}» no es una clase de IVA")
        return {
            "external_id": external_id,
            "location": location,
            "occurred_at": _moment(row.get("occurred_at")),
            "number": (row.get("number") or "").strip() or external_id,
            "item": item,
            "quantity": quantity,
            "unit_price": _money(row.get("unit_price"), "unit_price"),
            "discount": _money(row.get("discount"), "discount", default=Decimal("0")),
            "unit_cost": _optional_money(row.get("unit_cost")),
            "vat_class": vat_class,
        }

    def _write(self, external_id, client_uuid, lines):
        first = lines[0]
        subtotal = sum(line["unit_price"] * Decimal(line["quantity"]) for line in lines)
        discount = sum(line["discount"] for line in lines)
        total = subtotal - discount
        tax = sum(_tax(line) for line in lines)
        sale = Sale.objects.create(
            tenant_id=self.tenant_id,
            location=first["location"],
            # **Null, and the database refuses anything else.** An imported sale
            # belongs to no turno and to no device: it was rung up in another
            # system before Botica existed.
            shift=None,
            device=None,
            number=f"{NUMBER_PREFIX}{first['number']}"[:32],
            status=SaleStatus.CLOSED,
            source=SaleSource.IMPORTED,
            subtotal=subtotal,
            discount=discount,
            tax=min(tax, total),
            total=total,
            occurred_at=first["occurred_at"],
            recorded_at=timezone.now(),
            closed_at=first["occurred_at"],
            client_uuid=client_uuid,
        )
        self.report.sales_created += 1
        SaleLine.objects.bulk_create(
            [
                SaleLine(
                    tenant_id=self.tenant_id,
                    sale=sale,
                    location=line["location"],
                    position=position,
                    item=line["item"],
                    lot=None,
                    quantity=line["quantity"],
                    unit_price=line["unit_price"],
                    discount=line["discount"],
                    vat_class=line["vat_class"],
                    tax_amount=_tax(line),
                    unit_cost=line["unit_cost"],
                    occurred_at=line["occurred_at"],
                    recorded_at=sale.recorded_at,
                    client_uuid=key_uuid(
                        self.tenant_id, self.source, external_id, position
                    ),
                )
                for position, line in enumerate(lines, start=1)
            ],
            batch_size=500,
        )
        self.report.lines_created += len(lines)


def _reason(error) -> str:
    """The first line of a database refusal, which is the constraint's name and
    nothing a person should not see."""
    return str(error).strip().splitlines()[0][:200]


def _tax(line) -> Decimal:
    """The IVA **contained** in the line's net amount, never added to it."""
    from core.models import VAT_RATES

    rate = VAT_RATES.get(line["vat_class"], Decimal("0"))
    net = line["unit_price"] * Decimal(line["quantity"]) - line["discount"]
    if rate == 0:
        return Decimal("0")
    return (net - net / (1 + rate / Decimal(100))).quantize(Decimal("0.01"))


def _whole(value, column) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError as error:
        raise RowRefused(f"«{value}» no es un entero en «{column}»") from error


def _money(value, column, default=None) -> Decimal:
    raw = str(value or "").strip()
    if not raw:
        if default is not None:
            return default
        raise RowRefused(f"«{column}» es obligatorio")
    try:
        return Decimal(raw.replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as error:
        raise RowRefused(f"«{value}» no es un importe en «{column}»") from error


def _optional_money(value) -> Decimal | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return _money(raw, "unit_cost")


def _moment(value) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise RowRefused("«occurred_at» es obligatorio")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise RowRefused(
            f"«{raw}» no es una fecha ISO (2024-03-18 o 2024-03-18T14:22:00)"
        ) from error
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed
