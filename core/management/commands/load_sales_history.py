"""Load a droguería's sales history from the previous system (§11.2).

Internal, and run by us once per tenant during onboarding, against a file whose
shape is different for every legacy system -- which is exactly why it is a
command and not an endpoint (ledger rule 6, context two). `--tenant` is required
and is pinned before a row is read: a loader that inferred a tenant from a file
would be a loader that could write one network's history into another's, and
under FORCE ROW LEVEL SECURITY an unpinned run would silently write nothing at
all, which is the worse failure of the two.

    make migrate
    .venv/bin/python manage.py load_sales_history --tenant demo-la-45 --file ./history.csv
    .venv/bin/python manage.py load_sales_history --tenant demo-la-45 --file ./history.csv --apply

**A run is a dry run unless `--apply` is passed**, and either way it writes its
`imports` row -- so a preview is on the record too. **Re-running the same file
writes nothing**: every row's `client_uuid` is derived from the tenant, the
source and the legacy document's own identifier, so a second run collides with
`UNIQUE (tenant_id, client_uuid)` and reports duplicates rather than doubling a
history.

**Nothing this command writes ever reaches anything fiscal, cash or shift.** The
rows land at `source = imported` with no shift and no device, and the database
refuses anything else.
"""

from core.management.commands._tenant import TenantCommand
from core.purchasing.loader import FILE, HEADER, Loader


class Command(TenantCommand):
    help = (
        "Load closed sales and their lines from the previous system, at "
        "`source = imported`. History only: no stock, no payments, no shifts."
    )

    def add_tenant_arguments(self, parser):
        parser.add_argument(
            "--file",
            help=f"The export, in the {FILE} contract: {', '.join(HEADER)}.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write. Without it the run validates, reports and rolls back.",
        )
        parser.add_argument(
            "--source",
            default="",
            help=(
                "What the row identities are keyed on besides the tenant and the "
                "legacy id. Defaults to the file's own name, so re-running one "
                "export is a no-op while two exports of two periods do not have "
                "to coordinate their ids."
            ),
        )
        parser.add_argument(
            "--location",
            action="append",
            default=[],
            metavar="LEGACY=CODE",
            help=(
                "Map a legacy location code onto a sede's own code (§11.6). "
                "Repeatable. An unmapped code is a row the loader refuses "
                "rather than guesses at."
            ),
        )
        parser.add_argument(
            "--columns",
            action="store_true",
            help="Print the CSV contract and exit.",
        )

    def handle(self, *args, **options):
        if options.get("columns"):
            self.stdout.write(FILE)
            self.stdout.write(f"  {', '.join(HEADER)}")
            return None
        if not options.get("file"):
            # Argparse cannot express "required unless --columns", so the
            # refusal is here -- and it is still before any connection is
            # opened, which is what rule 6's second context is about.
            self.stderr.write(self.style.ERROR("--file is required."))
            raise SystemExit(2)
        failed = super().handle(*args, **options)
        if failed:
            # **Outside** the pinned transaction. Raising inside it would roll
            # back the rows that did load and the `imports` row that says which
            # ones did.
            raise SystemExit(1)
        return None

    def handle_tenant(self, tenant_id, *args, **options):
        mapping = {}
        for entry in options["location"]:
            legacy, _, code = entry.partition("=")
            if not legacy or not code:
                self.stderr.write(
                    self.style.ERROR(f"--location takes LEGACY=CODE, got «{entry}».")
                )
                raise SystemExit(2)
            mapping[legacy.strip()] = code.strip()

        loader = Loader(
            tenant_id=tenant_id,
            path=options["file"],
            apply=options["apply"],
            source=options["source"],
            locations=mapping,
        )
        run = loader.run()

        mode = "APLICADA" if run.dry_run is False else "PRUEBA (sin --apply)"
        self.stdout.write(f"carga de histórico {mode} · tenant {tenant_id}")
        self.stdout.write(f"  {options['file']}")
        self.stdout.write(f"  {loader.report.summary()}")
        for entry in run.errors[:50]:
            self.stdout.write(
                f"  {entry['file']}:{entry['line']}  «{entry['value']}»  "
                f"{entry['reason']}"
            )
        if run.rows_failed:
            self.stderr.write(
                self.style.ERROR(
                    f"{run.rows_failed} fila(s) no se pudieron cargar. "
                    f"El registro de la carga es {run.id}."
                )
            )
            return run.rows_failed
        self.stdout.write(self.style.SUCCESS(f"registro de la carga {run.id}"))
        return 0
