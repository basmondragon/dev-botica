"""Opening stock at scale, from a file, for one sede.

Internal, and run by us during onboarding -- the same footing as
`load_catalog`, and the same tenant-pinning path (ledger rule 6, context two):
`--tenant` is required and is pinned before a row is read. A run with no tenant
refuses rather than guessing one.

    .venv/bin/python manage.py load_opening_stock \\
        --tenant demo-la-45 --location CHA --file ./opening/chapinero.csv
    .venv/bin/python manage.py load_opening_stock ... --apply

**A run is a dry run unless `--apply` is passed**, for the reason `load_catalog`
gives: the cost of that decision is one flag, and the cost of the opposite is a
pilot's opening stock silently doubled by a re-run somebody thought was a
preview. On an append-only ledger the correction for a doubled load is itself a
movement, which is why the flag is here and not a confirmation prompt.

**It writes through the ledger service like every other path** (rule 7): one
`adjustment` move per line at `reason = opening_stock`, all under one shared
`document_id`, and the projection follows in the same transaction. It does not
touch `stock_on_hand` and it does not insert a `stock_moves` row of its own.

The lines are `adjustment` and not `receipt` because the ledger fixes `receipt`
as caused by S6, when a goods receipt against a purchase order is confirmed --
and opening stock has no order behind it. **Cost of goods must therefore never
be computed by filtering `type = 'receipt'`**; it reads `unit_cost` across every
positive type.

The CSV contract, one header row, in any column order:

    item_code   the previous system's own product code (`items.external_code`)
    barcode     any `item_barcodes.code`, as an alternative to `item_code`
    lot_code    required where the item tracks lots
    expires_at  `YYYY-MM-DD`, required where the item tracks expiry
    quantity    a positive integer, **in base units**
    unit_cost   optional; what these units cost to acquire, per base unit
"""

import csv
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from core import audit
from core.inventory import ledger
from core.management.commands._tenant import TenantCommand
from core.models import AuditAction, Item, ItemBarcode, Location, StockMoveType

HEADERS = ("item_code", "barcode", "lot_code", "expires_at", "quantity", "unit_cost")


class Command(TenantCommand):
    help = (
        "Load one sede's opening stock from a CSV. Writes one adjustment move "
        "per line through the ledger service, under one shared document id."
    )

    def add_tenant_arguments(self, parser):
        parser.add_argument(
            "--location", required=True, help="The sede's `code`, e.g. CHA."
        )
        parser.add_argument("--file", required=True, help="The CSV to read.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write. Without it the run validates, reports and rolls back.",
        )
        parser.add_argument(
            "--columns", action="store_true", help="Print the CSV contract and exit."
        )

    def handle_tenant(self, tenant_id, *args, **options):
        if options["columns"]:
            self.stdout.write("opening_stock.csv: " + ", ".join(HEADERS))
            return

        location = Location.objects.filter(code=options["location"]).first()
        if location is None:
            raise CommandError(
                f"No sede in this tenant has the code {options['location']!r}."
            )

        self._location_id = location.id
        rows, failures = self._read(options["file"])
        if failures:
            for line, reason in failures:
                self.stderr.write(self.style.ERROR(f"line {line}: {reason}"))

        document_id = uuid.uuid4()
        moves, resolved_failures = self._resolve(tenant_id, rows, document_id)
        failures.extend(resolved_failures)

        if failures:
            for line, reason in resolved_failures:
                self.stderr.write(self.style.ERROR(f"line {line}: {reason}"))

        self.stdout.write(
            f"{len(rows)} line(s) read, {len(moves)} movable, {len(failures)} failed"
        )
        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("dry run: nothing was written. Re-run with --apply.")
            )
            # A failed dry run exits non-zero for the same reason a failed apply
            # does: an onboarding script that reads a preview's exit code and
            # gets zero over sixty broken lines is a script that then applies
            # them.
            raise SystemExit(1 if failures else 0)
        if failures:
            raise CommandError(
                f"{len(failures)} line(s) failed. Nothing was written: an "
                "opening load is one document, and half of one is a shelf "
                "figure nobody can reconcile."
            )

        with transaction.atomic():
            result = ledger.append(
                moves, tenant_id=tenant_id, request_id=f"opening:{document_id}"
            )
            audit.record(
                actor=None,
                tenant_id=tenant_id,
                action=AuditAction.CREATE,
                entity_type="receipts",
                entity_id=document_id,
                after={
                    "location_id": str(location.id),
                    "reason": "opening_stock",
                    "lines": len(moves),
                    "written": len(result.written),
                    "duplicate": len(result.duplicates),
                    "skipped": len(result.skipped),
                    "units": sum(move.quantity for move in moves),
                },
                request_id=f"opening:{document_id}",
                at=timezone.now(),
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(result.written)} move(s) appended at {location.code} under "
                f"document {document_id}"
            )
        )

    def _read(self, path):
        rows, failures = [], []
        try:
            handle = open(path, newline="", encoding="utf-8-sig")
        except OSError as error:
            raise CommandError(f"Could not read {path}: {error}") from error
        with handle:
            reader = csv.DictReader(handle)
            unknown = set(reader.fieldnames or []) - set(HEADERS)
            if unknown:
                raise CommandError(
                    f"Unknown column(s): {', '.join(sorted(unknown))}. The "
                    f"contract is: {', '.join(HEADERS)}."
                )
            for line, raw in enumerate(reader, start=2):
                row = {key: (value or "").strip() for key, value in raw.items()}
                if not any(row.values()):
                    continue
                try:
                    quantity = int(row.get("quantity") or 0)
                except ValueError:
                    failures.append((line, "quantity is not a whole number"))
                    continue
                if quantity <= 0:
                    failures.append((line, "quantity must be greater than zero"))
                    continue
                row["quantity"] = quantity
                if row.get("unit_cost"):
                    try:
                        row["unit_cost"] = Decimal(row["unit_cost"])
                    except InvalidOperation:
                        failures.append((line, "unit_cost is not a number"))
                        continue
                else:
                    row["unit_cost"] = None
                if row.get("expires_at"):
                    try:
                        row["expires_at"] = datetime.strptime(
                            row["expires_at"], "%Y-%m-%d"
                        ).date()
                    except ValueError:
                        failures.append((line, "expires_at is not YYYY-MM-DD"))
                        continue
                else:
                    row["expires_at"] = None
                row["_line"] = line
                rows.append(row)
        return rows, failures

    def _resolve(self, tenant_id, rows, document_id):
        """Turn each line into a move, or into a named failure."""
        from core.inventory.api import resolve_lot

        moves, failures = [], []
        for index, row in enumerate(rows):
            item = self._item(tenant_id, row)
            if item is None:
                failures.append(
                    (row["_line"], "no item matches this item_code or barcode")
                )
                continue
            if not item.tracks_stock:
                failures.append(
                    (row["_line"], f"«{item.name}» does not track stock (A7)")
                )
                continue
            try:
                lot = resolve_lot(
                    tenant_id=tenant_id,
                    item=item,
                    lot_code=row.get("lot_code") or "",
                    expires_at=row.get("expires_at"),
                    unit_cost=row.get("unit_cost"),
                    supplier_id=None,
                )
            except ledger.Refused as refusal:
                failures.append((row["_line"], str(refusal)))
                continue
            moves.append(
                ledger.Move(
                    location_id=self._location_id,
                    item_id=item.id,
                    lot_id=lot.id if lot else None,
                    quantity=row["quantity"],
                    type=StockMoveType.ADJUSTMENT,
                    reason="opening_stock",
                    unit_cost=row.get("unit_cost"),
                    document_type="receipts",
                    document_id=document_id,
                    key=f"opening:{document_id}:{index}",
                )
            )
        return moves, failures

    def _item(self, tenant_id, row):
        if row.get("item_code"):
            return Item.objects.filter(
                tenant_id=tenant_id, external_code=row["item_code"]
            ).first()
        if row.get("barcode"):
            barcode = ItemBarcode.objects.filter(
                tenant_id=tenant_id, code=row["barcode"]
            ).first()
            return barcode.item if barcode else None
        return None
