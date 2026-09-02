"""Load a droguería's master data from the documented CSV contract.

Internal, and run by us during onboarding -- there is no tenant-facing import
wizard in v1 (§12, design-system §B.8.4·7 has no owner). It is the second
command on S0's tenant-pinning path (ledger rule 6, context two): `--tenant` is
required and is pinned before a row is read.

    make migrate
    .venv/bin/python manage.py load_catalog --tenant demo-la-45 --dir ./export
    .venv/bin/python manage.py load_catalog --tenant demo-la-45 --dir ./export --apply

**A run is a dry run unless `--apply` is passed.** The cost of that decision is
one flag; the cost of the opposite is a pilot's catalog silently doubled by a
re-run somebody thought was a preview. Either way the run writes its `imports`
row, so a preview is on the record too, and **the command exits non-zero if any
row failed** -- an eight-file onboarding run that reports success over a partial
load is the exact failure that exit code exists for.
"""

from core.catalog.loader import ENTITIES, HEADERS, Loader
from core.management.commands._tenant import TenantCommand
from core.models import VatClass


class Command(TenantCommand):
    help = (
        "Load manufacturers, categories, suppliers, items, barcodes, supplier "
        "links, prices and customers from one CSV per entity. Master data only."
    )

    def add_tenant_arguments(self, parser):
        parser.add_argument(
            "--dir",
            required=True,
            help=(
                "A directory holding any of: "
                + ", ".join(f"{entity}.csv" for entity in ENTITIES)
                + ". Files are read in that order, which is the reference order."
            ),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write. Without it the run validates, reports and rolls back.",
        )
        parser.add_argument(
            "--vat-class-when-missing",
            choices=VatClass.values,
            help=(
                "The IVA class to use for item rows that carry none. The run "
                "records how many rows took it, which is what turns a guess "
                "into a recorded operator decision."
            ),
        )
        parser.add_argument(
            "--columns",
            action="store_true",
            help="Print the CSV contract and exit.",
        )

    def handle(self, *args, **options):
        if options.get("columns"):
            for entity in ENTITIES:
                self.stdout.write(f"{entity}.csv")
                self.stdout.write(f"  {', '.join(HEADERS[entity])}")
            return None
        failed = super().handle(*args, **options)
        if failed:
            # **Outside** the pinned transaction, deliberately. Raising inside it
            # would roll back the rows that did load and the `imports` row that
            # says which ones did -- turning a partial load an operator can read
            # and fix into a silent no-op that still exits non-zero.
            raise SystemExit(1)
        return None

    def handle_tenant(self, tenant_id, *args, **options):
        loader = Loader(
            tenant_id=tenant_id,
            directory=options["dir"],
            apply=options["apply"],
            vat_class_when_missing=options.get("vat_class_when_missing"),
        )
        files = loader.files()
        run = loader.run()

        mode = "APLICADA" if run.dry_run is False else "PRUEBA (sin --apply)"
        self.stdout.write(f"carga {mode} · tenant {tenant_id}")
        for _entity, path in files:
            self.stdout.write(f"  {path.name}")
        self.stdout.write(f"  {loader.report.summary()}")
        for entry in run.errors:
            self.stdout.write(
                f"  {entry['file']}:{entry['line']}  «{entry['value']}»  "
                f"{entry['reason']}"
            )

        if run.rows_failed:
            # The exit code itself is raised by `handle`, once this transaction
            # has committed. An operator running this over eight files knows
            # without reading eight screens.
            self.stderr.write(
                self.style.ERROR(
                    f"{run.rows_failed} fila(s) no se pudieron cargar. "
                    f"El registro de la carga es {run.id}."
                )
            )
            return run.rows_failed

        self.stdout.write(self.style.SUCCESS(f"registro de la carga {run.id}"))
        return 0
