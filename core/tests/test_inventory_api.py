"""S3's endpoints: the documents, the derivation, the roles and the trail.

Every test here is one line of *Acceptance* or one row of *Verification*, and
the docstrings name which. The ones that matter most are the arithmetic:
transfers that end at `N − 12` rather than `N − 24`, and a count that ends at
`counted − sales` rather than at `counted`. Both are wrong in ways where every
individual move looks correct.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.inventory import ledger, settings as inventory_settings
from core.models import (
    AuditLog,
    StockCountLine,
    StockMove,
    StockMoveType,
    StockOnHand,
    StockPolicy,
    SyncConflict,
    SyncConflictStatus,
    SyncConflictType,
    Tenant,
    Transfer,
    TransferStatus,
)
from core.tenancy import pin_tenant
from core.tests.conftest import make_location
from core.tests.test_inventory_ledger import make_item, make_lot, move

pytestmark = pytest.mark.django_db


def api(client_as, user):
    return client_as(user)


def post(client, path, body):
    return client.post(path, body, content_type="application/json")


def patch(client, path, body):
    return client.patch(path, body, content_type="application/json")


def put(client, path, body):
    return client.put(path, body, content_type="application/json")


def stock(tenant, location, item, lot=None):
    row = StockOnHand.objects.filter(
        tenant=tenant, location=location, item=item, lot=lot
    ).first()
    return row.quantity if row else 0


def seed_stock(tenant, location, item, lot, quantity):
    with pin_tenant(tenant.id):
        ledger.append([move(location, item, quantity, lot)], tenant_id=tenant.id)


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


def test_a_receipt_creates_the_lot_and_appends_one_move_per_line(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 2, and the ledger's own ownership rule: opening stock and a
    standalone receipt are **`adjustment` rows and not `receipt` rows**, because
    `receipt` is caused by S6."""
    item = make_item(tenant_a)
    client = api(client_as, admin_a)
    response = post(
        client,
        "/api/receipts",
        {
            "location_id": str(sede_a.id),
            "reason": "standalone_receipt",
            "lines": [
                {
                    "item_id": str(item.id),
                    "lot_code": "A-2291",
                    "expires_at": str(timezone.localdate() + timedelta(days=500)),
                    "quantity": 40,
                    "unit_cost": "1200.00",
                }
            ],
        },
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["lines_written"] == 1
    assert body["moves"][0]["type"] == StockMoveType.ADJUSTMENT
    assert body["moves"][0]["reason"] == "standalone_receipt"
    with pin_tenant(tenant_a.id):
        assert StockOnHand.objects.get(tenant=tenant_a, item=item).quantity == 40


def test_a_lot_code_whose_stored_expiry_disagrees_is_refused(
    client_as, tenant_a, sede_a, admin_a
):
    """The field-scope error the receiving surface renders. **The line is
    refused rather than accepted with either date**: an expiry mismatch on the
    same lot code is a typo the person holding the box fixes in three seconds,
    and silently keeping either value puts a wrong date at the head of the FEFO
    queue."""
    item = make_item(tenant_a)
    make_lot(tenant_a, item, code="A-2291", days=400)
    client = api(client_as, admin_a)
    response = post(
        client,
        "/api/receipts",
        {
            "location_id": str(sede_a.id),
            "lines": [
                {
                    "item_id": str(item.id),
                    "lot_code": "A-2291",
                    "expires_at": str(timezone.localdate() + timedelta(days=90)),
                    "quantity": 5,
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "A-2291" in response.json()["detail"]


def test_receiving_writes_base_units_and_never_packs(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 22 · twelve packs of a splittable blister of thirty is a move
    of 360. **Wrong when it is 12** -- a projection reading a thirtieth of the
    truth, correctable only by another move."""
    item = make_item(
        tenant_a,
        name="Blíster de 30",
        unit="tableta",
        splittable=True,
        units_per_pack=30,
    )
    client = api(client_as, admin_a)
    response = post(
        client,
        "/api/receipts",
        {
            "location_id": str(sede_a.id),
            "lines": [
                {
                    "item_id": str(item.id),
                    "lot_code": "B-1",
                    "expires_at": str(timezone.localdate() + timedelta(days=500)),
                    # 12 cajas × 30 tabletas, converted by the surface that took
                    # the entry and shown as `12 cajas · 360 unidades`.
                    "quantity": 12 * 30,
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["moves"][0]["quantity"] == 360


# ---------------------------------------------------------------------------
# The direct-movement dialog
# ---------------------------------------------------------------------------


def test_the_move_endpoint_accepts_three_types_and_no_others(
    client_as, tenant_a, sede_a, admin_a
):
    """Every other type is the consequence of a document that has its own
    endpoint, and a general 'write me a move' endpoint would be the hole through
    which a future stage bypasses the ledger service."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 50)
    client = api(client_as, admin_a)
    response = post(
        client,
        "/api/stock-moves",
        {
            "location_id": str(sede_a.id),
            "item_id": str(item.id),
            "lot_id": str(lot.id),
            "quantity": 5,
            "type": "transfer_out",
            "reason": "correction",
        },
    )
    assert response.status_code == 422


def test_a_cashier_may_record_a_merma_and_not_an_ajuste(
    client_as, tenant_a, sede_a, cashier_a
):
    """Acceptance 20, and design-system §B.17·3's open question, answered.

    A negative movement a cashier can point at on a shelf is something the
    person who found it should record while it is in their hand. **A positive
    adjustment is the one movement that creates value out of nothing**, and it
    is the exact shape of a loss being covered.
    """
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 50)
    client = api(client_as, cashier_a)
    body = {
        "location_id": str(sede_a.id),
        "item_id": str(item.id),
        "lot_id": str(lot.id),
        "quantity": 3,
        "type": "shrinkage",
        "reason": "damage",
    }
    assert post(client, "/api/stock-moves", body).status_code == 200
    assert stock(tenant_a, sede_a, item, lot) == 47

    refused = post(
        client,
        "/api/stock-moves",
        {**body, "type": "adjustment", "reason": "correction"},
    )
    assert refused.status_code == 403


def test_a_cashier_cannot_write_a_movement_at_another_sede(
    client_as, tenant_a, sede_a, cashier_a
):
    """A2 · an explicit location outside the identity's set is **rejected, not
    intersected away**."""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, other, item, lot, 10)
    response = post(
        api(client_as, cashier_a),
        "/api/stock-moves",
        {
            "location_id": str(other.id),
            "item_id": str(item.id),
            "lot_id": str(lot.id),
            "quantity": 1,
            "type": "shrinkage",
            "reason": "damage",
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Transfers, and the arithmetic of a shortfall
# ---------------------------------------------------------------------------


def make_transfer(client, origin, destination, item, lot, quantity):
    response = post(
        client,
        "/api/transfers",
        {
            "origin_location_id": str(origin.id),
            "destination_location_id": str(destination.id),
            "lines": [
                {
                    "item_id": str(item.id),
                    "lot_id": str(lot.id),
                    "quantity_requested": quantity,
                }
            ],
        },
    )
    assert response.status_code == 200, response.content
    return response.json()


def network_total(tenant, item):
    return sum(
        StockOnHand.objects.filter(tenant=tenant, item=item).values_list(
            "quantity", flat=True
        )
    )


def test_a_shortfall_written_off_moves_the_network_total_once(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 9, and the sharpest arithmetic in the stage.

    Dispatch 60, receive 48, resolve as `No llegó`. **Every move attributable to
    the transfer sums to −12**, and the network total ends at `N − 12`. Wrong
    when it ends at `N − 24`: the same units were written off twice, once by
    `transfer_out` and once by the resolution, and both moves look individually
    correct.
    """
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 100)
    start = network_total(tenant_a, item)

    client = api(client_as, admin_a)
    transfer = make_transfer(client, sede_a, other, item, lot, 60)
    assert (
        post(client, f"/api/transfers/{transfer['id']}/dispatch", {}).status_code == 200
    )
    line = Transfer.objects.get(id=transfer["id"]).lines.get()

    received = post(
        client,
        f"/api/transfers/{transfer['id']}/receive",
        {"lines": [{"line_id": str(line.id), "quantity": 48}]},
    )
    assert received.status_code == 200
    body = received.json()
    assert body["status"] == TransferStatus.PARTIAL
    assert body["in_transit"] == 12
    assert stock(tenant_a, sede_a, item, lot) == 40
    assert stock(tenant_a, other, item, lot) == 48
    assert network_total(tenant_a, item) == start - 12

    resolved = post(
        client,
        f"/api/transfers/{transfer['id']}/resolve",
        {"line_id": str(line.id), "resolution": "lost_in_transit"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == TransferStatus.RECEIVED
    assert network_total(tenant_a, item) == start - 12

    with pin_tenant(tenant_a.id):
        attributable = StockMove.objects.filter(
            tenant=tenant_a, document_id=transfer["id"]
        )
        assert sum(one.quantity for one in attributable) == -12
        # The shrinkage is the row that carries the reason, and it is what a
        # shortfall has to leave behind: merchandise nobody ever has to explain
        # is exactly what nets out of a ledger quietly.
        loss = attributable.get(type=StockMoveType.SHRINKAGE)
        assert loss.reason == "loss"
        assert loss.location_id == sede_a.id


def test_resolving_a_shortfall_twice_appends_nothing(
    client_as, tenant_a, sede_a, admin_a
):
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 100)
    client = api(client_as, admin_a)
    transfer = make_transfer(client, sede_a, other, item, lot, 60)
    post(client, f"/api/transfers/{transfer['id']}/dispatch", {})
    line = Transfer.objects.get(id=transfer["id"]).lines.get()
    post(
        client,
        f"/api/transfers/{transfer['id']}/receive",
        {"lines": [{"line_id": str(line.id), "quantity": 48}]},
    )
    first = post(
        client,
        f"/api/transfers/{transfer['id']}/resolve",
        {"line_id": str(line.id), "resolution": "lost_in_transit"},
    )
    assert first.status_code == 200
    before = StockMove.objects.filter(tenant=tenant_a).count()
    second = post(
        client,
        f"/api/transfers/{transfer['id']}/resolve",
        {"line_id": str(line.id), "resolution": "lost_in_transit"},
    )
    assert second.status_code == 409
    assert StockMove.objects.filter(tenant=tenant_a).count() == before


def test_a_partial_transfer_is_closed_by_a_resolution_and_not_by_a_second_receipt(
    client_as, tenant_a, sede_a, admin_a
):
    """**Two doors to one outcome, one of which quietly lies about the shelf.**

    Receiving again on a `partial` would set `quantity_received` to the full
    dispatched figure while the ledger appended nothing -- the second
    `transfer_in` carries the same document key as the first and is
    deduplicated by `one_move_per_client_uuid` -- so the line would claim units
    the destination never got, and the projection and the document would
    disagree with each other for ever.
    """
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 100)
    client = api(client_as, admin_a)
    transfer = make_transfer(client, sede_a, other, item, lot, 60)
    post(client, f"/api/transfers/{transfer['id']}/dispatch", {})
    line = Transfer.objects.get(id=transfer["id"]).lines.get()
    post(
        client,
        f"/api/transfers/{transfer['id']}/receive",
        {"lines": [{"line_id": str(line.id), "quantity": 48}]},
    )
    again = post(client, f"/api/transfers/{transfer['id']}/receive", {})
    assert again.status_code == 409
    assert "faltante" in again.json()["detail"]

    line.refresh_from_db()
    assert line.quantity_received == 48
    assert stock(tenant_a, other, item, lot) == 48


def test_a_late_arrival_returns_the_network_total_to_where_it_started(
    client_as, tenant_a, sede_a, admin_a
):
    """The other half of acceptance 9: `Llegó después` sums to **0**."""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 100)
    start = network_total(tenant_a, item)
    client = api(client_as, admin_a)
    transfer = make_transfer(client, sede_a, other, item, lot, 60)
    post(client, f"/api/transfers/{transfer['id']}/dispatch", {})
    line = Transfer.objects.get(id=transfer["id"]).lines.get()
    post(
        client,
        f"/api/transfers/{transfer['id']}/receive",
        {"lines": [{"line_id": str(line.id), "quantity": 48}]},
    )
    post(
        client,
        f"/api/transfers/{transfer['id']}/resolve",
        {"line_id": str(line.id), "resolution": "received_late"},
    )
    assert network_total(tenant_a, item) == start
    with pin_tenant(tenant_a.id):
        attributable = StockMove.objects.filter(
            tenant=tenant_a, document_id=transfer["id"]
        )
        assert sum(one.quantity for one in attributable) == 0


def test_a_dispatch_beyond_the_projection_is_refused_at_the_endpoint(
    client_as, tenant_a, sede_a, admin_a
):
    """**Refusal is a policy at the endpoint, never in the service.** The sale
    path never asks this question, and putting the check inside the ledger would
    make the offline sale path depend on a rule that must not apply to it."""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 10)
    client = api(client_as, admin_a)
    transfer = make_transfer(client, sede_a, other, item, lot, 60)
    refused = post(client, f"/api/transfers/{transfer['id']}/dispatch", {})
    assert refused.status_code == 422

    with pin_tenant(tenant_a.id):
        tenant = Tenant.objects.get(id=tenant_a.id)
        inventory_settings.write(tenant, {"negative_stock_block_outbound": False})
    assert (
        post(client, f"/api/transfers/{transfer['id']}/dispatch", {}).status_code == 200
    )
    assert stock(tenant_a, sede_a, item, lot) == -50


def test_a_dispatched_transfer_is_a_fact_and_not_a_form(
    client_as, tenant_a, sede_a, admin_a
):
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 100)
    client = api(client_as, admin_a)
    transfer = make_transfer(client, sede_a, other, item, lot, 10)
    post(client, f"/api/transfers/{transfer['id']}/dispatch", {})
    assert (
        patch(client, f"/api/transfers/{transfer['id']}", {"note": "x"}).status_code
        == 409
    )
    assert client.delete(f"/api/transfers/{transfer['id']}").status_code == 409


# ---------------------------------------------------------------------------
# Cycle counts
# ---------------------------------------------------------------------------


def test_a_count_measures_the_discrepancy_at_entry_and_sales_apply_on_top(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 8, and the arithmetic nobody gets right by accident.

    A count entered at 10:00 and closed at 10:40 with two sales in between ends
    at **counted minus those sales**, not at counted. Getting it backwards
    double-counts every sale made during a count.
    """
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 100)
    client = api(client_as, admin_a)

    count = post(client, "/api/stock-counts", {"location_id": str(sede_a.id)}).json()
    entered = post(
        client,
        f"/api/stock-counts/{count['id']}/lines",
        {
            "lines": [
                {
                    "item_id": str(item.id),
                    "lot_id": str(lot.id),
                    "counted_quantity": 95,
                }
            ]
        },
    )
    assert entered.status_code == 200
    assert entered.json()["lines"][0]["expected_quantity"] == 100

    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, item, -5, lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )
    assert stock(tenant_a, sede_a, item, lot) == 95

    closed = post(client, f"/api/stock-counts/{count['id']}/close", {})
    assert closed.status_code == 200
    with pin_tenant(tenant_a.id):
        adjustment = StockMove.objects.get(tenant=tenant_a, type=StockMoveType.COUNT)
        assert adjustment.quantity == -5
    assert stock(tenant_a, sede_a, item, lot) == 90


def test_closing_a_count_resolves_the_negative_exception_it_covers(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 7 · the adjusting move and the resolution land in one
    transaction, **with no second endpoint under S2's `sync_conflicts` path**."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [
                move(sede_a, item, -2, lot, kind=StockMoveType.SALE),
            ],
            tenant_id=tenant_a.id,
        )
        assert (
            SyncConflict.objects.filter(
                tenant=tenant_a,
                type=SyncConflictType.NEGATIVE_STOCK,
                status=SyncConflictStatus.OPEN,
            ).count()
            == 1
        )

    client = api(client_as, admin_a)
    count = post(client, "/api/stock-counts", {"location_id": str(sede_a.id)}).json()
    entered = post(
        client,
        f"/api/stock-counts/{count['id']}/lines",
        {
            "lines": [
                {"item_id": str(item.id), "lot_id": str(lot.id), "counted_quantity": 6}
            ]
        },
    ).json()
    assert entered["lines"][0]["resolves_negative"] is True

    post(client, f"/api/stock-counts/{count['id']}/close", {})
    with pin_tenant(tenant_a.id):
        assert (
            SyncConflict.objects.filter(
                tenant=tenant_a, status=SyncConflictStatus.OPEN
            ).count()
            == 0
        )
        assert (
            StockMove.objects.get(tenant=tenant_a, type=StockMoveType.COUNT).reason
            == "negative_resolution"
        )
    assert stock(tenant_a, sede_a, item, lot) == 6


def test_the_counts_list_flags_the_sedes_that_are_due(
    client_as, tenant_a, sede_a, admin_a
):
    """The API surface asks this one path for *counts, with the locations whose
    `count_cadence_days` has elapsed flagged as due* -- one path and one
    envelope, not a second endpoint the surface does not list.

    **A sede that has never been counted is due, not silent**: it is exactly the
    one somebody should walk.
    """
    never = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 20)
    client = api(client_as, admin_a)

    body = client.get("/api/stock-counts").json()
    due = {one["location_name"]: one["due"] for one in body["due_locations"]}
    assert due == {"Chapinero": True, "Suba": True}

    count = post(client, "/api/stock-counts", {"location_id": str(sede_a.id)}).json()
    post(client, f"/api/stock-counts/{count['id']}/close", {})
    body = client.get("/api/stock-counts").json()
    due = {one["location_name"]: one["due"] for one in body["due_locations"]}
    assert due == {"Chapinero": False, "Suba": True}
    assert never.name == "Suba"


def test_a_cashier_may_count_and_may_not_close(
    client_as, tenant_a, sede_a, admin_a, cashier_a
):
    """Closing writes adjusting moves of arbitrary sign, and that is the
    movement that can cover a loss."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 20)
    count = post(
        api(client_as, admin_a), "/api/stock-counts", {"location_id": str(sede_a.id)}
    ).json()

    till = api(client_as, cashier_a)
    entered = post(
        till,
        f"/api/stock-counts/{count['id']}/lines",
        {
            "lines": [
                {"item_id": str(item.id), "lot_id": str(lot.id), "counted_quantity": 18}
            ]
        },
    )
    assert entered.status_code == 200
    assert post(till, f"/api/stock-counts/{count['id']}/close", {}).status_code == 403


def test_a_replayed_count_line_does_not_double_count(
    client_as, tenant_a, sede_a, admin_a
):
    """A5 · idempotent on `client_uuid` per line, so an offline device replaying
    a batch does not double-count."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 20)
    client = api(client_as, admin_a)
    count = post(client, "/api/stock-counts", {"location_id": str(sede_a.id)}).json()
    line = {
        "item_id": str(item.id),
        "lot_id": str(lot.id),
        "counted_quantity": 18,
        "client_uuid": str(uuid.uuid4()),
    }
    for _replay in range(3):
        post(client, f"/api/stock-counts/{count['id']}/lines", {"lines": [line]})
    assert StockCountLine.objects.filter(tenant=tenant_a).count() == 1


# ---------------------------------------------------------------------------
# The Estado derivation and the grid
# ---------------------------------------------------------------------------


def build_grid(tenant, location, other):
    """One row in each of the seven states, built from quantities and dates
    rather than from a column nobody can set."""
    rows = {}
    plan = [
        ("expired", -10, 30, 200, 20),
        ("stockout", 400, 0, 200, 20),
        ("expiring_urgent", 100, 30, 200, 20),
        ("expiring", 300, 30, 200, 20),
        ("reorder_point", 900, 10, 200, 20),
        ("overstock", 900, 400, 200, 20),
        ("sufficient", 900, 100, 200, 20),
    ]
    with pin_tenant(tenant.id):
        for name, days, quantity, capacity, reorder in plan:
            item = make_item(tenant, name=f"Producto {name}")
            lot = make_lot(tenant, item, code=f"L-{name}", days=days)
            StockPolicy.objects.create(
                tenant=tenant,
                item=item,
                location=location,
                max_quantity=capacity,
                reorder_point=reorder,
            )
            if quantity:
                ledger.append(
                    [move(location, item, quantity, lot)], tenant_id=tenant.id
                )
            else:
                # A `Quiebre` is a shelf that emptied, not a product nobody
                # ever stocked -- and its clause needs another sede to name.
                ledger.append(
                    [
                        move(location, item, 5, lot),
                        move(
                            location,
                            item,
                            -5,
                            lot,
                            kind=StockMoveType.SALE,
                        ),
                        move(other, item, 96, lot, kind=StockMoveType.TRANSFER_IN),
                    ],
                    tenant_id=tenant.id,
                )
            rows[name] = (item, lot)
    return rows


def test_the_seven_states_derive_from_quantity_expiry_and_policy(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 14 and 24 · every state renders, and `Estado` sorts by the
    derivation's ordinal.

    **Never alphabetical on the Spanish label**: in Spanish that would place
    `Punto de reorden` above `Quiebre` and above both expiry states, which looks
    deliberate, reads correctly and is wrong on all 4.284 rows at once.
    """
    other = make_location(tenant_a, "SUB", "Suba")
    build_grid(tenant_a, sede_a, other)
    client = api(client_as, admin_a)

    body = client.get(
        f"/api/stock?location_id={sede_a.id}&sort=state&order=asc&page_size=25"
    ).json()
    assert [row["state"] for row in body["rows"]] == [
        "expired",
        "stockout",
        "expiring_urgent",
        "expiring",
        "reorder_point",
        "overstock",
        "sufficient",
    ]
    assert [row["state_ordinal"] for row in body["rows"]] == [1, 2, 3, 4, 5, 6, 7]
    assert body["action_required"] == 4

    reverse = client.get(
        f"/api/stock?location_id={sede_a.id}&sort=state&order=desc&page_size=25"
    ).json()
    assert [row["state"] for row in reverse["rows"]] == [
        "sufficient",
        "overstock",
        "reorder_point",
        "expiring",
        "expiring_urgent",
        "stockout",
        "expired",
    ]


def test_expiry_outranks_the_reorder_point(client_as, tenant_a, sede_a, admin_a):
    """The handoff settles it: the Salbutamol row is at 7 units against a 4-day
    cover and still renders `Vence en 6 meses`. **A reorder badge that hides an
    expiry sends someone to order more of something that will not sell before
    its date.**"""
    item = make_item(tenant_a, name="Salbutamol inhalador 100 mcg")
    lot = make_lot(tenant_a, item, code="B-7741", days=150)
    with pin_tenant(tenant_a.id):
        StockPolicy.objects.create(
            tenant=tenant_a,
            item=item,
            location=sede_a,
            max_quantity=90,
            reorder_point=20,
        )
        ledger.append([move(sede_a, item, 7, lot)], tenant_id=tenant_a.id)
    body = client_as(admin_a).get(f"/api/stock?location_id={sede_a.id}").json()
    assert body["rows"][0]["state"] == "expiring_urgent"


def test_a_row_with_no_policy_reaches_neither_state_five_nor_six(
    client_as, tenant_a, sede_a, admin_a
):
    """`Sin política definida` in the record panel, and **no bar**: a bar with
    no capacity behind it is a bar measuring nothing."""
    item = make_item(tenant_a, name="Sin política")
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 1)
    client = api(client_as, admin_a)
    row = client.get(f"/api/stock?location_id={sede_a.id}").json()["rows"][0]
    assert row["state"] == "sufficient"
    assert row["policy_source"] is None
    assert row["bar_percentage"] is None

    put(
        client,
        "/api/stock-policies",
        {
            "policies": [
                {
                    "item_id": str(item.id),
                    "location_id": str(sede_a.id),
                    "reorder_point": 10,
                    "max_quantity": 100,
                }
            ]
        },
    )
    row = client.get(f"/api/stock?location_id={sede_a.id}").json()["rows"][0]
    assert row["state"] == "reorder_point"
    assert row["policy_source"] == "manual"
    assert row["bar_percentage"] == 1


def test_the_quiebre_clause_names_one_sede_and_names_it_again(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 15 · the location holding the most units, ties broken by name
    so the string does not change between page loads. **A badge listing four
    sedes is a badge nobody reads.**"""
    other = make_location(tenant_a, "SUB", "Suba")
    third = make_location(tenant_a, "KEN", "Kennedy")
    rows = build_grid(tenant_a, sede_a, other)
    item, lot = rows["stockout"]
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(third, item, 12, lot, kind=StockMoveType.TRANSFER_IN)],
            tenant_id=tenant_a.id,
        )
    client = api(client_as, admin_a)
    for _load in range(2):
        body = client.get(f"/api/stock?location_id={sede_a.id}&state=stockout").json()
        assert body["rows"][0]["elsewhere"]["location_name"] == "Suba"
        assert body["rows"][0]["elsewhere"]["quantity"] == 96
        assert body["rows"][0]["bar_percentage"] == 0


def test_the_state_sort_never_decreases_across_a_page_boundary(
    client_as, tenant_a, sede_a, admin_a
):
    """The failure this catches is a sort applied per page instead of per query,
    which a reviewer looking at page one cannot see."""
    other = make_location(tenant_a, "SUB", "Suba")
    build_grid(tenant_a, sede_a, other)
    client = api(client_as, admin_a)
    ordinals = []
    for page in (1, 2, 3):
        body = client.get(
            f"/api/stock?location_id={sede_a.id}&sort=state&order=asc"
            f"&page_size=25&page={page}"
        ).json()
        ordinals.extend(row["state_ordinal"] for row in body["rows"])
    assert ordinals == sorted(ordinals)


def test_the_summary_counts_agree_with_the_grid(client_as, tenant_a, sede_a, admin_a):
    """*Verification* asserts against **what the product reports** rather than
    against a literal: the figures are whatever the fixtures built, and a check
    that hard-codes them is red the first time a fixture changes."""
    other = make_location(tenant_a, "SUB", "Suba")
    build_grid(tenant_a, sede_a, other)
    client = api(client_as, admin_a)
    summary = client.get(f"/api/stock/summary?location_id={sede_a.id}").json()
    by_state = {row["state"]: row["count"] for row in summary["states"]}
    # Every one of the seven is present, including any at zero: a chip whose
    # options moved as the data did would be a chip nobody could trust.
    assert set(by_state) == {
        "expired",
        "stockout",
        "expiring_urgent",
        "expiring",
        "reorder_point",
        "overstock",
        "sufficient",
    }
    for state, count in by_state.items():
        page = client.get(
            f"/api/stock?location_id={sede_a.id}&state={state}&page_size=100"
        ).json()
        assert page["row_count"] == count


# ---------------------------------------------------------------------------
# Valuation, the trace, and the settings group
# ---------------------------------------------------------------------------


def test_the_expiring_endpoint_reconciles_at_cost_and_moves_with_the_horizon(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 23 · value and lot count reconcile to `Σ quantity ×
    lots.unit_cost`, the total is on the same basis, and changing the horizon
    moves both **without a deploy**.

    A percentage whose numerator and denominator came from two cost bases is a
    number nobody can defend to an accountant.
    """
    soon = make_item(tenant_a, name="Vence pronto")
    later = make_item(tenant_a, name="Vence después")
    soon_lot = make_lot(tenant_a, soon, code="S-1", days=40, cost="1000.00")
    later_lot = make_lot(tenant_a, later, code="S-2", days=200, cost="2000.00")
    seed_stock(tenant_a, sede_a, soon, soon_lot, 10)
    seed_stock(tenant_a, sede_a, later, later_lot, 5)

    client = api(client_as, admin_a)
    body = client.get("/api/stock/expiring").json()
    valuation = next(one for one in body["horizons"] if one["key"] == "valuation")
    assert valuation["days"] == 90
    assert valuation["value"] == "10000.00"
    assert valuation["lots"] == 1
    assert body["total_value"] == "20000.00"

    patch(client, "/api/settings/inventory", {"expiry_valuation_days": 300})
    body = client.get("/api/stock/expiring").json()
    valuation = next(one for one in body["horizons"] if one["key"] == "valuation")
    assert valuation["value"] == "20000.00"
    assert valuation["lots"] == 2


def test_the_total_value_counts_a_negative_row_rather_than_dropping_it(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 23 · the total reconciles to `Σ quantity × lots.unit_cost` over
    **every** `stock_on_hand` row in scope.

    §5 rule 2 makes a negative row a designed-for state and not an anomaly: two
    offline tills sell the last box, both sales stand, and the key goes below
    zero. Those units are merchandise the network owes; their value is real and
    negative. Dropping them makes the denominator of S9's
    `4,6% del inventario valorizado` disagree with the sum anybody would compute
    by hand -- and a percentage nobody can reproduce is one nobody can defend to
    an accountant.
    """
    held = make_item(tenant_a, name="En existencia")
    owed = make_item(tenant_a, name="Sobrevendido")
    held_lot = make_lot(tenant_a, held, code="V-1", days=40, cost="1000.00")
    owed_lot = make_lot(tenant_a, owed, code="V-2", days=200, cost="500.00")
    seed_stock(tenant_a, sede_a, held, held_lot, 10)
    with pin_tenant(tenant_a.id):
        ledger.append(
            [move(sede_a, owed, -4, owed_lot, kind=StockMoveType.SALE)],
            tenant_id=tenant_a.id,
        )

    body = client_as(admin_a).get("/api/stock/expiring").json()
    # 10 x 1.000 held, minus 4 x 500 owed.
    assert body["total_value"] == "8000.00"
    with pin_tenant(tenant_a.id):
        by_hand = sum(
            row.quantity * row.lot.unit_cost
            for row in StockOnHand.objects.filter(tenant=tenant_a).select_related("lot")
        )
    assert Decimal(body["total_value"]) == by_hand


def test_a_lot_with_nothing_on_the_shelf_is_not_a_lot_at_risk(
    client_as, tenant_a, sede_a, admin_a
):
    """`142 lotes` beside `$18,9 M` is a count of lots at risk, and a lot with
    nothing on it is not one. Excluding a zero changes no value, so all three
    figures still come off one query."""
    item = make_item(tenant_a, name="Agotado")
    lot = make_lot(tenant_a, item, code="V-0", days=30, cost="1000.00")
    with pin_tenant(tenant_a.id):
        ledger.append(
            [
                move(sede_a, item, 5, lot),
                move(sede_a, item, -5, lot, kind=StockMoveType.SALE),
            ],
            tenant_id=tenant_a.id,
        )
    body = client_as(admin_a).get("/api/stock/expiring").json()
    valuation = next(one for one in body["horizons"] if one["key"] == "valuation")
    assert valuation["lots"] == 0
    assert valuation["value"] == "0.00"


def test_the_lot_trace_is_the_recall_answer(client_as, tenant_a, sede_a, admin_a):
    """Acceptance 10 · every move in `recorded_at` order with its document, its
    device, its user and both clocks, a running balance whose final value equals
    the projection, and the same rows in the same order as CSV.

    **A final balance that disagrees with the projection is a trace nobody can
    hand to an inspector.**
    """
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item, code="A-2291")
    with pin_tenant(tenant_a.id):
        ledger.append(
            [
                move(sede_a, item, 100, lot),
                move(sede_a, item, -20, lot, kind=StockMoveType.TRANSFER_OUT),
                move(other, item, 20, lot, kind=StockMoveType.TRANSFER_IN),
                move(sede_a, item, -4, lot, kind=StockMoveType.SALE),
            ],
            tenant_id=tenant_a.id,
        )
    client = api(client_as, admin_a)
    body = client.get(f"/api/lots/{lot.id}/trace").json()
    assert len(body["moves"]) == 4
    assert body["moves"][-1]["balance"] == 96
    assert body["lot"]["total"] == 96
    assert {one["location_name"] for one in body["lot"]["by_location"]} == {
        "Chapinero",
        "Suba",
    }

    csv_response = client.get(f"/api/lots/{lot.id}/trace.csv")
    assert csv_response.status_code == 200
    assert csv_response["Content-Type"].startswith("text/csv")
    lines = csv_response.content.decode().strip().splitlines()
    assert len(lines) == 5
    assert lines[-1].split(",")[9] == "96"

    found = client.get("/api/lots?code=A-2291").json()
    assert found["row_count"] == 1
    assert found["rows"][0]["total"] == 96


def test_the_settings_group_leaves_its_neighbours_alone(client_as, tenant_a, admin_a):
    """*What this stage would break* · a helper misused as a whole-column write
    wipes S0's and S2's keys, and nothing surfaces it until somebody opens
    another settings screen weeks later."""
    client = api(client_as, admin_a)
    patch(
        client,
        "/api/settings/tenant",
        {
            "name": "Droguerías La 45",
            "nit": "901.245.778-3",
            "legal_name": "Droguerías La 45 S.A.S.",
            "timezone": "America/Bogota",
        },
    )
    patch(client, "/api/settings/inventory", {"expiry_alert_days": 200})
    assert client.get("/api/settings/inventory").json()["expiry_alert_days"] == 200
    assert (
        client.get("/api/settings/tenant").json()["legal_name"]
        == "Droguerías La 45 S.A.S."
    )
    assert client.get("/api/settings/sync").json()["pull_interval_seconds"] == 8


def test_an_alert_window_wider_than_the_notice_window_is_refused(
    client_as, tenant_a, admin_a
):
    """A lot would enter `Vence pronto` without having passed through `Vence`,
    which is a badge sequence nobody could explain."""
    response = patch(
        api(client_as, admin_a),
        "/api/settings/inventory",
        {"expiry_alert_days": 400},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Thresholds, and the trail
# ---------------------------------------------------------------------------


def test_a_manual_threshold_survives_a_rebuild_and_a_count_close(
    client_as, tenant_a, sede_a, admin_a
):
    """Acceptance 17 · **wrong when a rebuild, a re-seed or a count close resets
    either column** -- at S6 that same hole is the model silently overwriting a
    number a pharmacist set, and `source` is the only thing that would have
    caught it."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 50)
    client = api(client_as, admin_a)
    put(
        client,
        "/api/stock-policies",
        {
            "policies": [
                {
                    "item_id": str(item.id),
                    "location_id": str(sede_a.id),
                    "reorder_point": 80,
                    "max_quantity": 200,
                }
            ]
        },
    )
    assert (
        client.get(f"/api/stock?location_id={sede_a.id}").json()["rows"][0]["state"]
        == "reorder_point"
    )

    count = post(client, "/api/stock-counts", {"location_id": str(sede_a.id)}).json()
    post(
        client,
        f"/api/stock-counts/{count['id']}/lines",
        {
            "lines": [
                {"item_id": str(item.id), "lot_id": str(lot.id), "counted_quantity": 48}
            ]
        },
    )
    post(client, f"/api/stock-counts/{count['id']}/close", {})
    with pin_tenant(tenant_a.id):
        ledger.rebuild(tenant_a.id, sede_a.id)
        policy = StockPolicy.objects.get(tenant=tenant_a, item=item)
        assert policy.reorder_point == 80
        assert policy.source == "manual"


def test_a_write_over_a_model_row_flips_the_source_back(
    client_as, tenant_a, sede_a, admin_a
):
    """That is the point of the column, and it is what S6 has to preserve."""
    item = make_item(tenant_a)
    with pin_tenant(tenant_a.id):
        StockPolicy.objects.create(
            tenant=tenant_a,
            item=item,
            location=sede_a,
            reorder_point=10,
            source="model",
        )
    body = put(
        api(client_as, admin_a),
        "/api/stock-policies",
        {
            "policies": [
                {
                    "item_id": str(item.id),
                    "location_id": str(sede_a.id),
                    "reorder_point": 25,
                }
            ]
        },
    ).json()
    assert body[0]["source"] == "manual"
    assert body[0]["reorder_point"] == 25


def test_every_mutation_lands_one_audit_row(client_as, tenant_a, sede_a, admin_a):
    """Acceptance 21 · nine mutations, nine rows, each with actor, entity and
    before/after. **A 40-line receipt is one row, not forty.**"""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item, code="A-2291", days=500)
    client = api(client_as, admin_a)
    with pin_tenant(tenant_a.id):
        before = AuditLog.objects.filter(tenant=tenant_a).count()

    post(
        client,
        "/api/receipts",
        {
            "location_id": str(sede_a.id),
            "lines": [
                {
                    "item_id": str(item.id),
                    "lot_code": "A-2291",
                    "expires_at": str(timezone.localdate() + timedelta(days=500)),
                    "quantity": 200,
                }
                for _line in range(40)
            ],
        },
    )
    post(
        client,
        "/api/stock-moves",
        {
            "location_id": str(sede_a.id),
            "item_id": str(item.id),
            "lot_id": str(lot.id),
            "quantity": 2,
            "type": "shrinkage",
            "reason": "damage",
        },
    )
    transfer = make_transfer(client, sede_a, other, item, lot, 60)
    post(client, f"/api/transfers/{transfer['id']}/dispatch", {})
    line = Transfer.objects.get(id=transfer["id"]).lines.get()
    post(
        client,
        f"/api/transfers/{transfer['id']}/receive",
        {"lines": [{"line_id": str(line.id), "quantity": 48}]},
    )
    post(
        client,
        f"/api/transfers/{transfer['id']}/resolve",
        {"line_id": str(line.id), "resolution": "lost_in_transit"},
    )
    count = post(client, "/api/stock-counts", {"location_id": str(sede_a.id)}).json()
    post(
        client,
        f"/api/stock-counts/{count['id']}/lines",
        {
            "lines": [
                {"item_id": str(item.id), "lot_id": str(lot.id), "counted_quantity": 1}
            ]
        },
    )
    post(client, f"/api/stock-counts/{count['id']}/close", {})
    put(
        client,
        "/api/stock-policies",
        {"policies": [{"item_id": str(item.id), "reorder_point": 5}]},
    )
    patch(client, "/api/settings/inventory", {"count_cadence_days": 15})

    with pin_tenant(tenant_a.id):
        rows = AuditLog.objects.filter(tenant=tenant_a).order_by("created_at")[before:]
        entities = [row.entity_type for row in rows]
    # One receipt (not forty), one movement, one transfer create, dispatch,
    # receive, resolve, one count create, one close, one policy, one settings.
    assert entities.count("receipts") == 1
    assert set(entities) == {
        "receipts",
        "stock_moves",
        "transfers",
        "transfer_lines",
        "stock_counts",
        "stock_policies",
        "settings.inventory",
    }
    for row in rows:
        assert row.actor_email == admin_a.email
        assert row.before is not None or row.after is not None


def test_a_cashier_reads_the_grid_and_writes_no_threshold(
    client_as, tenant_a, sede_a, cashier_a
):
    """Acceptance 20 · read-only across every sede, and `PUT /api/stock-policies`
    refused when called directly."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 10)
    client = api(client_as, cashier_a)
    assert client.get("/api/stock").status_code == 200
    assert (
        put(
            client,
            "/api/stock-policies",
            {"policies": [{"item_id": str(item.id), "reorder_point": 5}]},
        ).status_code
        == 403
    )


def test_the_ledger_read_is_scoped_to_a_cashiers_own_sede(
    client_as, tenant_a, sede_a, cashier_a
):
    """A2 · a grid that filters by its own chip and forgets the helper looks
    correct to every reviewer holding an owner account."""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, other, item, lot, 10)
    client = api(client_as, cashier_a)
    assert client.get("/api/stock-moves").json()["row_count"] == 0
    assert client.get(f"/api/stock-moves?location_id={other.id}").status_code == 403


def test_the_projection_has_no_api(client_as, tenant_a, admin_a):
    """Rule 7 · there is **no endpoint that writes `stock_on_hand`**, at any
    path, for any role. The projection has no API, and this is the check that
    says so out loud."""
    from core.api import api as ninja_api

    schema = ninja_api.get_openapi_schema()
    assert not [path for path in schema["paths"] if "stock-on-hand" in path]
    assert not [path for path in schema["paths"] if "stock_on_hand" in path]
    for path, operations in schema["paths"].items():
        if path.startswith("/api/stock-moves"):
            assert set(operations) <= {"get", "post"}, path


# ---------------------------------------------------------------------------
# En tránsito, on the item's record panel (§1 deliverable 11)
# ---------------------------------------------------------------------------


def test_the_item_lookup_carries_the_units_on_the_road(
    client_as, tenant_a, sede_a, admin_a
):
    """Deliverable 11 · units between the two legs are **on no shelf**, so they
    are never in `quantity` and never a state in the `Estado` column -- and the
    record panel has to be able to tell an empty sede from one whose box is in
    a van."""
    other = make_location(tenant_a, "SUB", "Suba")
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 100)
    client = api(client_as, admin_a)
    transfer = make_transfer(client, sede_a, other, item, lot, 60)

    quiet = client.get(f"/api/stock/availability?item_id={item.id}").json()
    assert quiet["in_transit"] == 0

    post(client, f"/api/transfers/{transfer['id']}/dispatch", {})
    moving = client.get(f"/api/stock/availability?item_id={item.id}").json()
    assert moving["in_transit"] == 60
    destination = next(
        one for one in moving["by_location"] if one["location_id"] == str(other.id)
    )
    # The destination has nothing on the shelf and sixty on the road, and the
    # two figures are separate.
    assert destination["quantity"] == 0
    assert destination["in_transit"] == 60

    line = Transfer.objects.get(id=transfer["id"]).lines.get()
    post(
        client,
        f"/api/transfers/{transfer['id']}/receive",
        {"lines": [{"line_id": str(line.id), "quantity": 48}]},
    )
    partial = client.get(f"/api/stock/availability?item_id={item.id}").json()
    assert partial["in_transit"] == 12

    post(
        client,
        f"/api/transfers/{transfer['id']}/resolve",
        {"line_id": str(line.id), "resolution": "lost_in_transit"},
    )
    settled = client.get(f"/api/stock/availability?item_id={item.id}").json()
    # A resolved line is settled either way, so its units are off the road.
    assert settled["in_transit"] == 0


# ---------------------------------------------------------------------------
# A refused line names the line and the box (§B.10.3)
# ---------------------------------------------------------------------------


def test_a_refused_receipt_line_names_which_line_and_which_control(
    client_as, tenant_a, sede_a, admin_a
):
    """`Cargar mercancía` takes twenty lines at a time. A region-scope block at
    the foot of the page saying a lot code disagrees leaves somebody reading
    twenty lines to find which one."""
    good = make_item(tenant_a, name="Ibuprofeno 400 mg × 30")
    clash = make_item(tenant_a, name="Acetaminofén 500 mg × 100")
    make_lot(tenant_a, clash, code="A-2291", days=400)
    client = api(client_as, admin_a)
    response = post(
        client,
        "/api/receipts",
        {
            "location_id": str(sede_a.id),
            "reason": "standalone_receipt",
            "lines": [
                {
                    "item_id": str(good.id),
                    "lot_code": "B-1",
                    "expires_at": str(timezone.localdate() + timedelta(days=500)),
                    "quantity": 10,
                },
                {
                    "item_id": str(clash.id),
                    "lot_code": "A-2291",
                    "expires_at": str(timezone.localdate() + timedelta(days=90)),
                    "quantity": 10,
                },
            ],
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["line"] == 1
    assert body["field"] == "expires_at"
    assert "A-2291" in body["detail"]
    # The whole entry is refused, not the good line alone: a receipt is one
    # document and half of one is not a thing anybody asked for.
    with pin_tenant(tenant_a.id):
        assert StockMove.objects.filter(tenant=tenant_a).count() == 0


def test_an_entry_replayed_under_its_own_key_books_nothing_twice(
    client_as, tenant_a, sede_a, admin_a
):
    """A5 · `Confirmar entrada` pressed twice, or retried after a timeout that
    committed. The surface mints the document id and the line keys once and
    keeps them across the retry, which is the whole reason they are on the
    wire."""
    item = make_item(tenant_a)
    client = api(client_as, admin_a)
    body = {
        "location_id": str(sede_a.id),
        "document_id": str(uuid.uuid4()),
        "reason": "standalone_receipt",
        "lines": [
            {
                "client_uuid": str(uuid.uuid4()),
                "item_id": str(item.id),
                "lot_code": "R-1",
                "expires_at": str(timezone.localdate() + timedelta(days=500)),
                "quantity": 40,
            }
        ],
    }
    first = post(client, "/api/receipts", body)
    second = post(client, "/api/receipts", body)
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["lines_duplicate"] == 1
    with pin_tenant(tenant_a.id):
        assert StockMove.objects.filter(tenant=tenant_a).count() == 1
        assert StockOnHand.objects.get(tenant=tenant_a, item=item).quantity == 40


# ---------------------------------------------------------------------------
# The open exceptions reach the screen that resolves them (§5 rule 2)
# ---------------------------------------------------------------------------


def test_a_count_carries_the_sedes_open_negatives_counted_or_not(
    client_as, tenant_a, sede_a, admin_a
):
    """Finding the exception is the whole point: a negative raised by a direct
    movement, a transfer receipt or the opening-stock command has **no device
    behind it**, so the device-scoped arrival queue never shows it. This screen
    is where §5 says it is resolved, so this is where it has to be visible --
    before anybody has counted the reference, not after."""
    item = make_item(tenant_a)
    lot = make_lot(tenant_a, item)
    seed_stock(tenant_a, sede_a, item, lot, 2)
    with pin_tenant(tenant_a.id):
        # A direct movement, from the office, with no device behind it.
        ledger.append(
            [move(sede_a, item, -5, lot, kind=StockMoveType.SHRINKAGE)],
            tenant_id=tenant_a.id,
        )
    client = api(client_as, admin_a)
    count = post(
        client, "/api/stock-counts", {"location_id": str(sede_a.id), "scope": "full"}
    ).json()

    listed = client.get("/api/stock-counts").json()
    open_rows = listed["rows"][0]["negatives"]
    assert len(open_rows) == 1
    assert open_rows[0]["item_name"] == item.name
    assert open_rows[0]["lot_code"] == lot.lot_code
    assert open_rows[0]["quantity"] == -3
    assert open_rows[0]["counted"] is False

    entered = post(
        client,
        f"/api/stock-counts/{count['id']}/lines",
        {
            "lines": [
                {
                    "item_id": str(item.id),
                    "lot_id": str(lot.id),
                    "counted_quantity": 4,
                }
            ]
        },
    ).json()
    assert entered["negatives"][0]["counted"] is True

    closed = post(client, f"/api/stock-counts/{count['id']}/close", {}).json()
    assert closed["negatives"] == []
    with pin_tenant(tenant_a.id):
        assert not SyncConflict.objects.filter(
            tenant=tenant_a, status=SyncConflictStatus.OPEN
        ).exists()
