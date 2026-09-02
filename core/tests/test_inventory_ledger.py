"""The ledger service: append plus projection, in one transaction.

**The whole stage rests on this file.** A3 is one line in architecture.md §14 --
no code updates a quantity in place -- and every property below is a way for
that line to be false: a projection that moved without a move, a move that
moved nothing, a replayed push counted twice, a refusal at a counter, a lot past
its date at the head of the FEFO queue.
"""

import re
import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from datetime import timedelta

from django.utils import timezone

from core.inventory import ledger
from core.models import (
    Item,
    ItemType,
    Lot,
    StockMove,
    StockMoveType,
    StockOnHand,
    SyncConflict,
    SyncConflictStatus,
    SyncConflictType,
    VatClass,
)
from core.tenancy import pin_tenant
from core.tests.conftest import make_location

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures shared with the other inventory tests
# ---------------------------------------------------------------------------


def make_item(tenant, name="Acetaminofén 500 mg × 100", **overrides):
    fields = dict(
        type=ItemType.PRODUCT,
        name=name,
        presentation="caja × 100 tabletas",
        unit="caja",
        vat_class=VatClass.EXCLUDED,
        invima_status="valid",
        tracks_stock=True,
        tracks_lots=True,
        tracks_expiry=True,
    )
    fields.update(overrides)
    return Item.objects.create(tenant=tenant, **fields)


def make_lot(tenant, item, code="A-2291", days=400, cost="1200.00"):
    return Lot.objects.create(
        tenant=tenant,
        item=item,
        lot_code=code,
        expires_at=(
            timezone.localdate() + timedelta(days=days) if days is not None else None
        ),
        unit_cost=cost,
    )


def move(location, item, quantity, lot=None, kind=StockMoveType.ADJUSTMENT, **extra):
    fields = dict(
        location_id=location.id,
        item_id=item.id,
        lot_id=lot.id if lot else None,
        quantity=quantity,
        type=kind,
        key=extra.pop("key", str(uuid.uuid4())),
    )
    if kind in (
        StockMoveType.ADJUSTMENT,
        StockMoveType.SHRINKAGE,
        StockMoveType.EXPIRY,
        StockMoveType.COUNT,
    ):
        fields.setdefault("reason", "opening_stock" if quantity > 0 else "damage")
    fields.update(extra)
    return ledger.Move(**fields)


def held(tenant, location, item, lot=None):
    row = StockOnHand.objects.filter(
        tenant=tenant, location=location, item=item, lot=lot
    ).first()
    return row.quantity if row else None


# ---------------------------------------------------------------------------
# One transaction, one projection
# ---------------------------------------------------------------------------


def test_receiving_appends_one_move_and_moves_the_projection_by_it(tenant_a, sede_a):
    """Acceptance 2 · exactly one row and exactly +40, in one transaction."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        result = ledger.append([move(sede_a, item, 40, lot)], tenant_id=tenant_a.id)
    assert len(result.written) == 1
    assert StockMove.objects.filter(tenant=tenant_a).count() == 1
    assert held(tenant_a, sede_a, item, lot) == 40


def test_a_failure_between_the_move_and_the_projection_leaves_neither(tenant_a, sede_a):
    """The append and the projection write are one unit. Forcing a failure after
    the moves are created must leave no move **and** no projection row -- a
    ledger with a row the projection never saw is drift nobody caused."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                ledger.append([move(sede_a, item, 40, lot)], tenant_id=tenant_a.id)
                raise RuntimeError("the process died here")
        assert StockMove.objects.filter(tenant=tenant_a).count() == 0
        assert held(tenant_a, sede_a, item, lot) is None


def test_a_service_moves_nothing_at_all(tenant_a, sede_a):
    """A7 · `tracks_stock = false` writes no move and no projection row. Not a
    zero-quantity row: a row saying a service has no stock is a row somebody
    later sums."""
    service = make_item(
        tenant_a,
        name="Toma de tensión arterial",
        type=ItemType.SERVICE,
        tracks_stock=False,
        tracks_lots=False,
        tracks_expiry=False,
    )
    with pin_tenant(tenant_a.id):
        result = ledger.append([move(sede_a, service, 5)], tenant_id=tenant_a.id)
    assert result.written == []
    assert len(result.skipped) == 1
    assert StockMove.objects.filter(tenant=tenant_a).count() == 0
    assert StockOnHand.objects.filter(tenant=tenant_a).count() == 0


def test_the_projection_keys_a_lot_less_item_once(tenant_a, sede_a):
    """`NULLS NOT DISTINCT` · without it a lot-less item accumulates one
    projection row per write and every figure on the screen is the last one
    written rather than the sum."""
    item = make_item(tenant_a, name="Jabón", tracks_lots=False, tracks_expiry=False)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10)], tenant_id=tenant_a.id)
        ledger.append([move(sede_a, item, 7)], tenant_id=tenant_a.id)
    assert StockOnHand.objects.filter(tenant=tenant_a, item=item).count() == 1
    assert held(tenant_a, sede_a, item) == 17


# ---------------------------------------------------------------------------
# What the service refuses, and what it never refuses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,quantity",
    [
        (StockMoveType.SHRINKAGE, 5),
        (StockMoveType.TRANSFER_IN, -5),
        (StockMoveType.SALE, 5),
    ],
)
def test_a_move_carries_the_sign_of_its_type(tenant_a, sede_a, kind, quantity):
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id), pytest.raises(ledger.Refused):
        ledger.append(
            [move(sede_a, item, quantity, lot, kind=kind)], tenant_id=tenant_a.id
        )


def test_a_lot_tracked_item_needs_a_lot(tenant_a, sede_a):
    item = make_item(tenant_a)
    with pin_tenant(tenant_a.id), pytest.raises(ledger.Refused):
        ledger.append([move(sede_a, item, 5)], tenant_id=tenant_a.id)


def test_an_untracked_item_refuses_a_lot(tenant_a, sede_a):
    item = make_item(tenant_a, name="Jabón", tracks_lots=False, tracks_expiry=False)
    other = make_item(tenant_a, name="Otra")
    lot = make_lot(tenant_a, other)
    with pin_tenant(tenant_a.id), pytest.raises(ledger.Refused):
        ledger.append([move(sede_a, item, 5, lot)], tenant_id=tenant_a.id)


def test_a_reason_belongs_to_a_reconciling_move(tenant_a, sede_a):
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id), pytest.raises(ledger.Refused):
        ledger.append(
            [
                move(
                    sede_a,
                    item,
                    5,
                    lot,
                    kind=StockMoveType.TRANSFER_IN,
                    reason="correction",
                )
            ],
            tenant_id=tenant_a.id,
        )


def test_a_server_move_with_no_key_is_refused(tenant_a, sede_a):
    """A move nobody can deduplicate is a button that moves stock twice."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    request = ledger.Move(
        location_id=sede_a.id,
        item_id=item.id,
        lot_id=lot.id,
        quantity=5,
        type=StockMoveType.ADJUSTMENT,
        reason="correction",
    )
    with pin_tenant(tenant_a.id), pytest.raises(ledger.Refused):
        ledger.append([request], tenant_id=tenant_a.id)


# ---------------------------------------------------------------------------
# Idempotency (A5, rule 8)
# ---------------------------------------------------------------------------


def test_replaying_the_same_move_three_times_changes_nothing(tenant_a, sede_a):
    """Acceptance 6 · three attempts, one set of moves, one projection."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    client_uuid = uuid.uuid4()
    with pin_tenant(tenant_a.id):
        for _attempt in range(3):
            ledger.append(
                [move(sede_a, item, 40, lot, client_uuid=client_uuid)],
                tenant_id=tenant_a.id,
            )
    assert StockMove.objects.filter(tenant=tenant_a).count() == 1
    assert held(tenant_a, sede_a, item, lot) == 40


def test_a_server_move_is_idempotent_on_its_document_key(tenant_a, sede_a):
    """Pressing a resolution twice appends nothing (acceptance 9)."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        for _press in range(2):
            ledger.append(
                [move(sede_a, item, 12, lot, key="transfer-loss:1:1")],
                tenant_id=tenant_a.id,
            )
    assert StockMove.objects.filter(tenant=tenant_a).count() == 1
    assert held(tenant_a, sede_a, item, lot) == 12


# ---------------------------------------------------------------------------
# Negative stock is an exception, never a refusal (§5 rule 2)
# ---------------------------------------------------------------------------


def test_two_sales_of_the_last_box_are_both_accepted(tenant_a, sede_a):
    """Acceptance 5 · both stand, the projection reads −1, and exactly **one**
    conflict row names the sede, the item, the lot and both documents."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 1, lot)], tenant_id=tenant_a.id)
        result = ledger.append(
            [
                move(sede_a, item, -1, lot, kind=StockMoveType.SALE),
                move(sede_a, item, -1, lot, kind=StockMoveType.SALE),
            ],
            tenant_id=tenant_a.id,
        )
        assert len(result.written) == 2
        assert held(tenant_a, sede_a, item, lot) == -1
        rows = SyncConflict.objects.filter(
            tenant=tenant_a, type=SyncConflictType.NEGATIVE_STOCK
        )
        assert rows.count() == 1
        detail = rows.get().detail
        assert detail["item_id"] == str(item.id)
        assert detail["lot_id"] == str(lot.id)
        assert detail["quantity"] == -1
        assert len(detail["documents"].split(",")) == 2


def test_the_conflict_names_both_sales_even_when_they_arrive_apart(tenant_a, sede_a):
    """Acceptance 5 · **both sales**, and two offline tills do not push together.

    The first push takes the shelf from one to zero and raises nothing; the
    second takes it to −1 and is the only move that append can see. A row naming
    one sale sends the office looking for a discrepancy with half the evidence.
    """
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 1, lot)], tenant_id=tenant_a.id)
        first = ledger.append(
            [move(sede_a, item, -1, lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )
        assert SyncConflict.objects.count() == 0, "one to zero is not an oversell"
        second = ledger.append(
            [move(sede_a, item, -1, lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )
        row = SyncConflict.objects.get(
            tenant=tenant_a, type=SyncConflictType.NEGATIVE_STOCK
        )
    named = row.detail["documents"]
    assert str(first.written[0].id) in named
    assert str(second.written[0].id) in named


def test_the_walk_back_stops_at_the_move_that_emptied_the_shelf(tenant_a, sede_a):
    """It names what oversold the shelf, not the whole history of the lot."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 30, lot)], tenant_id=tenant_a.id)
        # Twenty ordinary sales that never take it below zero.
        for _sale in range(20):
            ledger.append(
                [move(sede_a, item, -1, lot, kind=StockMoveType.SALE)],
                tenant_id=tenant_a.id,
            )
        ledger.append(
            [move(sede_a, item, -11, lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )
        row = SyncConflict.objects.get(
            tenant=tenant_a, type=SyncConflictType.NEGATIVE_STOCK
        )
    # One move crossed zero; the twenty before it are not evidence of anything.
    assert len(row.detail["documents"].split(",")) == 1


def test_a_third_consuming_move_updates_the_standing_row(tenant_a, sede_a):
    """A queue with one line per oversold unit is a queue nobody reads."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        for _sale in range(3):
            ledger.append(
                [move(sede_a, item, -1, lot, kind=StockMoveType.SALE)],
                tenant_id=tenant_a.id,
            )
        rows = SyncConflict.objects.filter(
            tenant=tenant_a, type=SyncConflictType.NEGATIVE_STOCK
        )
        assert rows.count() == 1
        assert rows.get().detail["quantity"] == -3


def test_a_resolved_exception_is_not_reopened_but_replaced(tenant_a, sede_a):
    """`raise_conflict` keeps a closed row closed, which is right for a daily
    re-run of the same check. Stock going negative **again** after somebody
    fixed it is a new fact and gets its own line."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, item, -1, lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )
        ledger.resolve_negative(
            tenant_id=tenant_a.id, keys=[(sede_a.id, item.id, lot.id)], actor=None
        )
        assert SyncConflict.objects.filter(status=SyncConflictStatus.OPEN).count() == 0
        ledger.append(
            [move(sede_a, item, -1, lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )
        assert SyncConflict.objects.filter(status=SyncConflictStatus.OPEN).count() == 1
        assert SyncConflict.objects.count() == 2


# ---------------------------------------------------------------------------
# FEFO, and the override that outranks it (§6)
# ---------------------------------------------------------------------------


def test_fefo_orders_by_expiry_and_skips_an_expired_lot(tenant_a, sede_a):
    """The check in *Verification*: four lots, one already expired; three come
    back ascending and the expired one is absent. **Wrong when the expired lot
    comes back unnamed** -- that puts stock past its date at the head of a
    cashier's queue."""
    item = make_item(tenant_a)
    lots = [
        make_lot(tenant_a, item, code="L-1", days=-10),
        make_lot(tenant_a, item, code="L-2", days=30),
        make_lot(tenant_a, item, code="L-3", days=90),
        make_lot(tenant_a, item, code="L-4", days=200),
    ]
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, item, 10, one) for one in lots], tenant_id=tenant_a.id
        )
        queue = ledger.available_lots(
            tenant_id=tenant_a.id, location_id=sede_a.id, item_id=item.id
        )
        assert [one.lot_code for one in queue] == ["L-2", "L-3", "L-4"]

        named = ledger.available_lots(
            tenant_id=tenant_a.id,
            location_id=sede_a.id,
            item_id=item.id,
            include_expired=True,
        )
        assert named[0].lot_code == "L-1"

        head = ledger.fefo_head(
            tenant_id=tenant_a.id, location_id=sede_a.id, item_id=item.id
        )
        assert head.lot_code == "L-2"
        assert not ledger.is_override(
            tenant_id=tenant_a.id,
            location_id=sede_a.id,
            item_id=item.id,
            lot_id=lots[1].id,
        )
        assert ledger.is_override(
            tenant_id=tenant_a.id,
            location_id=sede_a.id,
            item_id=item.id,
            lot_id=lots[2].id,
        )


def test_a_lot_with_nothing_on_the_shelf_is_not_available(tenant_a, sede_a):
    item = make_item(tenant_a)
    empty = make_lot(tenant_a, item, code="L-0", days=10)
    stocked = make_lot(tenant_a, item, code="L-9", days=300)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 5, stocked)], tenant_id=tenant_a.id)
        queue = ledger.available_lots(
            tenant_id=tenant_a.id, location_id=sede_a.id, item_id=item.id
        )
    assert [one.lot_code for one in queue] == ["L-9"]
    assert empty.lot_code not in [one.lot_code for one in queue]


# ---------------------------------------------------------------------------
# The projection is disposable, and this is the proof (A3, acceptance 3)
# ---------------------------------------------------------------------------


def test_dropping_the_projection_and_rebuilding_it_changes_nothing(tenant_a, sede_a):
    """The check this stage exists for, in miniature: a database with an
    adjustment, a shrinkage, a transfer-shaped pair and a count in it.

    The diff is taken **in both directions**, because only a symmetric diff
    catches both failures -- a key present before and missing after, and a key
    present after that the ledger cannot produce.
    """
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    plain = make_item(tenant_a, name="Jabón", tracks_lots=False, tracks_expiry=False)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [
                move(sede_a, item, 100, lot),
                move(sede_a, plain, 40),
                move(sede_a, item, -8, lot, kind=StockMoveType.SHRINKAGE),
                move(sede_a, item, -20, lot, kind=StockMoveType.TRANSFER_OUT),
                move(other, item, 20, lot, kind=StockMoveType.TRANSFER_IN),
                move(
                    sede_a,
                    item,
                    -3,
                    lot,
                    kind=StockMoveType.COUNT,
                    reason="count_adjustment",
                ),
            ],
            tenant_id=tenant_a.id,
        )
        before = _snapshot(tenant_a)
        StockOnHand.objects.filter(tenant=tenant_a).delete()
        for location in (sede_a, other):
            ledger.rebuild(tenant_a.id, location.id)
        after = _snapshot(tenant_a)

    assert before - after == set()
    assert after - before == set()
    assert (sede_a.id, item.id, lot.id, 69) in before


def test_the_rebuild_keeps_a_key_that_sums_to_zero(tenant_a, sede_a):
    """A lot received and entirely sold is a real key with a real quantity of
    zero, and it is what `Quiebre` renders from. A rebuild that dropped it would
    change what the screen says."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [
                move(sede_a, item, 5, lot),
                move(sede_a, item, -5, lot, kind=StockMoveType.SALE),
            ],
            tenant_id=tenant_a.id,
        )
        StockOnHand.objects.filter(tenant=tenant_a).delete()
        ledger.rebuild(tenant_a.id, sede_a.id)
    assert held(tenant_a, sede_a, item, lot) == 0


def test_verify_finds_a_row_written_outside_the_service(tenant_a, sede_a):
    """Acceptance 4 · zero drift on a healthy database, and the exact key named
    when a `stock_moves` row is inserted by hand.

    A verify that still reports zero is comparing the projection with itself.
    """
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
        assert ledger.verify(tenant_a.id, sede_a.id) == {}

        StockMove.objects.create(
            tenant=tenant_a,
            location=sede_a,
            item=item,
            lot=lot,
            quantity=7,
            type=StockMoveType.ADJUSTMENT,
            reason="correction",
            client_uuid=uuid.uuid4(),
        )
        drift = ledger.verify(tenant_a.id, sede_a.id)
    assert drift == {(item.id, lot.id): {"ledger": 17, "projection": 10}}


def _snapshot(tenant):
    return {
        (row.location_id, row.item_id, row.lot_id, row.quantity)
        for row in StockOnHand.objects.filter(tenant=tenant)
    }


# ---------------------------------------------------------------------------
# Append-only is a grant, not a convention
# ---------------------------------------------------------------------------


def test_the_runtime_role_cannot_update_or_delete_a_move(
    tenant_a, sede_a, as_runtime_role
):
    """Acceptance 1 · **eleven stage documents saying `append-only` is a
    convention; a `REVOKE` is a property.**

    A correction to a movement fails at the database rather than succeeding
    quietly and corrupting the ledger and the projection consistently -- which
    is the one failure the rebuild check cannot see, because it makes both sides
    agree on the same wrong number.
    """
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
        as_runtime_role()
        with connection.cursor() as cursor:
            for statement in (
                "UPDATE stock_moves SET quantity = 1",
                "DELETE FROM stock_moves",
            ):
                with pytest.raises(Exception) as refusal:
                    with transaction.atomic():
                        cursor.execute(statement)
                assert "permission denied" in str(refusal.value).lower()


def test_the_unique_client_key_is_a_database_constraint(tenant_a, sede_a):
    """A5 · deduplication is a unique index and nothing more."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    key = uuid.uuid4()
    with pin_tenant(tenant_a.id):
        StockMove.objects.create(
            tenant=tenant_a,
            location=sede_a,
            item=item,
            lot=lot,
            quantity=1,
            type=StockMoveType.ADJUSTMENT,
            reason="correction",
            client_uuid=key,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                StockMove.objects.create(
                    tenant=tenant_a,
                    location=sede_a,
                    item=item,
                    lot=lot,
                    quantity=1,
                    type=StockMoveType.ADJUSTMENT,
                    reason="correction",
                    client_uuid=key,
                )


# ---------------------------------------------------------------------------
# The grep the gate asks for, as a test rather than as a habit
# ---------------------------------------------------------------------------

#: Where a write to the ledger or the projection could hide. `core/migrations`
#: is included on purpose: *Verification* names migrations and jobs alongside
#: application code, and a data migration that "corrected" a quantity is the
#: exact defect the rebuild check cannot see.
SOURCE_ROOTS = ("core", "botica")

#: The one module allowed to write `stock_on_hand` (ownership.md rule 7), and
#: the two migrations that create and secure the tables.
LEDGER_MODULE = "core/inventory/ledger.py"
SCHEMA_FILES = (
    "core/migrations/0009_inventory.py",
    "core/migrations/0010_inventory_rls.py",
)


def _sources():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    for directory in SOURCE_ROOTS:
        for path in (root / directory).rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if relative.startswith("core/tests/"):
                continue
            yield relative, path.read_text(encoding="utf-8")


def test_no_application_path_updates_or_deletes_a_movement():
    """The gate's own grep · **a hit is a path the rebuild check cannot see**,
    because it corrupts the ledger and the projection consistently (rule 7, A3).

    The database refuses it too -- migration 0010 revokes both grants -- and
    this is the half that catches it in review rather than in production.
    """
    import re

    offending = re.compile(
        r"StockMove\.objects[^\n]*\.(update|delete)\(|"
        r"(?i:UPDATE|DELETE\s+FROM)\s+stock_moves"
    )
    hits = [
        (name, line)
        for name, source in _sources()
        for line in source.splitlines()
        if offending.search(line) and name not in SCHEMA_FILES
    ]
    assert hits == []


def test_the_projection_is_written_from_exactly_one_module():
    """Rule 7 · `stock_on_hand` is maintained by the ledger service and by
    nothing else. A second writer is a second definition of what stock is."""
    import re

    writes = re.compile(
        r"StockOnHand\.objects[^\n]*\.(create|update|bulk_create)\(|"
        r"(?i:INSERT\s+INTO|UPDATE)\s+stock_on_hand"
    )
    hits = [
        name
        for name, source in _sources()
        for line in source.splitlines()
        if writes.search(line)
        if name not in SCHEMA_FILES
    ]
    assert set(hits) <= {LEDGER_MODULE}


def _statements(path):
    """A migration with its module docstring and its comments removed.

    Only the module docstring, and deliberately: the SQL these files run is
    itself a triple-quoted string, so a blanket strip of every one of them would
    delete the `CREATE TYPE` this check is looking for. What has to go is the
    prose -- both files **say** `ALTER TYPE` in explaining why they contain none,
    and a grep that could not tell an argument from a statement would make the
    two mutually exclusive.
    """
    import ast

    source = path.read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source))
    if docstring:
        source = source.replace(docstring, "", 1)
    return re.sub(r"#[^\n]*", "", source)


def test_this_stages_migrations_create_one_enum_and_alter_none():
    """The gate · **no `ALTER TYPE` at all.** An enum value migrated by the
    stage that writes it has to land before the stage that reads it, which fails
    a clean build in dependency order rather than at the write."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    for name in SCHEMA_FILES:
        # The prose is read past: both files **say** `ALTER TYPE` in explaining
        # why they contain none, and a grep that could not tell an argument from
        # a statement would make the two mutually exclusive.
        source = _statements(root / name)
        assert not re.search(r"\bALTER\s+TYPE\b", source)
    created = _statements(root / SCHEMA_FILES[0])
    assert created.count("CREATE TYPE") == 1
    assert "CREATE TYPE stock_move_type" in created
    for value in (
        "receipt",
        "sale",
        "customer_return",
        "supplier_return",
        "transfer_out",
        "transfer_in",
        "adjustment",
        "shrinkage",
        "expiry",
        "count",
    ):
        assert f"'{value}'" in created


# ---------------------------------------------------------------------------
# The lock is per sede, and that is the whole of "20 short locks"
# ---------------------------------------------------------------------------


# **Real transactions, not the suite's usual rollback wrapper.** An advisory
# lock is released by a transaction ending, and every other test in this file
# runs inside one transaction pytest rolls back at the end -- under which the
# command's own pins are savepoints and every lock in the test is still held.
# The property under test is exactly "the transaction ended", so it needs a
# database that commits.
@pytest.mark.django_db(transaction=True)
def test_the_rebuild_command_holds_one_sede_lock_at_a_time(tenant_a, sede_a):
    """**A twenty-sede rebuild is twenty short locks, not one long one.**

    `pg_advisory_xact_lock` releases when the *transaction* ends. Under one
    enclosing pin -- which is what `TenantCommand` gives every other command --
    `ledger.rebuild`'s `atomic()` block is only a savepoint, so each sede's lock
    survives until the command exits and the last sede's rebuild has the whole
    network locked behind it. A counter at the first sede would then block until
    the twentieth finished, which is the one thing the stage document says
    running per location is for.

    The property is counted rather than argued: after each sede, the backend
    running the command holds exactly one advisory lock.
    """
    from django.core.management import call_command

    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a, name="Jabón", tracks_lots=False, tracks_expiry=False)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10)], tenant_id=tenant_a.id)
        ledger.append([move(other, item, 4)], tenant_id=tenant_a.id)

    held = []
    original = ledger.rebuild

    def counting(tenant_id, location_id):
        report = original(tenant_id, location_id)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid()"
            )
            held.append(cursor.fetchone()[0])
        return report

    ledger.rebuild = counting
    try:
        call_command("rebuild_stock_projection", tenant=str(tenant_a.id))
    finally:
        ledger.rebuild = original

    assert held == [1, 1], (
        f"the command accumulated locks across sedes: {held}. Each sede must be "
        "its own transaction, or the whole network is locked for the length of "
        "the run."
    )


# ---------------------------------------------------------------------------
# FEFO, stamped rather than derived (§6, acceptance 11)
# ---------------------------------------------------------------------------


def test_a_consuming_move_stamps_whether_it_disagreed_with_fefo(tenant_a, sede_a):
    """Acceptance 11 · **the head is `false`, the second lot is `true`.**

    Stamped by the service, so every consuming path in the product carries it
    rather than whichever caller remembered -- and read before the projection
    moves, because afterwards it is unrecoverable.
    """
    item = make_item(tenant_a)
    head = make_lot(tenant_a, item, code="F-1", days=30)
    later = make_lot(tenant_a, item, code="F-2", days=300)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, item, 20, head), move(sede_a, item, 20, later)],
            tenant_id=tenant_a.id,
        )
        taken_head = ledger.append(
            [move(sede_a, item, -5, head, kind=StockMoveType.SHRINKAGE)],
            tenant_id=tenant_a.id,
        )
        taken_later = ledger.append(
            [move(sede_a, item, -5, later, kind=StockMoveType.SHRINKAGE)],
            tenant_id=tenant_a.id,
        )
    assert taken_head.written[0].fefo_override is False
    assert taken_later.written[0].fefo_override is True


def test_writing_off_an_expired_lot_is_not_an_override(tenant_a, sede_a):
    """The queue never offered that lot, so taking it is not a disagreement
    with the queue -- and stamping `vencimiento` as an override would mark the
    one operation that is unambiguously right."""
    item = make_item(tenant_a)
    expired = make_lot(tenant_a, item, code="F-0", days=-5)
    fresh = make_lot(tenant_a, item, code="F-9", days=300)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, item, 12, expired), move(sede_a, item, 12, fresh)],
            tenant_id=tenant_a.id,
        )
        result = ledger.append(
            [move(sede_a, item, -12, expired, kind=StockMoveType.EXPIRY)],
            tenant_id=tenant_a.id,
        )
    assert result.written[0].fefo_override is False


def test_a_caller_that_already_knows_is_believed(tenant_a, sede_a):
    """S4's counter showed the queue and watched a cashier pick past its head,
    so it states the answer rather than having it re-derived."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item, code="F-5")
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
        result = ledger.append(
            [
                move(
                    sede_a,
                    item,
                    -1,
                    lot,
                    kind=StockMoveType.SHRINKAGE,
                    fefo_override=True,
                )
            ],
            tenant_id=tenant_a.id,
        )
    assert result.written[0].fefo_override is True


def test_an_addition_never_disagreed_with_a_queue(tenant_a, sede_a):
    item = make_item(tenant_a)
    first = make_lot(tenant_a, item, code="F-A", days=30)
    second = make_lot(tenant_a, item, code="F-B", days=300)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 5, first)], tenant_id=tenant_a.id)
        result = ledger.append([move(sede_a, item, 5, second)], tenant_id=tenant_a.id)
    assert result.written[0].fefo_override is False


# ---------------------------------------------------------------------------
# The other-location set enters the till's predicate on a crossing
# ---------------------------------------------------------------------------


def test_crossing_into_trouble_moves_the_other_sedes_rows(tenant_a, sede_a):
    """A row that enters a collection without being written can never be served.

    The other-location set's membership is derived from *this* sede's shortage,
    so the rows that must now reach the till are rows at sedes nobody wrote --
    behind the device's cursor, and never in a delta page. The crossing is what
    moves them.
    """
    from core.models import StockPolicy

    other = make_location(tenant_a, code="S02", name="Suba")
    item = make_item(tenant_a)
    here = make_lot(tenant_a, item, code="C-1")
    there = make_lot(tenant_a, item, code="C-2")
    with pin_tenant(tenant_a.id):
        StockPolicy.objects.create(
            tenant=tenant_a, item=item, location=None, reorder_point=5
        )
        ledger.append(
            [move(sede_a, item, 20, here), move(other, item, 30, there)],
            tenant_id=tenant_a.id,
        )
        elsewhere = StockOnHand.objects.get(
            tenant=tenant_a, location=other, item=item, lot=there
        )
        before, lot_before = (
            elsewhere.updated_at,
            Lot.objects.get(id=there.id).updated_at,
        )

        # Still above the reorder point: no crossing, nothing touched.
        ledger.append(
            [move(sede_a, item, -10, here, kind=StockMoveType.SHRINKAGE)],
            tenant_id=tenant_a.id,
        )
        elsewhere.refresh_from_db()
        assert elsewhere.updated_at == before

        # 10 → 4, at or below the reorder point: the crossing.
        ledger.append(
            [move(sede_a, item, -6, here, kind=StockMoveType.SHRINKAGE)],
            tenant_id=tenant_a.id,
        )
        elsewhere.refresh_from_db()
        assert elsewhere.updated_at > before
        assert Lot.objects.get(id=there.id).updated_at > lot_before


def test_a_sede_already_short_does_not_restamp_the_network(tenant_a, sede_a):
    """Re-stamping on every later sale would push the other sedes' stock past
    every till's cursor on every ticket."""
    from core.models import StockPolicy

    other = make_location(tenant_a, code="S03", name="Kennedy")
    item = make_item(tenant_a)
    here = make_lot(tenant_a, item, code="D-1")
    there = make_lot(tenant_a, item, code="D-2")
    with pin_tenant(tenant_a.id):
        StockPolicy.objects.create(
            tenant=tenant_a, item=item, location=None, reorder_point=5
        )
        ledger.append(
            [move(sede_a, item, 6, here), move(other, item, 30, there)],
            tenant_id=tenant_a.id,
        )
        ledger.append(
            [move(sede_a, item, -2, here, kind=StockMoveType.SHRINKAGE)],
            tenant_id=tenant_a.id,
        )
        elsewhere = StockOnHand.objects.get(
            tenant=tenant_a, location=other, item=item, lot=there
        )
        after_crossing = elsewhere.updated_at
        ledger.append(
            [move(sede_a, item, -1, here, kind=StockMoveType.SHRINKAGE)],
            tenant_id=tenant_a.id,
        )
        elsewhere.refresh_from_db()
    assert elsewhere.updated_at == after_crossing
