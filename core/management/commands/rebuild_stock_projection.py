"""Rebuild `stock_on_hand` from `stock_moves`, per sede, on a live system.

**This is the operation A3 exists for**, and it is a deliverable rather than a
claim: a projection nobody has ever rebuilt is a projection nobody trusts. Drop
the whole thing, run this, and every quantity on Existencias is identical to
what it was before.

    .venv/bin/python manage.py rebuild_stock_projection --tenant demo-la-45
    .venv/bin/python manage.py rebuild_stock_projection --tenant demo-la-45 \\
        --location CHA
    .venv/bin/python manage.py rebuild_stock_projection --tenant demo-la-45 --verify

For one tenant and one location, inside one pinned transaction: take the
location's advisory lock; compute `SUM(quantity) GROUP BY item_id, lot_id` over
`stock_moves`; upsert every resulting key and delete every key the ledger no
longer produces; write the `audit_log` row; release. **The lock is the whole
correctness argument** -- the ledger service takes it on every append, so a
rebuild and a sale cannot interleave into a lost update. It runs per location so
a 20-sede network is 20 short locks and not one long one, and so a single sede
can be rebuilt during business hours without the other nineteen pausing.

`--verify` recomputes and reports the difference **without writing**. Drift is
never expected: a non-zero result means a code path wrote outside the ledger
service, which is a defect and not a thing to repair here (rule 7).
"""

from django.core.management.base import CommandError

from core import audit
from core.inventory import ledger
from core.management.commands._tenant import TenantCommand, resolve_tenant
from core.models import AuditAction, Location, LocationStatus
from core.tenancy import pin_tenant


class Command(TenantCommand):
    help = (
        "Rebuild the stock projection from the ledger, per sede, or verify it "
        "without writing."
    )

    def add_tenant_arguments(self, parser):
        parser.add_argument(
            "--location",
            help="A sede's `code`. Omitted, every active sede is done in turn.",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Recompute and report the difference without writing anything.",
        )

    def handle(self, *args, **options):
        """**One pinned transaction per sede, not one for the whole run.**

        `TenantCommand` pins once around `handle_tenant`, which is right for
        every other command: a load is one document and a document is one
        transaction. It is wrong here, and quietly so.
        `ledger.rebuild` takes `pg_advisory_xact_lock`, which releases when the
        **transaction** ends -- so under one enclosing pin its `atomic()` block
        is only a savepoint and every sede's lock is held until the command
        exits. A twenty-sede run would then be one long lock over the whole
        network rather than twenty short ones, and a counter at the first sede
        would block until the twentieth finished.

        That is the exact property the stage document names: *a single sede can
        be rebuilt during business hours without the other nineteen pausing.*
        Pinning per sede is what makes it true.
        """
        tenant_id = resolve_tenant(options["tenant"])
        with pin_tenant(tenant_id):
            locations = list(
                Location.objects.filter(status=LocationStatus.ACTIVE).order_by("code")
            )
            if options["location"]:
                locations = [
                    one for one in locations if one.code == options["location"]
                ]
                if not locations:
                    raise CommandError(
                        f"No sede in this tenant has the code {options['location']!r}."
                    )

        drifted = 0
        for location in locations:
            # Its own pin, and therefore its own transaction and its own lock.
            with pin_tenant(tenant_id):
                if options["verify"]:
                    drift = ledger.verify(tenant_id, location.id)
                    drifted += len(drift)
                    self._report_verify(tenant_id, location, drift)
                    continue
                report = ledger.rebuild(tenant_id, location.id)
                audit.record(
                    actor=None,
                    tenant_id=tenant_id,
                    action=AuditAction.UPDATE,
                    entity_type="stock_on_hand",
                    entity_id=location.id,
                    before={"rebuilt": location.code},
                    after=report,
                    request_id=f"rebuild:{location.code}",
                )
            self.stdout.write(
                f"{location.code}: {report['keys']} key(s), "
                f"{report['changed']} changed, {report['removed']} removed"
            )

        if options["verify"] and drifted:
            raise CommandError(
                f"{drifted} key(s) disagree with the ledger. That is a code path "
                "writing outside core.inventory.ledger, not a projection to "
                "repair (ownership.md rule 7)."
            )
        self.stdout.write(self.style.SUCCESS("done"))

    def _report_verify(self, tenant_id, location, drift):
        for (item_id, lot_id), sides in list(drift.items())[:20]:
            self.stderr.write(
                self.style.ERROR(
                    f"{location.code} {item_id} lot {lot_id}: ledger "
                    f"{sides['ledger']}, projection {sides['projection']}"
                )
            )
        audit.record(
            actor=None,
            tenant_id=tenant_id,
            action=AuditAction.UPDATE,
            entity_type="stock_on_hand",
            entity_id=location.id,
            before={"checked": location.code},
            after={"drift": len(drift)},
            request_id=f"verify:{location.code}",
        )
        self.stdout.write(f"{location.code}: {len(drift)} key(s) drifted")
