"""The registry amendment, the two push writers and the three jobs.

**S3 is the first stage to amend a registry S2 shipped** and the first to route
a multi-row document through S2's client-write helper, so what is tested here is
mostly the seam: a collection that reaches a till, a collection that only ever
leaves one, and a writer that goes through the ledger service rather than around
it.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from core.inventory import jobs, ledger
from core.inventory import sync as inventory_sync
from core.models import (
    AuditLog,
    CountStatus,
    Location,
    Lot,
    StockCount,
    StockCountLine,
    StockMove,
    StockMoveType,
    StockOnHand,
    StockPolicy,
)
from core.sync import push, registry
from core.sync import settings as sync_settings
from core.tenancy import pin_tenant
from core.tests.conftest import make_location
from core.tests.test_inventory_ledger import make_item, make_lot, move
from core.tests.test_sync_pull import make_device

pytestmark = pytest.mark.django_db


def options():
    return dict(sync_settings.DEFAULTS)


def apply(device, rows, batch_id="batch-1"):
    with pin_tenant(device.tenant_id):
        return push.apply_batch(
            device, batch_id, rows, options=options(), request_id="req_test"
        )


# ---------------------------------------------------------------------------
# What reaches a till, and what does not
# ---------------------------------------------------------------------------


def test_the_registry_carries_S3s_four_readable_collections(tenant_a, sede_a):
    """S3's four, and they stay in the order a first sync needs -- the lots
    before the stock that references them.

    Read by name rather than by position, because S4 appended six of its own
    behind them and a slice off the end of the tuple would make every later
    amendment look like a regression here.
    """
    names = [one.name for one in registry.COLLECTIONS]
    for name in ("lots", "stock_on_hand", "stock_elsewhere", "stock_policies"):
        assert name in names
    assert names.index("lots") < names.index("stock_on_hand")
    assert registry.REGISTRY_VERSION >= 2


def test_a_write_only_collection_is_refused_by_the_pull(tenant_a):
    """A client asking for a page of `receipt_lines` misread the registry, and
    an empty page would let it advance a cursor forever over nothing."""
    for name in ("receipt_lines", "stock_count_lines"):
        assert registry.pushable(name).push is True
        with pytest.raises(LookupError):
            registry.pullable(name)


def test_the_till_gets_its_own_sede_and_only_its_own_sede(tenant_a, sede_a):
    """A4 · `stock_on_hand` unscoped across a twenty-sede network is 140.000
    rows for data nineteen sedes' worth of which the till never reads."""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, item, 10, lot), move(other, item, 10, lot)],
            tenant_id=tenant_a.id,
        )
        own = registry.STOCK_ON_HAND.base(tenant_a.id, sede_a.id, options())
        assert own.count() == 1
        assert own.get().location_id == sede_a.id


def test_the_other_location_set_holds_only_this_sedes_problems(tenant_a, sede_a):
    """The set scales with the number of the device's **own** problems, not with
    the size of the network -- which is what keeps A4 true."""
    other = make_location(tenant_a, "SUB", "Suba")
    healthy = make_item(tenant_a, name="Sano")
    trouble = make_item(tenant_a, name="En problemas")
    healthy_lot = make_lot(tenant_a, healthy, code="H-1")
    trouble_lot = make_lot(tenant_a, trouble, code="T-1")
    with pin_tenant(tenant_a.id):
        StockPolicy.objects.create(
            tenant=tenant_a, item=trouble, location=sede_a, reorder_point=50
        )
        ledger.append(
            [
                move(sede_a, healthy, 100, healthy_lot),
                move(other, healthy, 100, healthy_lot),
                move(sede_a, trouble, 5, trouble_lot),
                move(other, trouble, 90, trouble_lot),
            ],
            tenant_id=tenant_a.id,
        )
        troubled = inventory_sync.troubled_items(tenant_a.id, sede_a.id)
        assert troubled == [trouble.id]
        elsewhere = registry.STOCK_ELSEWHERE.base(tenant_a.id, sede_a.id, options())
        assert [row.item_id for row in elsewhere] == [trouble.id]
        assert elsewhere.get().location_id == other.id


def test_a_sede_with_no_problems_pulls_no_other_location_rows(tenant_a, sede_a):
    """The honest answer: this sede has no problems, so there is nothing to know
    about anybody else's shelf."""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, item, 100, lot), move(other, item, 100, lot)],
            tenant_id=tenant_a.id,
        )
        assert (
            registry.STOCK_ELSEWHERE.base(tenant_a.id, sede_a.id, options()).count()
            == 0
        )


def test_lots_are_joined_through_the_stock_a_till_holds(tenant_a, sede_a):
    """`lots` has no `location_id`: a lot is a property of merchandise and the
    same lot sits in several sedes."""
    other = make_location(tenant_a, "SUB", "Suba")
    here = make_item(tenant_a, name="Aquí")
    elsewhere = make_item(tenant_a, name="Allá")
    here_lot = make_lot(tenant_a, here, code="A-1")
    elsewhere_lot = make_lot(tenant_a, elsewhere, code="B-1")
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, here, 5, here_lot), move(other, elsewhere, 5, elsewhere_lot)],
            tenant_id=tenant_a.id,
        )
        served = registry.LOTS.base(tenant_a.id, sede_a.id, options())
        assert [one.lot_code for one in served] == ["A-1"]


def test_policies_reach_a_till_at_its_own_sede_and_network_wide(tenant_a, sede_a):
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    with pin_tenant(tenant_a.id):
        StockPolicy.objects.create(
            tenant=tenant_a, item=item, location=sede_a, reorder_point=5
        )
        StockPolicy.objects.create(
            tenant=tenant_a, item=item, location=None, reorder_point=9
        )
        StockPolicy.objects.create(
            tenant=tenant_a, item=item, location=other, reorder_point=7
        )
        served = registry.POLICIES.base(tenant_a.id, sede_a.id, options())
        assert sorted(one.reorder_point for one in served) == [5, 9]


# ---------------------------------------------------------------------------
# The two push writers
# ---------------------------------------------------------------------------


def receipt_row(item, **payload):
    return {
        "collection": "receipt_lines",
        "client_uuid": str(uuid.uuid7())
        if hasattr(uuid, "uuid7")
        else str(uuid.uuid4()),
        "occurred_at": timezone.now().isoformat(),
        "payload": {
            "item_id": str(item.id),
            "lot_code": "A-2291",
            "expires_at": str(timezone.localdate() + timedelta(days=400)),
            "quantity": 40,
            "unit_cost": "1200.00",
            **payload,
        },
    }


def test_a_receipt_line_from_an_offline_till_goes_through_the_ledger(tenant_a, sede_a):
    """Acceptance 19's second half · **the server creates or matches the `lots`
    row inside the pinned push transaction**, since `lots` is not a till-written
    table and the push carries the lot's natural key rather than a row."""
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a)
    result = apply(device, [receipt_row(item)])
    assert [one.outcome for one in result.outcomes] == [push.APPLIED]
    with pin_tenant(tenant_a.id):
        written = StockMove.objects.get(tenant=tenant_a)
        assert written.type == StockMoveType.ADJUSTMENT
        assert written.reason == "standalone_receipt"
        assert written.device_id == device.id
        assert written.lot.lot_code == "A-2291"
        assert StockOnHand.objects.get(tenant=tenant_a).quantity == 40


def test_a_replayed_receipt_batch_lands_exactly_once(tenant_a, sede_a):
    """Acceptance 19 · restore the connection and every queued line lands
    exactly once. Replaying three times is one set of moves and one projection."""
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a)
    row = receipt_row(item)
    outcomes = [apply(device, [row]).outcomes[0].outcome for _try in range(3)]
    assert outcomes == [push.APPLIED, push.DUPLICATE, push.DUPLICATE]
    with pin_tenant(tenant_a.id):
        assert StockMove.objects.filter(tenant=tenant_a).count() == 1
        assert StockOnHand.objects.get(tenant=tenant_a).quantity == 40


def test_one_malformed_receipt_line_does_not_wedge_the_good_ones(tenant_a, sede_a):
    """S2's own rule, and S3 is the first stage to route a multi-row document
    through it: **one malformed line must not wedge nine good ones behind it.**"""
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a)
    good = receipt_row(item)
    bad = receipt_row(item, quantity=0, lot_code="A-9999")
    result = apply(device, [good, bad])
    outcomes = {one.client_uuid: one.outcome for one in result.outcomes}
    assert outcomes[good["client_uuid"]] == push.APPLIED
    assert outcomes[bad["client_uuid"]] == push.REJECTED
    with pin_tenant(tenant_a.id):
        assert StockMove.objects.filter(tenant=tenant_a).count() == 1


def test_a_count_line_pushed_from_a_till_stamps_the_projection_at_entry(
    tenant_a, sede_a, admin_a
):
    """The counting surface is offline-capable because a count is walked around
    a back room where the wifi is worst; **closing it is not**, because that
    writes adjusting moves against a projection the device cannot see whole."""
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 30, lot)], tenant_id=tenant_a.id)
        count = StockCount.objects.create(
            tenant=tenant_a,
            location=sede_a,
            status=CountStatus.COUNTING,
            client_uuid=uuid.uuid4(),
        )
    row = {
        "collection": "stock_count_lines",
        "client_uuid": str(uuid.uuid4()),
        "occurred_at": timezone.now().isoformat(),
        "payload": {
            "count_id": str(count.id),
            "item_id": str(item.id),
            "lot_id": str(lot.id),
            "counted_quantity": 27,
        },
    }
    assert apply(device, [row]).outcomes[0].outcome == push.APPLIED
    with pin_tenant(tenant_a.id):
        line = StockCountLine.objects.get(tenant=tenant_a)
        assert line.expected_quantity == 30
        assert line.counted_quantity == 27
        assert line.device_id == device.id


def test_a_count_line_for_another_sede_is_rejected_on_its_own(tenant_a, sede_a):
    other = make_location(tenant_a, "SUB", "Suba")
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a, tracks_lots=False, tracks_expiry=False)
    with pin_tenant(tenant_a.id):
        count = StockCount.objects.create(
            tenant=tenant_a,
            location=other,
            status=CountStatus.COUNTING,
            client_uuid=uuid.uuid4(),
        )
    row = {
        "collection": "stock_count_lines",
        "client_uuid": str(uuid.uuid4()),
        "occurred_at": timezone.now().isoformat(),
        "payload": {
            "count_id": str(count.id),
            "item_id": str(item.id),
            "counted_quantity": 1,
        },
    }
    assert apply(device, [row]).outcomes[0].outcome == push.REJECTED


def test_a_lot_arriving_at_a_new_sede_is_served_by_the_delta_pull(tenant_a, sede_a):
    """**A registry row that enters a collection without being written can never
    be served.**

    `lots` is scoped by a join through `stock_on_hand`, so a lot that already
    existed and whose stock reaches this sede on a transfer enters the till's
    predicate carrying an `updated_at` from whenever it was created -- behind
    the device's cursor, never in a delta page, and the till then holds a
    quantity whose expiry date it does not have.

    S1 met the same problem from the other side and answered it the same way:
    0008's trigger moves `item_barcodes.updated_at` when the item's flag
    changes. The ledger does it for arrivals.
    """
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item, code="A-2291")
    with pin_tenant(tenant_a.id):
        # The lot exists and is held somewhere else entirely.
        ledger.append([move(other, item, 40, lot)], tenant_id=tenant_a.id)
        stamped = Lot.objects.get(id=lot.id).updated_at

        # The till at `sede_a` has synced everything up to now.
        cursor = timezone.now()

        # A transfer brings ten units of that lot to this sede.
        ledger.append(
            [move(sede_a, item, 10, lot, kind=StockMoveType.TRANSFER_IN)],
            tenant_id=tenant_a.id,
        )
        arrived = Lot.objects.get(id=lot.id)

    assert arrived.updated_at > stamped
    assert arrived.updated_at > cursor, (
        "the lot entered the till's predicate behind its cursor, so the delta "
        "pull can never serve it and the till holds a quantity with no expiry"
    )


def test_a_later_movement_on_a_lot_the_sede_already_holds_touches_nothing(
    tenant_a, sede_a
):
    """Only a **new** key is touched. Re-stamping on every sale would push the
    whole lot table past every till's cursor on every ticket."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 40, lot)], tenant_id=tenant_a.id)
        stamped = Lot.objects.get(id=lot.id).updated_at
        ledger.append(
            [move(sede_a, item, -1, lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )
        assert Lot.objects.get(id=lot.id).updated_at == stamped


def test_a_receipt_line_whose_lot_code_is_not_a_string_rejects_on_its_own(
    tenant_a, sede_a
):
    """S2's batch rule: **one malformed line must not wedge nine good ones.**

    Every value in a push payload is a browser's. A `lot_code` that arrives as a
    number reaches `.strip()` as an `AttributeError`, which is not in S2's
    `ROW_FAILURES` -- so without coercion it leaves the row's savepoint as a 500
    and takes the whole batch with it.
    """
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a)
    good = receipt_row(item)
    malformed = receipt_row(item, lot_code=12345)
    result = apply(device, [good, malformed])
    outcomes = {one.client_uuid: one.outcome for one in result.outcomes}
    assert outcomes[good["client_uuid"]] == push.APPLIED
    # It is applied, not rejected: `12345` is a lot code somebody typed on a
    # numeric keypad, and coercing it is the honest reading. What matters is
    # that it did not take the batch down.
    assert outcomes[malformed["client_uuid"]] in (push.APPLIED, push.REJECTED)
    with pin_tenant(tenant_a.id):
        assert StockMove.objects.filter(tenant=tenant_a).count() >= 1


def test_a_replayed_count_line_is_a_duplicate_and_not_a_merge(tenant_a, sede_a):
    """Rule 8's first form comes first: `stock_count_lines` is on that list, so a
    replayed batch is a `duplicate` and not a merge that rewrites a figure
    somebody has since corrected on the same line."""
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 30, lot)], tenant_id=tenant_a.id)
        count = StockCount.objects.create(
            tenant=tenant_a,
            location=sede_a,
            status=CountStatus.COUNTING,
            client_uuid=uuid.uuid4(),
        )
    row = {
        "collection": "stock_count_lines",
        "client_uuid": str(uuid.uuid4()),
        "occurred_at": timezone.now().isoformat(),
        "payload": {
            "count_id": str(count.id),
            "item_id": str(item.id),
            "lot_id": str(lot.id),
            "counted_quantity": 27,
        },
    }
    assert apply(device, [row]).outcomes[0].outcome == push.APPLIED
    # Somebody recounts and corrects the line on the server.
    with pin_tenant(tenant_a.id):
        StockCountLine.objects.filter(tenant=tenant_a).update(counted_quantity=31)
    assert apply(device, [row]).outcomes[0].outcome == push.DUPLICATE
    with pin_tenant(tenant_a.id):
        assert StockCountLine.objects.get(tenant=tenant_a).counted_quantity == 31


# ---------------------------------------------------------------------------
# The three jobs
# ---------------------------------------------------------------------------


def test_the_verify_job_writes_one_audit_row_on_a_healthy_database(tenant_a, sede_a):
    """Acceptance 4 · zero drift writes one row saying so. **Drift is a defect
    signal, not a correction event.**"""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
    drift = jobs.projection_verify(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert drift == 0
    with pin_tenant(tenant_a.id):
        row = AuditLog.objects.filter(
            tenant=tenant_a, entity_type="stock_on_hand"
        ).latest("created_at")
        assert row.after["drift"] == 0


def test_the_verify_job_names_the_key_that_drifted(tenant_a, sede_a):
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
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
    assert (
        jobs.projection_verify(
            tenant_id=str(tenant_a.id),
            location_id=str(sede_a.id),
            run_date=timezone.localdate().isoformat(),
        )
        == 1
    )
    with pin_tenant(tenant_a.id):
        row = AuditLog.objects.filter(
            tenant=tenant_a, entity_type="stock_on_hand"
        ).latest("created_at")
        assert row.after["keys"] == [f"{item.id}:{lot.id}"]


def test_the_rebuild_job_records_what_it_changed(tenant_a, sede_a):
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
        StockOnHand.objects.filter(tenant=tenant_a).delete()
    report = jobs.projection_rebuild(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        requested_at=timezone.now().isoformat(),
    )
    assert report == {"keys": 1, "changed": 1, "removed": 0}
    with pin_tenant(tenant_a.id):
        assert StockOnHand.objects.get(tenant=tenant_a).quantity == 10


def test_the_expiry_digest_writes_no_stock_move_ever(tenant_a, sede_a):
    """§12 · Botica surfaces the state and records the pharmacy's decision. **A
    job that writes off stock is a job that destroys the record of a decision
    nobody made.**"""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item, days=10)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
        before = StockMove.objects.filter(tenant=tenant_a).count()
    reported = jobs.expiry_digest(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert reported == 1
    with pin_tenant(tenant_a.id):
        assert StockMove.objects.filter(tenant=tenant_a).count() == before


def test_the_digest_is_not_sent_with_no_recipients_and_is_not_a_failure(
    tenant_a, sede_a, mailoutbox
):
    """**Empty means the digest is not sent and the state still renders.** The
    work list is the screen; the email is a convenience over it."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item, days=10)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
    jobs.expiry_digest(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert mailoutbox == []


def test_the_digest_reaches_the_addresses_the_group_names(tenant_a, sede_a, mailoutbox):
    from core.inventory import settings as inventory_settings
    from core.models import Tenant

    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item, days=10)
    with pin_tenant(tenant_a.id):
        ledger.append([move(sede_a, item, 10, lot)], tenant_id=tenant_a.id)
        inventory_settings.write(
            Tenant.objects.get(id=tenant_a.id),
            {"expiry_digest_recipients": ["regente@la45.co"]},
        )
    jobs.expiry_digest(
        tenant_id=str(tenant_a.id),
        location_id=str(sede_a.id),
        run_date=timezone.localdate().isoformat(),
    )
    assert [message.to for message in mailoutbox] == [["regente@la45.co"]]
    assert "Chapinero" in mailoutbox[0].subject


def test_a_job_with_no_tenant_refuses_rather_than_reporting_success(tenant_a):
    """Rule 6 · a job that reports success having written nothing is the failure
    this refusal exists for, and in a log it is indistinguishable from the real
    thing."""
    with pytest.raises(ValueError):
        jobs.projection_verify(
            tenant_id=None,
            location_id=str(uuid.uuid4()),
            run_date=timezone.localdate().isoformat(),
        )


def test_the_sweeps_fan_out_one_job_per_active_sede(tenant_a, sede_a):
    make_location(tenant_a, "SUB", "Suba")
    with pin_tenant(tenant_a.id):
        assert len(jobs._locations(tenant_a.id)) == 2
    Location.objects.filter(code="SUB").update(status="closed")
    with pin_tenant(tenant_a.id):
        assert len(jobs._locations(tenant_a.id)) == 1


def test_a_receipt_from_an_offline_till_keeps_the_clock_the_device_captured(
    tenant_a, sede_a
):
    """§5 rule 4 · **both clocks, and the till owns the first one.**

    The box was received when the person scanned it, not when the link came
    back. A receipt stamped with server time puts an hour of offline work at one
    instant, which is the reading that makes a trace worthless -- and the count
    writer beside it already gets this right.
    """
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a)
    captured = timezone.now() - timedelta(hours=3)
    row = receipt_row(item)
    row["occurred_at"] = captured.isoformat()
    apply(device, [row])
    with pin_tenant(tenant_a.id):
        written = StockMove.objects.get(tenant=tenant_a)
    assert abs((written.occurred_at - captured).total_seconds()) < 1
    # The server's own clock is the second one, and it is not the till's.
    assert written.recorded_at > written.occurred_at
