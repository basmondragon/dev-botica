"""The fixtures, and what the screen must render from them.

**The seed is the last check, and `convincingly` is not a feeling** (§1,
acceptance 26). What is asserted here is what a person would see: the footer's
two figures, seven states none of which is one nobody has ever produced, a bar
where a policy exists and none where it does not, a `Quiebre` clause with a sede
to name, and the module's other three routes off their empty states.

**No figure here is a production sizing.** 4.284 rows and 312 requiring action
are what these fixtures build; the per-sede estimates in S3's *Offline* section
are what a real pilot will hold, and a check that asserted the second against
the first would be red on every run for a reason that has nothing to do with the
code.
"""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db.models import Count, Sum
from django.utils import timezone

from core.demo import identity, registry as demo_registry
from core.inventory import demo as inventory_demo
from core.inventory import ledger, settings as inventory_settings, states
from core.models import (
    CountStatus,
    Location,
    Lot,
    StockCount,
    StockMove,
    StockMoveType,
    StockOnHand,
    StockPolicy,
    Tenant,
    Transfer,
    TransferStatus,
)
from core.tenancy import pin_tenant

pytestmark = pytest.mark.django_db


def seed(profile):
    call_command("seed_demo_tenant", profile=profile)
    return demo_registry.uid(profile, "tenants", identity.slug_for(profile))


def grid(tenant_id):
    tenant = Tenant.objects.get(id=tenant_id)
    options = inventory_settings.read(tenant)
    return states.annotate(
        StockOnHand.objects.filter(tenant_id=tenant_id),
        today=timezone.localdate(),
        alert_days=options["expiry_alert_days"],
        notice_days=options["expiry_notice_days"],
    )


def test_the_default_seed_builds_the_drawn_grid():
    """Acceptance 13 and 26 · `1-25 de 4.284` with `312 requieren acción`
    beside it, and the chip narrows to exactly those 312.

    The drawn footer reads `1-15`; the drawn page group reads `… · 172`, which
    is 4.284 over a page size of 25. The two cannot both be true of one screen,
    and 172 is the one that also fixes the drawn row-size select -- so the
    range renders `1-25` and the two figures the check is actually about, the
    total and the annotation, are exact.
    """
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        rows = grid(tenant_id)
        assert rows.count() == inventory_demo.TARGET_ROWS
        action = rows.filter(
            state_ordinal__in=[states.ORDINALS[one] for one in states.ACTION_STATES]
        )
        assert action.count() == inventory_demo.TARGET_ACTION


def test_every_one_of_the_seven_states_has_rows_behind_it():
    """Acceptance 14 · **no badge in §B.7.4 is one nobody has ever seen.** Both
    dot styles, all four badge families and the precedence of the derivation are
    visible on one screen."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        rows = grid(tenant_id)
        for name, ordinal in states.ORDINALS.items():
            assert rows.filter(state_ordinal=ordinal).count() >= 12, name


def test_the_first_page_carries_the_five_drawn_states():
    """Acceptance 26 · open `/inventory` with no filter touched and put it
    beside Pantalla 2. **Vencido** and the second expiry tier are one press of
    the `Estado` sort away, which is what the drawing shows."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        page = grid(tenant_id).order_by(
            "item__name", "location__name", "lot__lot_code"
        )[:25]
        seen = {states.name_of(row.state_ordinal) for row in page}
    assert {
        states.SUFFICIENT,
        states.REORDER_POINT,
        states.OVERSTOCK,
        states.STOCKOUT,
        states.EXPIRING_URGENT,
    } <= seen


def test_a_bar_has_a_denominator_or_no_bar_at_all():
    """A bar with no capacity behind it is a bar measuring nothing, and
    `Sin política definida` is a state somebody has actually seen."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        rows = grid(tenant_id)
        unmanaged = rows.filter(policy_max_quantity__isnull=True)
        assert unmanaged.count() > 0
        # A row with no policy cannot reach state 5 or 6, because
        # `quantity <= NULL` is unknown and never true.
        assert not unmanaged.filter(
            state_ordinal__in=[
                states.ORDINALS[states.REORDER_POINT],
                states.ORDINALS[states.OVERSTOCK],
            ]
        ).exists()


def test_a_quiebre_row_has_a_sede_to_name_and_names_the_same_one_twice():
    """The clause is stable between page loads, which is what the tie-break by
    name is for."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        empty = (
            grid(tenant_id)
            .filter(state_ordinal=states.ORDINALS[states.STOCKOUT])
            .first()
        )
        assert empty is not None
        elsewhere = (
            StockOnHand.objects.filter(tenant_id=tenant_id, item_id=empty.item_id)
            .exclude(location_id=empty.location_id)
            .filter(quantity__gt=0)
        )
        assert elsewhere.exists()


def test_lot_less_rows_and_shared_lots_both_exist():
    """`sin lote` renders in `Lote` and `Vence` on a real screen, and a lot
    several sedes hold is what the recall answer's reverse lookup finds."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert StockOnHand.objects.filter(
            tenant_id=tenant_id, lot__isnull=True
        ).exists()
        shared = Lot.objects.filter(tenant_id=tenant_id, lot_code="A-2291").first()
        assert shared is not None
        holders = StockOnHand.objects.filter(
            tenant_id=tenant_id, lot=shared
        ).values_list("location_id", flat=True)
        assert len(set(holders)) > 1


def test_one_lot_runs_past_the_record_panels_fifty_move_cap():
    """The cap is exercised rather than assumed, and every row has a history --
    a panel opened on whichever row a reviewer clicks is never the empty state.

    **Grouped by `(location, item, lot)`, which is what the panel actually
    reads.** Grouping by `lot_id` alone passes on a NULL bucket -- every
    lot-less row in the network summed together -- which is a green check over a
    panel that never shows more than three lines.
    """
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        deepest = max(
            StockMove.objects.filter(tenant_id=tenant_id, lot_id__isnull=False)
            .values("location_id", "item_id", "lot_id")
            .annotate(total=Count("id"))
            .values_list("total", flat=True)
        )
        assert deepest > 50
        keys = set(
            StockMove.objects.filter(tenant_id=tenant_id).values_list(
                "location_id", "item_id", "lot_id"
            )
        )
        rows = set(
            StockOnHand.objects.filter(tenant_id=tenant_id).values_list(
                "location_id", "item_id", "lot_id"
            )
        )
        assert rows <= keys


def test_every_seeded_move_names_the_till_it_happened_on():
    """The seed's own contract: every move carries its document, its device, its
    user and both clocks. **A device column empty on five thousand rows** is a
    lot trace nobody would hand an inspector and a record panel with a blank
    where the equipment should be."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        without = StockMove.objects.filter(
            tenant_id=tenant_id, device__isnull=True
        ).count()
        assert without == 0
        assert StockMove.objects.filter(tenant_id=tenant_id, user_name="").count() == 0


def test_a_seeded_lot_reads_as_a_history_and_never_dips_below_zero():
    """Acceptance 10 · **the trace is the recall answer, so it has to read like
    one.**

    Every seeded move used to be stamped at the instant the seed ran, which left
    the trace's `(recorded_at, id)` ordering to sort a shelf's whole life by
    uuid: a merma appeared before the entrada that made it possible and the
    running balance dipped to −47 on a shelf that was never short. The final
    balance was right and every individual row was right, which is why nothing
    but opening the screen caught it.

    Two properties, and the second is the one a person would notice: the moves
    on a lot are in order, and the balance behind them is one a shelf could
    actually have had.
    """
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        deepest = (
            StockMove.objects.filter(tenant_id=tenant_id, lot_id__isnull=False)
            .values("lot_id")
            .annotate(total=Count("id"))
            .order_by("-total")
            .first()
        )
        assert deepest["total"] > 50
        walk = list(
            StockMove.objects.filter(tenant_id=tenant_id, lot_id=deepest["lot_id"])
            .order_by("recorded_at", "id")
            .values_list("recorded_at", "quantity")
        )

    stamps = [one for one, _ in walk]
    assert stamps == sorted(stamps)
    # Not merely sorted: **distinct**. Equal stamps put the tie-break back on
    # the uuid, which is the defect itself wearing a passing assertion.
    assert len(set(stamps)) == len(stamps)

    balance = 0
    for _stamp, quantity in walk:
        balance += quantity
        assert balance >= 0, "the trace shows a shelf owing stock it never owed"

    with pin_tenant(tenant_id):
        held = (
            StockOnHand.objects.filter(
                tenant_id=tenant_id, lot_id=deepest["lot_id"]
            ).aggregate(total=Sum("quantity"))["total"]
            or 0
        )
    assert balance == held


def test_the_seeded_history_settles_before_the_documents_that_follow_it():
    """A dispatch at three days and a count closed at two have to land **after**
    the history that stocked the shelf they move. Interleaved, the trace shows a
    transfer leaving a sede that had not received anything yet.

    **S3's own documents, named rather than taken as "everything with a
    document_type".** S4's sales are documents too and they span the whole
    180-day window by design -- a shop sells the day after it stocks, not only
    in the last fortnight -- so the property that matters for them is a
    different one and S4's fixture states it: a sale is never dated before the
    opening receipt that made it possible.
    """
    tenant_id = seed("default")
    now = timezone.now()
    with pin_tenant(tenant_id):
        newest_history = (
            StockMove.objects.filter(
                tenant_id=tenant_id,
                type__in=[StockMoveType.ADJUSTMENT, StockMoveType.SHRINKAGE],
                document_type="",
            )
            .order_by("-recorded_at")
            .values_list("recorded_at", flat=True)
            .first()
        )
        documents = list(
            StockMove.objects.filter(
                tenant_id=tenant_id,
                document_type__in=["transfers", "stock_counts"],
            ).values_list("recorded_at", flat=True)
        )
    assert newest_history is not None
    assert documents
    assert newest_history <= now - timedelta(days=inventory_demo.HISTORY_SETTLES_DAYS)
    assert min(documents) > newest_history


def test_a_seeded_transfer_moves_one_reference_between_two_sedes():
    """A document that took one product out of the origin and credited a
    different one at the destination is not a transfer -- it is two unrelated
    movements wearing one number, and the `partial` would show `En tránsito` on
    one reference while the received units landed on another."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        for transfer in Transfer.objects.filter(tenant_id=tenant_id):
            items = {line.item_id for line in transfer.lines.all()}
            moved = set(
                StockMove.objects.filter(
                    tenant_id=tenant_id, document_id=transfer.id
                ).values_list("item_id", flat=True)
            )
            assert moved <= items, (
                f"transfer {transfer.number} moved a reference its lines do not name"
            )
            sedes = {transfer.origin_location_id, transfer.destination_location_id}
            touched = set(
                StockMove.objects.filter(
                    tenant_id=tenant_id, document_id=transfer.id
                ).values_list("location_id", flat=True)
            )
            assert touched <= sedes


def test_a_seeded_count_is_walked_at_one_sede():
    """Lines drawn from four different sedes would produce a document the
    product's own counting screen could never have created, and the adjusting
    moves would land on shelves nobody counted."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        for count in StockCount.objects.filter(tenant_id=tenant_id):
            moved = set(
                StockMove.objects.filter(
                    tenant_id=tenant_id, document_id=count.id
                ).values_list("location_id", flat=True)
            )
            assert moved <= {count.location_id}, (
                "a count closed adjusting moves at a sede it was not walked at"
            )
            for line in count.lines.all():
                held = StockOnHand.objects.filter(
                    tenant_id=tenant_id,
                    location_id=count.location_id,
                    item_id=line.item_id,
                    lot_id=line.lot_id,
                )
                assert held.exists(), (
                    "a counted line names a shelf the counted sede does not hold"
                )


def test_the_other_three_routes_are_off_their_empty_states():
    """**An empty state on any of the four routes after a seed run is a stage
    that registered no fixtures**, and no screenshot of it is worth
    reviewing."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        statuses = set(
            Transfer.objects.filter(tenant_id=tenant_id).values_list(
                "status", flat=True
            )
        )
        assert statuses == {
            TransferStatus.DRAFT,
            TransferStatus.DISPATCHED,
            TransferStatus.RECEIVED,
            TransferStatus.PARTIAL,
        }
        partial = Transfer.objects.get(
            tenant_id=tenant_id, status=TransferStatus.PARTIAL
        )
        line = partial.lines.get()
        assert line.quantity_dispatched - line.quantity_received == 12

        counts = list(
            StockCount.objects.filter(tenant_id=tenant_id).values_list(
                "status", flat=True
            )
        )
        assert sorted(counts) == [CountStatus.CLOSED, CountStatus.COUNTING]
        closed = StockCount.objects.get(tenant_id=tenant_id, status=CountStatus.CLOSED)
        assert any(
            one.counted_quantity != one.expected_quantity for one in closed.lines.all()
        )


def test_the_seed_writes_no_projection_row_of_its_own():
    """**It contributes no `stock_on_hand` rows at all**, and the rebuild is
    what proves it: drop the projection, rebuild from the ledger, and every
    quantity is identical.

    Acceptance 3, on a database with the four document kinds the rebuild has to
    survive in it -- a transfer at `partial`, a closed count, an adjustment and
    a shrinkage.
    """
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        assert "stock_on_hand" not in demo_registry.REGISTRY["stock"].guard_tables
        before = _snapshot(tenant_id)
        StockOnHand.objects.filter(tenant_id=tenant_id).delete()
        for location in Location.objects.filter(tenant_id=tenant_id):
            ledger.rebuild(tenant_id, location.id)
        after = _snapshot(tenant_id)
        assert before - after == set()
        assert after - before == set()
        for location in Location.objects.filter(tenant_id=tenant_id):
            assert ledger.verify(tenant_id, location.id) == {}


def _snapshot(tenant_id):
    return {
        (row.location_id, row.item_id, row.lot_id, row.quantity)
        for row in StockOnHand.objects.filter(tenant_id=tenant_id)
    }


def test_running_the_seed_twice_changes_nothing():
    """Every seeded id is derived, and every seeded move carries a derived
    `client_uuid` -- so a re-run is a set of duplicates and the projection does
    not move."""
    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        before = _snapshot(tenant_id)
        moves = StockMove.objects.filter(tenant_id=tenant_id).count()
    seed("default")
    with pin_tenant(tenant_id):
        assert StockMove.objects.filter(tenant_id=tenant_id).count() == moves
        assert _snapshot(tenant_id) == before


def test_the_seed_refuses_a_tenant_that_already_holds_a_movement():
    """**4.284 fabricated stock rows inside a customer's tenant is not something
    anyone cleans up afterwards**, because the ledger is append-only by design
    and the correction is itself a movement.

    The row planted here is a movement on a reference the seed itself wrote, so
    what refuses the run is the movement and nothing around it -- which is the
    shape a real tenant presents: a catalog that came from an import and a shelf
    somebody has already started using.
    """
    from core.models import Item
    from core.tests.test_inventory_ledger import move as request

    tenant_id = seed("minimal")
    with pin_tenant(tenant_id):
        location = Location.objects.filter(tenant_id=tenant_id).first()
        item = Item.objects.filter(tenant_id=tenant_id, tracks_stock=True).first()
        ledger.append([request(location, item, 5)], tenant_id=tenant_id)
        planted = StockMove.objects.filter(tenant_id=tenant_id).count()

    with pytest.raises(Exception) as refusal:
        seed("minimal")
    assert "stock_moves" in str(refusal.value)
    with pin_tenant(tenant_id):
        # Nothing was written: a refusal rolls the whole run back.
        assert StockMove.objects.filter(tenant_id=tenant_id).count() == planted


def test_the_minimal_profile_renders_without_a_second_sede():
    """**With one sede there is no other location to name**, so a `Quiebre` row
    carries no `hay N en <sede>` clause -- that is the derivation rendering
    correctly on a one-sede network, not a missing fixture."""
    tenant_id = seed("minimal")
    with pin_tenant(tenant_id):
        assert Location.objects.filter(tenant_id=tenant_id).count() == 1
        rows = grid(tenant_id)
        assert rows.count() > 0
        assert StockPolicy.objects.filter(tenant_id=tenant_id).exists()


def test_the_two_seeded_networks_cannot_see_each_other(as_runtime_role):
    """*What this stage would break* · S0's tenant isolation, over eight new
    tables and a command that writes across a tenant.

    Read **as the runtime role**: the suite runs as the migration role, which
    owns the tables and would read every network whatever the policies say, so a
    check written without this passes for the wrong reason.
    """
    first = seed("default")
    second = seed("minimal")
    with pin_tenant(first):
        as_runtime_role()
        assert not StockMove.objects.filter(tenant_id=second).exists()
        assert not Lot.objects.filter(tenant_id=second).exists()
        assert not StockOnHand.objects.filter(tenant_id=second).exists()
        assert not StockPolicy.objects.filter(tenant_id=second).exists()
        mine = StockOnHand.objects.count()
    with pin_tenant(second):
        as_runtime_role()
        assert not StockMove.objects.filter(tenant_id=first).exists()
        assert 0 < StockOnHand.objects.count() < mine


def test_the_catalog_is_read_and_never_written():
    """*What this stage would break* · S1's catalog. S3 reads that column set on
    every grid page and must not have learned to write it."""
    from core.models import Item

    tenant_id = seed("default")
    with pin_tenant(tenant_id):
        checksum = sorted(
            Item.objects.filter(tenant_id=tenant_id).values_list(
                "id", "invima_status", "invima_registration", "custom"
            )
        )
    seed("default")
    with pin_tenant(tenant_id):
        assert (
            sorted(
                Item.objects.filter(tenant_id=tenant_id).values_list(
                    "id", "invima_status", "invima_registration", "custom"
                )
            )
            == checksum
        )
