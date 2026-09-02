"""The internal load tool's engine (§11.2).

Master data only -- manufacturers, categories, suppliers, items, barcodes,
supplier links, prices, customers. Opening stock and lots are S3's; sales
history is S6's. It is run by us during onboarding, from a management command
that takes an **explicit tenant argument and pins it before doing any work**
(ledger rule 6): a loader that inferred a tenant from a file would be a loader
that could write a network's catalog into another network's, and under FORCE ROW
LEVEL SECURITY an unpinned run would silently write nothing at all, which is the
worse failure of the two.

**Input is one CSV per entity, with a fixed header, applied in a fixed order.**
The order is the reference order: an item names a laboratorio, a barcode names
an item, a supplier link names both. A run may supply a subset; a file whose
references are not yet present **fails its rows rather than creating a
placeholder**, because a placeholder laboratorio called `GENFAR ` with a
trailing space is precisely the debris a catalog cleanup exists to remove.

Three things this tool will not do, each because the alternative is a silent
data defect rather than a loud run failure:

* It never guesses a `vat_class`. A row without one fails, unless an operator
  passes `--vat-class-when-missing`, and the run records how many rows took it
  -- which turns a guess into a recorded operator decision.
* It never deletes and never deactivates. A product missing from a new export is
  a product the export forgot, not a product the network stopped selling.
* It never writes stock, a lot or a sale.
"""

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from core.catalog import prices
from core.models import (
    DOCUMENT_TYPES,
    Category,
    Customer,
    ImportKind,
    ImportRun,
    ImportStatus,
    InvimaStatus,
    Item,
    ItemBarcode,
    ItemType,
    Location,
    Manufacturer,
    Supplier,
    SupplierItem,
    VatClass,
)

#: The reference order, and the file each entity is read from. A run may supply
#: a subset; what it cannot do is supply a child before its parent exists.
ENTITIES = (
    "manufacturers",
    "categories",
    "suppliers",
    "items",
    "item_barcodes",
    "supplier_items",
    "item_prices",
    "customers",
)

#: The documented CSV contract. A throwaway translation script turns whatever
#: the fifteen-year-old system exports into these; the loader never guesses at
#: an export's own shape, so a wrong guess costs that script and never this.
HEADERS = {
    "manufacturers": ("name", "nit"),
    "categories": ("name", "parent"),
    "suppliers": ("nit", "name", "contact", "payment_terms", "lead_time_days"),
    "items": (
        "external_code",
        "type",
        "name",
        "presentation",
        "manufacturer",
        "category",
        "description",
        "active_ingredient",
        "strength",
        "invima_registration",
        "invima_expires_at",
        "invima_status",
        "requires_prescription",
        "controlled",
        "cold_chain",
        "unit",
        "splittable",
        "units_per_pack",
        "vat_class",
        "tracks_stock",
        "tracks_lots",
        "tracks_expiry",
        "active",
        "service_cost",
        "barcode",
    ),
    "item_barcodes": ("item", "code", "is_primary"),
    "supplier_items": (
        "supplier",
        "item",
        "supplier_code",
        "cost",
        "min_order_pack",
        "is_preferred",
    ),
    "item_prices": (
        "item",
        "location",
        "price",
        "effective_from",
        "effective_to",
        "per_pack",
    ),
    "customers": (
        "document_type",
        "document",
        "name",
        "phone",
        "email",
        "address",
        "data_consent",
        "notes",
    ),
}

#: What a file cannot be read without. Everything else in `HEADERS` is optional
#: and defaults, which is what lets a partial export load (§11.2).
REQUIRED = {
    "manufacturers": ("name",),
    "categories": ("name",),
    "suppliers": ("name",),
    "items": ("name", "unit"),
    "item_barcodes": ("item", "code"),
    "supplier_items": ("supplier", "item"),
    "item_prices": ("item", "price"),
    "customers": (),
}


#: The eleven columns the service table leaves without meaning. The loader
#: refuses a service row that fills any of them, so the meaninglessness never
#: becomes stale data somebody later trusts -- the same rule the editor keeps by
#: not rendering them.
SERVICE_HAS_NO_TEXT = (
    "manufacturer",
    "presentation",
    "active_ingredient",
    "strength",
    "invima_registration",
    "invima_expires_at",
)
SERVICE_HAS_NO_FLAG = (
    "requires_prescription",
    "controlled",
    "cold_chain",
    "splittable",
    "tracks_stock",
    "tracks_lots",
    "tracks_expiry",
)


def _meaningless(column):
    return (
        f"un servicio no lleva «{column}»: la columna no tiene sentido para una "
        "fila que no mueve existencias"
    )


class RowRefused(Exception):
    """One row could not be placed. The run continues; the row is reported.

    A run that fails all ten rows because one was broken is as wrong as a run
    that loads nine and exits zero, and both are quiet.
    """


class BadHeader(Exception):
    """A file whose header the contract does not admit. The file is skipped and
    reported; the run continues."""


class DryRun(Exception):
    """Roll the work back, keep the report."""


@dataclass
class Report:
    rows_read: int = 0
    rows_created: int = 0
    rows_updated: int = 0
    rows_failed: int = 0
    errors: list = field(default_factory=list)
    #: How many rows took the operator's `--vat-class-when-missing` value. The
    #: guess is a recorded decision or it is not allowed to be a guess.
    vat_class_defaulted: int = 0

    def failed(self, file, line, value, reason):
        self.rows_failed += 1
        self.errors.append(
            {"file": file, "line": line, "value": value, "reason": str(reason)}
        )

    def summary(self):
        return (
            f"leídas {self.rows_read} · creadas {self.rows_created} · "
            f"actualizadas {self.rows_updated} · fallidas {self.rows_failed}"
        )


class Loader:
    """One run, over one directory, against one pinned tenant."""

    def __init__(self, *, tenant_id, directory, apply, vat_class_when_missing=None):
        self.tenant_id = tenant_id
        self.directory = Path(directory)
        self.apply = apply
        self.vat_class_when_missing = vat_class_when_missing
        self.report = Report()
        #: The columns the file being read actually carries. A run may supply a
        #: subset (§11.2), and **an omitted column leaves the stored value
        #: alone** -- otherwise a file with four columns would wipe the
        #: laboratorio, the registro and the handling flags off every item it
        #: names, and reactivate the ones an administrator had deactivated. A
        #: blank cell in a column the file *does* carry still clears the value:
        #: that is the operator saying so.
        self.columns: set[str] = set()

    # -- the run ----------------------------------------------------------

    def files(self):
        """The entity files present, in the reference order."""
        return [
            (entity, self.directory / f"{entity}.csv")
            for entity in ENTITIES
            if (self.directory / f"{entity}.csv").exists()
        ]

    def run(self):
        """Read every file, write an `imports` row either way, and report.

        A dry run does the whole thing inside a savepoint and rolls it back, so
        that references created earlier in the same run resolve for the files
        that follow -- a preview that could not see its own manufacturers would
        report failures the real run would not have.
        """
        present = self.files()
        started = timezone.now()
        try:
            with transaction.atomic():
                for entity, path in present:
                    try:
                        self._read(entity, path)
                    except (
                        BadHeader,
                        OSError,
                        UnicodeDecodeError,
                        csv.Error,
                    ) as bad:
                        # A malformed header, a wrong encoding or a file that
                        # cannot be opened makes that file unreadable. It is a
                        # run failure rather than a row failure, and the run
                        # keeps going -- so an operator sees every bad file in
                        # one pass, the rows already loaded from earlier files
                        # commit, and the run still writes its `imports` row.
                        self.report.failed(path.name, 1, "", bad)
                if not self.apply:
                    raise DryRun
        except DryRun:
            pass

        run = ImportRun.objects.create(
            tenant_id=self.tenant_id,
            kind=ImportKind.CATALOG,
            source=str(self.directory),
            status=(
                ImportStatus.FAILED
                if self.report.rows_failed
                else ImportStatus.COMPLETED
            ),
            dry_run=not self.apply,
            started_at=started,
            finished_at=timezone.now(),
            rows_read=self.report.rows_read,
            rows_created=self.report.rows_created,
            rows_updated=self.report.rows_updated,
            rows_failed=self.report.rows_failed,
            errors=self._errors(present),
        )
        return run

    def _errors(self, present):
        """The per-row error list, with the operator's own decision on top of it
        so a reader of `GET /api/imports` sees what the run was told to do."""
        entries = list(self.report.errors)
        if self.report.vat_class_defaulted:
            entries.insert(
                0,
                {
                    "file": "items.csv",
                    "line": 0,
                    "value": self.vat_class_when_missing,
                    "reason": (
                        f"{self.report.vat_class_defaulted} fila(s) tomaron la "
                        "clase de IVA que indicó el operador porque el archivo "
                        "no la traía"
                    ),
                },
            )
        if not present:
            entries.append(
                {
                    "file": str(self.directory),
                    "line": 0,
                    "value": "",
                    "reason": "la carpeta no tiene ningún archivo de los que este "
                    "cargador lee",
                }
            )
        return entries

    def _read(self, entity, path):
        handler = getattr(self, f"_load_{entity}")
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            found = {(name or "").strip() for name in reader.fieldnames or []}
            missing = set(REQUIRED[entity]) - found
            # A misspelled header silently drops a whole column, which is the
            # quiet defect this tool exists not to have -- and a misspelling
            # usually shows up as one missing column and one unknown one, so
            # both halves go in the same sentence rather than in two runs.
            self.columns = found & set(HEADERS[entity])
            unknown = found - set(HEADERS[entity]) - {""}
            if missing or unknown:
                said = []
                if missing:
                    said.append(f"falta «{', '.join(sorted(missing))}»")
                if unknown:
                    said.append(f"sobra «{', '.join(sorted(unknown))}»")
                raise BadHeader(
                    " y ".join(said) + f". Las columnas de este archivo son: "
                    f"{', '.join(HEADERS[entity])}"
                )
            for line, row in enumerate(reader, start=2):
                self.report.rows_read += 1
                try:
                    # Each row is its own savepoint: an IntegrityError inside a
                    # transaction poisons it, and one bad row must not take the
                    # other nine with it.
                    with transaction.atomic():
                        created = handler(
                            {key: (value or "").strip() for key, value in row.items()}
                        )
                except RowRefused as refusal:
                    self.report.failed(path.name, line, _first_value(row), refusal)
                except Exception as error:  # noqa: BLE001 -- reported, never swallowed
                    self.report.failed(path.name, line, _first_value(row), error)
                else:
                    if created:
                        self.report.rows_created += 1
                    else:
                        self.report.rows_updated += 1

    # -- entities ---------------------------------------------------------

    def _load_manufacturers(self, row):
        name = _required(row, "name", "el laboratorio necesita un nombre")
        return _upsert(
            Manufacturer,
            {"tenant_id": self.tenant_id, "name": name},
            {"nit": row.get("nit", "")},
        )

    def _load_categories(self, row):
        name = _required(row, "name", "la categoría necesita un nombre")
        parent = None
        if row.get("parent"):
            parent = self._category(row["parent"])
        return _upsert(
            Category,
            {"tenant_id": self.tenant_id, "name": name, "parent": parent},
            {},
        )

    def _load_suppliers(self, row):
        name = _required(row, "name", "el proveedor necesita un nombre")
        nit = row.get("nit", "")
        key = (
            {"tenant_id": self.tenant_id, "nit": nit}
            if nit
            else {"tenant_id": self.tenant_id, "name": name}
        )
        fields = {
            "name": name,
            "contact": row.get("contact", ""),
            "payment_terms": row.get("payment_terms", ""),
            "lead_time_days": _optional_int(row.get("lead_time_days")),
        }
        return _upsert(Supplier, key, fields, only=self._writable(fields))

    def _load_items(self, row):
        name = _required(row, "name", "el producto necesita un nombre")
        kind = row.get("type") or ItemType.PRODUCT
        if kind not in ItemType.values:
            raise RowRefused(f"«{kind}» no es un tipo: use product o service")

        vat_class = row.get("vat_class")
        if not vat_class:
            if not self.vat_class_when_missing:
                raise RowRefused(
                    "sin clase de IVA. Es el único campo que este cargador no "
                    "adivina: una clase equivocada es un error de impuestos que "
                    "se repite en cada línea. Corrija el archivo o pase "
                    "--vat-class-when-missing"
                )
            vat_class = self.vat_class_when_missing
            self.report.vat_class_defaulted += 1
        if vat_class not in VatClass.values:
            raise RowRefused(f"«{vat_class}» no es una clase de IVA")

        service = kind == ItemType.SERVICE
        if service:
            # The eleven columns the service table leaves without meaning. The
            # loader refuses a service row that fills any of them, so the
            # meaninglessness never becomes stale data somebody later trusts --
            # the same rule the editor keeps by not rendering them.
            for column in SERVICE_HAS_NO_TEXT:
                if row.get(column):
                    raise RowRefused(_meaningless(column))
            for column in SERVICE_HAS_NO_FLAG:
                if _flag(row.get(column)):
                    raise RowRefused(_meaningless(column))
            if (row.get("units_per_pack") or "1") != "1":
                raise RowRefused(_meaningless("units_per_pack"))
            if row.get("invima_status") not in ("", None, InvimaStatus.NOT_APPLICABLE):
                raise RowRefused(
                    "el registro INVIMA de un servicio es siempre «not_applicable»"
                )

        fields = {
            "type": kind,
            "name": name,
            "presentation": row.get("presentation", ""),
            "description": row.get("description", ""),
            "active_ingredient": row.get("active_ingredient", ""),
            "strength": row.get("strength", ""),
            "invima_registration": row.get("invima_registration", ""),
            "invima_expires_at": _optional_date(row.get("invima_expires_at")),
            "invima_status": _invima_status(row.get("invima_status"), service),
            "requires_prescription": _flag(row.get("requires_prescription")),
            "controlled": _flag(row.get("controlled")),
            "cold_chain": _flag(row.get("cold_chain")),
            "unit": _required(row, "unit", "el producto necesita una unidad base"),
            "splittable": _flag(row.get("splittable")),
            "units_per_pack": _optional_int(row.get("units_per_pack")) or 1,
            "vat_class": vat_class,
            "tracks_stock": not service
            and _flag(row.get("tracks_stock"), default=True),
            "active": _flag(row.get("active"), default=True),
            "manufacturer": (
                self._manufacturer(row["manufacturer"])
                if row.get("manufacturer")
                else None
            ),
            "category": self._category(row["category"])
            if row.get("category")
            else None,
            "service_cost": _optional_money(row.get("service_cost"))
            if service
            else None,
        }
        fields["tracks_lots"] = fields["tracks_stock"] and _flag(
            row.get("tracks_lots"), default=True
        )
        fields["tracks_expiry"] = fields["tracks_stock"] and _flag(
            row.get("tracks_expiry"), default=True
        )

        # Idempotency on `external_code`, falling back to the primary barcode
        # when the export has no code, and to name-and-presentation when it has
        # neither -- which is the last honest key, because that pair is unique
        # by constraint anyway.
        external_code = row.get("external_code", "")
        barcode = row.get("barcode", "")
        if external_code:
            key = {"tenant_id": self.tenant_id, "external_code": external_code}
        elif barcode:
            existing = ItemBarcode.objects.filter(
                tenant_id=self.tenant_id, code=barcode
            ).first()
            key = (
                {"tenant_id": self.tenant_id, "id": existing.item_id}
                if existing
                else {
                    "tenant_id": self.tenant_id,
                    "name": name,
                    "presentation": fields["presentation"],
                }
            )
        else:
            # Neither a code nor a barcode. §11.2 fixes the behaviour: a second
            # run **refuses those rows rather than silently doubling the
            # catalog** -- the failure is loud, which is the whole reason the
            # fallback is named rather than improvised. Matching on name and
            # presentation instead would update quietly and hide from the
            # operator that the export has no stable handle at all.
            twin = Item.objects.filter(
                tenant_id=self.tenant_id,
                name=name,
                presentation=fields["presentation"],
            ).first()
            if twin is not None:
                raise RowRefused(
                    "este producto ya existe y la fila no trae código externo ni "
                    "código de barras, así que nada dice si es el mismo. Agregue "
                    "una de las dos columnas al archivo"
                )
            key = {
                "tenant_id": self.tenant_id,
                "name": name,
                "presentation": fields["presentation"],
            }
        if external_code:
            fields["external_code"] = external_code

        item, created = _upsert(
            Item, key, fields, returning=True, only=self._writable(fields)
        )
        if barcode:
            self._barcode(item, barcode, primary=True)
        return created

    def _load_item_barcodes(self, row):
        item = self._item(_required(row, "item", "el código necesita un producto"))
        code = _required(row, "code", "el código no puede estar vacío")
        return self._barcode(item, code, primary=_flag(row.get("is_primary")))

    def _barcode(self, item, code, *, primary):
        """One code path for both entry points.

        A code already held by another item is refused rather than reassigned --
        an ambiguous scan sells the wrong product at the wrong price (§4) -- and
        a new primary demotes the item's previous one first, or the partial
        unique index refuses the row on the second run of the same file.
        """
        held = ItemBarcode.objects.filter(tenant_id=self.tenant_id, code=code).first()
        if held is not None and held.item_id != item.id:
            raise RowRefused(f"el código ya es de «{held.item.name}»")
        if primary:
            ItemBarcode.objects.filter(item=item, is_primary=True).exclude(
                code=code
            ).update(is_primary=False)
        return _upsert(
            ItemBarcode,
            {"tenant_id": self.tenant_id, "code": code},
            {"item": item, "is_primary": primary},
        )

    def _load_supplier_items(self, row):
        supplier = self._supplier(
            _required(row, "supplier", "el enlace necesita un proveedor")
        )
        item = self._item(_required(row, "item", "el enlace necesita un producto"))
        preferred = _flag(row.get("is_preferred"))
        if preferred:
            SupplierItem.objects.filter(item=item, is_preferred=True).exclude(
                supplier=supplier
            ).update(is_preferred=False)
        return _upsert(
            SupplierItem,
            {"tenant_id": self.tenant_id, "supplier": supplier, "item": item},
            {
                "supplier_code": row.get("supplier_code", ""),
                "cost": _optional_money(row.get("cost")),
                "min_order_pack": _optional_int(row.get("min_order_pack")) or 1,
                "is_preferred": preferred,
            },
        )

    def _load_item_prices(self, row):
        item = self._item(_required(row, "item", "el precio necesita un producto"))
        location = None
        if row.get("location"):
            location = Location.objects.filter(
                tenant_id=self.tenant_id, code=row["location"]
            ).first()
            if location is None:
                raise RowRefused(f"la sede «{row['location']}» no existe")
        price = _money(row, "price", "el precio no es un número")
        if price < 0:
            raise RowRefused("el precio no puede ser negativo")
        # §11.2 · **prices per pack rather than per unit are converted by
        # `units_per_pack` at load**, the same conversion receiving and supplier
        # cost use. The column exists because an export that cannot say which of
        # the two it means is the one case worth a phone call before the run,
        # and a loader that guessed would put a box price on a tableta.
        if _flag(row.get("per_pack")):
            if item.units_per_pack < 1:
                raise RowRefused("este producto no declara unidades por empaque")
            price = (price / item.units_per_pack).quantize(prices.CENTAVO)
        effective_from = (
            _optional_date(row.get("effective_from")) or timezone.localdate()
        )
        _, changed = prices.import_price(
            tenant_id=self.tenant_id,
            item=item,
            price=price,
            effective_from=effective_from,
            location=location,
            effective_to=_optional_date(row.get("effective_to")),
        )
        return changed

    def _load_customers(self, row):
        document_type = row.get("document_type", "")
        document = row.get("document", "")
        if document and document_type not in DOCUMENT_TYPES:
            raise RowRefused(
                f"«{document_type}» no es un tipo de documento: use "
                + ", ".join(DOCUMENT_TYPES)
            )
        consent = _flag(row.get("data_consent"))
        fields = {
            "name": row.get("name", ""),
            "phone": row.get("phone", ""),
            "email": row.get("email", ""),
            "address": row.get("address", ""),
            "notes": row.get("notes", ""),
            "data_consent": consent,
        }
        if not document:
            if not fields["name"]:
                raise RowRefused("el cliente necesita un documento o un nombre")
            key = {
                "tenant_id": self.tenant_id,
                "document": "",
                "name": fields["name"],
            }
        else:
            key = {
                "tenant_id": self.tenant_id,
                "document_type": document_type,
                "document": document,
            }
        # The moment consent was given is not the moment a file was re-applied.
        # Stamping it on every run would move a Ley 1581 timestamp forward each
        # time somebody re-ran the same export, and would make the run report a
        # change where there was none.
        existing = Customer.objects.filter(**key).first()
        if not consent:
            fields["data_consent_at"] = None
        elif existing is not None and existing.data_consent:
            fields["data_consent_at"] = existing.data_consent_at
        else:
            fields["data_consent_at"] = timezone.now()
        return _upsert(Customer, key, fields, only=self._writable(fields))

    #: The stored field a column feeds, where the two are not the same word.
    #: Everything else maps to itself.
    SOURCE_COLUMN = {
        "manufacturer": "manufacturer",
        "category": "category",
        "tracks_lots": "tracks_lots",
        "tracks_expiry": "tracks_expiry",
        "tracks_stock": "tracks_stock",
        "invima_status": "invima_status",
        "vat_class": "vat_class",
        "data_consent_at": "data_consent",
    }

    def _writable(self, fields):
        """The fields whose column the file being read actually carries.

        An omitted column leaves the stored value alone; a blank cell in a
        column the file does carry still clears it. That distinction is the
        whole of §11.2's "a run may supply a subset" -- without it, a four-column
        items file wipes the laboratorio, the registro and the handling flags
        off every item it names.
        """
        return {
            field
            for field in fields
            if self.SOURCE_COLUMN.get(field, field) in self.columns
        }

    # -- reference resolution ---------------------------------------------
    #
    # Every one of these fails the row rather than creating what it could not
    # find. Under the pin, a reference in another network is simply not there --
    # which is how a run pinned to tenant A writes nothing into tenant B and
    # says so, rather than reaching across.

    def _manufacturer(self, name):
        row = Manufacturer.objects.filter(tenant_id=self.tenant_id, name=name).first()
        if row is None:
            raise RowRefused(f"el laboratorio «{name}» no existe en esta droguería")
        return row

    def _category(self, path):
        """`Medicamentos > Analgésicos`, or a bare name for a top-level one."""
        parts = [part.strip() for part in path.split(">") if part.strip()]
        if not parts or len(parts) > 2:
            raise RowRefused(
                f"«{path}» no es una categoría: el catálogo tiene dos niveles, "
                "escritos «Madre > Hija»"
            )
        parent = None
        row = None
        for part in parts:
            row = Category.objects.filter(
                tenant_id=self.tenant_id, name=part, parent=parent
            ).first()
            if row is None:
                raise RowRefused(f"la categoría «{path}» no existe en esta droguería")
            parent = row
        return row

    def _supplier(self, key):
        row = Supplier.objects.filter(tenant_id=self.tenant_id, nit=key).first()
        if row is None:
            row = Supplier.objects.filter(tenant_id=self.tenant_id, name=key).first()
        if row is None:
            raise RowRefused(f"el proveedor «{key}» no existe en esta droguería")
        return row

    def _item(self, key):
        """An item by `external_code`, then by any barcode, then by name.

        Three resolutions in a fixed order, because a legacy export names its
        products by whichever of the three it happens to hold, and a miss fails
        the row.
        """
        row = Item.objects.filter(tenant_id=self.tenant_id, external_code=key).first()
        if row is None:
            barcode = ItemBarcode.objects.filter(
                tenant_id=self.tenant_id, code=key
            ).first()
            row = barcode.item if barcode else None
        if row is None:
            matches = list(Item.objects.filter(tenant_id=self.tenant_id, name=key)[:2])
            if len(matches) > 1:
                raise RowRefused(
                    f"«{key}» nombra más de un producto: use el código externo o "
                    "un código de barras"
                )
            row = matches[0] if matches else None
        if row is None:
            raise RowRefused(f"el producto «{key}» no existe en esta droguería")
        return row


# ---------------------------------------------------------------------------
# Field readers. Each one refuses rather than coercing: a silently coerced value
# is a defect that reaches a shelf.
# ---------------------------------------------------------------------------


def _first_value(row):
    for value in row.values():
        if value:
            return str(value)[:120]
    return ""


def _required(row, column, message):
    value = row.get(column, "")
    if not value:
        raise RowRefused(message)
    return value


TRUE = {"1", "true", "t", "yes", "y", "si", "sí", "x"}
FALSE = {"0", "false", "f", "no", "n", ""}


def _flag(value, *, default=False):
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in TRUE:
        return True
    if text in FALSE:
        return False
    raise RowRefused(f"«{value}» no es sí o no")


def _optional_int(value):
    if not value:
        return None
    try:
        return int(str(value).strip())
    except ValueError as error:
        raise RowRefused(f"«{value}» no es un número entero") from error


def _optional_money(value):
    if not value:
        return None
    return _to_decimal(value)


def _money(row, column, message):
    value = row.get(column, "")
    if not value:
        raise RowRefused(message)
    return _to_decimal(value)


#: `12.500` and `1.234.500` -- a dot followed by exactly three digits, all the
#: way down. Pesos carry at most two decimals, so three digits after a dot is
#: never a fraction and the dot is always a thousands separator.
THOUSANDS = re.compile(r"^-?\d{1,3}(\.\d{3})+$")

#: `1,234.56` -- the English spelling of the same figure, and the one shape this
#: tool refuses rather than reads. Under the Colombian convention it would mean
#: 1,23456 pesos: a price a thousand times too small, loaded silently, onto a
#: shelf. A number nobody can tell apart is worth a phone call before the run.
ENGLISH = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def _to_decimal(value):
    """A figure in pesos, in either spelling the export can arrive in.

    A Colombian export writes `12.500,50`; a machine-made one writes
    `12500.50`. Both are read, and anything else is refused rather than guessed
    at -- a silently coerced price is a defect that reaches a shelf.
    """
    text = str(value).strip().replace(" ", "")
    if ENGLISH.match(text):
        raise RowRefused(
            f"«{value}» está escrito a la inglesa y en la convención colombiana "
            "valdría mil veces menos. Escriba el archivo con coma decimal "
            "(12.500,50) o sin separador de miles (12500.50)"
        )
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif THOUSANDS.match(text):
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise RowRefused(f"«{value}» no es un valor en pesos") from error


DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y")


def _optional_date(value) -> date | None:
    if not value:
        return None
    for shape in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), shape).date()
        except ValueError:
            continue
    raise RowRefused(f"«{value}» no es una fecha (use AAAA-MM-DD)")


def _invima_status(value, service):
    if service:
        return InvimaStatus.NOT_APPLICABLE
    if not value:
        return InvimaStatus.NOT_APPLICABLE
    if value not in InvimaStatus.values:
        raise RowRefused(f"«{value}» no es un estado de registro INVIMA")
    return value


def _upsert(model, key, fields, *, returning=False, only=None):
    """Write a row only when it differs, so a second run genuinely changes
    nothing -- `updated_at` included. The same file applied twice is a no-op.

    `only` names the fields an **update** may touch; a create always writes the
    whole defaulted dict, because a new row needs a value in every column.
    """
    row = model.objects.filter(**key).first()
    if row is None:
        # Merged rather than passed as two mappings: a column can legitimately
        # be both the natural key and a field to write -- `items.external_code`
        # is exactly that -- and `create(**key, **fields)` would refuse it.
        row = model.objects.create(**{**key, **fields})
        return (row, True) if returning else True
    if only is not None:
        fields = {name: value for name, value in fields.items() if name in only}
    changed = [
        name for name, value in fields.items() if getattr(row, name, None) != value
    ]
    if changed:
        for name, value in fields.items():
            setattr(row, name, value)
        row.save(update_fields=[*changed, "updated_at"])
    return (row, False) if returning else False
