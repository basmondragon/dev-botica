"""The order, from the proposal to the shelf.

**The single most important assertion in this file is that
`suggested_quantity` never moves.** Two columns, never one: the difference
between what the model proposed and what the buyer approved is the only honest
measure the product will ever have of whether the model is trusted, and
overwriting the proposal destroys that measurement permanently and
irrecoverably.

After it: the approval and its idempotence, the discard that is not a failure,
receiving through S3's ledger and only through it, over- and under-delivery, the
cost and the lead time observed rather than quoted, and the role gate that keeps
a `cashier` off every one of these surfaces.
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from core.inventory import ledger
from core.models import (
    AuditLog,
    Item,
    GoodsReceipt,
    GoodsReceiptStatus,
    Lot,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    StockMove,
    StockMoveType,
    StockOnHand,
    Supplier,
    SupplierItem,
    Tenant,
)
from core.purchasing import orders as order_service, receiving
from core.tenancy import pin_tenant
from core.tests.conftest import make_location
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_purchasing_forecast import (
    link,
    policy,
    refresh,
    sell,
    stock,
    supplier,
)
from core.tests.test_sync_pull import make_item

pytestmark = pytest.mark.django_db


def build_order(tenant, location, *, units=30, weeks=20, on_hand=40, name=None):
    """A tenant with one measured reference and one `suggested` order on it.

    The reference tracks lots, because that is what a droguería's stock is and
    what the receiving screen has to ask for.
    """
    item = make_item(tenant, name or f"Suero oral 500 ml {Item.objects.count()}")
    lot = make_lot(tenant, item, code=f"A-{Item.objects.count():04d}")
    stock(tenant, location, item, on_hand, lot=lot, days_back=210)
    seller = supplier(tenant)
    link(tenant, item, seller, cost="3900.00")
    for week in range(1, weeks + 1):
        sell(tenant, location, item, weeks_back=week, units=units)
    refresh(tenant, location)
    with pin_tenant(tenant.id):
        built = order_service.generate(tenant.id, location.id)
    return item, seller, built[0]


# ---------------------------------------------------------------------------
# The edit (acceptance 3, 4)
# ---------------------------------------------------------------------------


def test_editing_a_quantity_writes_approved_and_never_suggested(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 3 · reading the row back shows the original
    `suggested_quantity` beside the new `approved_quantity`."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    line = order.lines.get()
    proposed = line.suggested_quantity
    assert proposed and proposed > 0

    response = client_as(owner_a).patch(
        f"/api/purchase-orders/{order.id}/lines/{line.id}",
        data={"approved_quantity": proposed - 20},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["suggested_quantity"] == proposed
    assert body["approved_quantity"] == proposed - 20

    with pin_tenant(tenant_a.id):
        fresh = PurchaseOrderLine.objects.get(id=line.id)
    assert fresh.suggested_quantity == proposed
    assert fresh.approved_quantity == proposed - 20


def test_no_endpoint_carries_a_field_that_could_move_the_proposal(
    client_as, owner_a, tenant_a, sede_a
):
    """**There is no field here that could carry a `suggested_quantity`**, which
    is the cheapest possible guarantee that no request ever moves one."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    line = order.lines.get()
    proposed = line.suggested_quantity

    client_as(owner_a).patch(
        f"/api/purchase-orders/{order.id}/lines/{line.id}",
        data={"approved_quantity": 5, "suggested_quantity": 1},
        content_type="application/json",
    )
    with pin_tenant(tenant_a.id):
        assert PurchaseOrderLine.objects.get(id=line.id).suggested_quantity == proposed


def test_setting_a_quantity_to_zero_leaves_the_order_and_the_total(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 4 · a line at zero leaves `Referencias sugeridas` and the
    order total, and stays on the screen with its reason."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    line = order.lines.get()
    client_as(owner_a).patch(
        f"/api/purchase-orders/{order.id}/lines/{line.id}",
        data={"approved_quantity": 0},
        content_type="application/json",
    )
    body = client_as(owner_a).get(f"/api/purchase-orders/{order.id}").json()
    assert body["suggested_reference_count"] == 0
    assert Decimal(body["order"]["total"]) == Decimal("0.00")
    assert len(body["lines"]) == 1


def test_the_four_tiles_come_back_in_the_same_request(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 2 · **the four KPI tiles are computed in the same request and
    do not add a second round trip.**"""
    _item, _seller, order = build_order(tenant_a, sede_a)
    body = client_as(owner_a).get(f"/api/purchase-orders/{order.id}").json()
    keys = [tile["key"] for tile in body["kpis"]]
    assert keys == [
        "suggested_references",
        "order_value",
        "stockouts_avoided",
        "manual_order_saving",
    ]
    assert body["provenance"]["window"]
    assert body["basis_counts"] and body["band_counts"]


def test_a_parametric_order_degrades_its_tiles_to_a_dash_never_a_zero(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 30 · **no tile renders `0`.** Tile 1 counts lines, tile 2
    keeps its figure with `sin proyección de venta` beneath it, and tiles 3 and
    4 render nothing at all rather than a zero."""
    item = make_item(tenant_a, "Sin histórico", tracks_lots=False)
    stock(tenant_a, sede_a, item, 2)
    policy(tenant_a, sede_a, item, reorder_point=10, max_quantity=60)
    link(tenant_a, item, supplier(tenant_a))
    refresh(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order = order_service.generate(tenant_a.id, sede_a.id)[0]

    tiles = {
        tile["key"]: tile
        for tile in client_as(owner_a)
        .get(f"/api/purchase-orders/{order.id}")
        .json()["kpis"]
    }
    assert tiles["suggested_references"]["figure"] is not None
    assert tiles["order_value"]["reading"] == "sin proyección de venta"
    assert tiles["stockouts_avoided"]["figure"] is None
    assert tiles["manual_order_saving"]["figure"] is None
    assert tiles["manual_order_saving"]["reading"] == "sin comparación posible"


def test_the_confidence_chip_filters_on_both_readings(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 28 · setting the chip to `Paramétrica` returns exactly the
    lines whose basis is `parametric`, **and the tiles go on describing the
    whole order**."""
    measured = make_item(tenant_a, "Con venta", tracks_lots=False)
    thin = make_item(tenant_a, "Sin venta", tracks_lots=False)
    stock(tenant_a, sede_a, measured, 30, days_back=210)
    stock(tenant_a, sede_a, thin, 2, days_back=210)
    policy(tenant_a, sede_a, thin, reorder_point=10, max_quantity=60)
    seller = supplier(tenant_a)
    link(tenant_a, measured, seller)
    link(tenant_a, thin, seller)
    for week in range(1, 21):
        sell(tenant_a, sede_a, measured, weeks_back=week, units=25)
    refresh(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order = order_service.generate(tenant_a.id, sede_a.id)[0]

    whole = client_as(owner_a).get(f"/api/purchase-orders/{order.id}").json()
    narrowed = (
        client_as(owner_a)
        .get(f"/api/purchase-orders/{order.id}?basis=parametric")
        .json()
    )

    assert narrowed["row_count"] == whole["basis_counts"]["parametric"]
    assert all(line["basis"] == "parametric" for line in narrowed["lines"])
    # The tiles describe the order, not the filtered slice.
    assert narrowed["kpis"] == whole["kpis"]
    assert narrowed["suggested_reference_count"] == whole["suggested_reference_count"]


def test_every_line_carries_a_reason_that_renders(client_as, owner_a, tenant_a, sede_a):
    """**`Por qué` is never empty.** Every line carries exactly one code, and
    the fixed string it renders is what the screen shows when the gateway wrote
    no prose."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    body = client_as(owner_a).get(f"/api/purchase-orders/{order.id}").json()
    for line in body["lines"]:
        assert line["reason_code"]
        assert line["reason_fallback"]


# ---------------------------------------------------------------------------
# Approve, dispatch, discard (acceptance 5, 6, 24)
# ---------------------------------------------------------------------------


def test_approving_locks_the_order_and_refuses_a_later_edit(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 5 · on confirm every quantity is frozen, and
    `PATCH …/lines/{id}` on that order is refused."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    line = order.lines.get()

    approved = client_as(owner_a).post(f"/api/purchase-orders/{order.id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == PurchaseOrderStatus.APPROVED

    refused = client_as(owner_a).patch(
        f"/api/purchase-orders/{order.id}/lines/{line.id}",
        data={"approved_quantity": 1},
        content_type="application/json",
    )
    assert refused.status_code == 409


def test_a_second_approval_changes_nothing(client_as, owner_a, tenant_a, sede_a):
    """Idempotent, because a double-pressed button must not send a supplier the
    same order twice."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    first = client_as(owner_a).post(f"/api/purchase-orders/{order.id}/approve").json()
    second = client_as(owner_a).post(f"/api/purchase-orders/{order.id}/approve").json()
    assert first["approved_at"] == second["approved_at"]


def test_discarding_is_terminal_and_is_not_a_failure(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 6 · the resulting badge is **Descartada** on the neutral
    family -- discarding a suggestion is the product working."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    body = client_as(owner_a).post(f"/api/purchase-orders/{order.id}/discard").json()
    assert body["status"] == PurchaseOrderStatus.DISCARDED
    again = client_as(owner_a).post(f"/api/purchase-orders/{order.id}/discard")
    assert again.status_code == 409


def test_three_actions_land_three_audit_rows(client_as, owner_a, tenant_a, sede_a):
    """Acceptance 24 · approving, discarding and editing one quantity each land
    an `audit_log` row with actor, entity and before/after."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    line = order.lines.get()
    with pin_tenant(tenant_a.id):
        before = AuditLog.objects.count()

    client_as(owner_a).patch(
        f"/api/purchase-orders/{order.id}/lines/{line.id}",
        data={"approved_quantity": 3},
        content_type="application/json",
    )
    client_as(owner_a).post(f"/api/purchase-orders/{order.id}/approve")

    _item2, _seller2, second = build_order(tenant_a, sede_a, name="Dipirona 500 mg")
    client_as(owner_a).post(f"/api/purchase-orders/{second.id}/discard")

    with pin_tenant(tenant_a.id):
        rows = AuditLog.objects.order_by("created_at")[before:]
        actions = [row.action for row in rows]
    assert actions == ["update", "approve", "reject"]
    assert all(row.actor_email == owner_a.email for row in rows)


def test_mark_sent_records_a_dispatch_botica_did_not_make(
    client_as, owner_a, tenant_a, sede_a
):
    """The supplier has no address on file, or the buyer phoned it in. An order
    stuck at **Aprobada** because we could not send it is an order nobody can
    receive against."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    client_as(owner_a).post(f"/api/purchase-orders/{order.id}/approve")
    body = client_as(owner_a).post(f"/api/purchase-orders/{order.id}/mark-sent").json()
    assert body["status"] == PurchaseOrderStatus.SENT
    assert body["sent_at"]


# ---------------------------------------------------------------------------
# Receiving (acceptance 8, 9, 10, 11, 12)
# ---------------------------------------------------------------------------


def open_receipt(client, actor, order):
    """Open a receipt against a `sent` order, as the screen does."""
    response = client(actor).post(
        "/api/goods-receipts",
        data={"purchase_order_id": str(order.id)},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()
    return response.json()


def type_receipt(client, actor, receipt, *, quantities=None):
    """Type the lot code and the expiry every lot-tracked line needs.

    A receipt cannot be confirmed without them, which is the point: the code and
    the date are printed on the carton, and a receiving screen that guessed them
    would put a lot nobody can trace onto a shelf.
    """
    lines = []
    for position, line in enumerate(receipt["lines"]):
        lines.append(
            {
                "id": line["id"],
                "item_id": line["item_id"],
                "purchase_order_line_id": line["purchase_order_line_id"],
                "quantity": (quantities or {}).get(position, line["quantity"]),
                "lot_code": f"L-{position:04d}",
                "expires_at": "2028-03-01",
                "unit_cost": line["unit_cost"],
            }
        )
    response = client(actor).patch(
        f"/api/goods-receipts/{receipt['id']}",
        data={"lines": lines},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()
    return response.json()


def sent_order(tenant, location):
    item, seller, order = build_order(tenant, location, on_hand=10)
    with pin_tenant(tenant.id):
        order_service.approve(order, actor=None)
        order_service.mark_sent(order)
    return item, seller, order


def test_receiving_moves_stock_only_through_the_ledger(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 11 · **no row in `stock_moves` is written by this stage's own
    code**, and every quantity traces to a `receipt` move carrying the receipt's
    document pair, the user and the timestamp."""
    item, _seller, order = sent_order(tenant_a, sede_a)
    opened = open_receipt(client_as, owner_a, order)
    type_receipt(client_as, owner_a, opened)

    confirmed = client_as(owner_a).post(
        f"/api/goods-receipts/{opened['id']}/confirm",
        data={},
        content_type="application/json",
    )
    assert confirmed.status_code == 200, confirmed.json()

    with pin_tenant(tenant_a.id):
        moves = list(StockMove.objects.filter(document_id=opened["id"]))
        assert moves and all(one.type == StockMoveType.RECEIPT for one in moves)
        assert all(one.document_type == "goods_receipt" for one in moves)
        assert all(one.user_id == owner_a.id for one in moves)
        # A3 · the projection is the ledger's sum, and rebuilding proves it.
        before = ledger.projection_totals(tenant_a.id, sede_a.id)
        ledger.rebuild(tenant_a.id, sede_a.id)
        assert ledger.projection_totals(tenant_a.id, sede_a.id) == before
    del item


def test_receiving_short_settles_the_order_partially(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 8 · under-delivery settles at **Recibida parcial** and leaves
    the shortfall on the line."""
    _item, _seller, order = sent_order(tenant_a, sede_a)
    opened = (
        client_as(owner_a)
        .post(
            "/api/goods-receipts",
            data={"purchase_order_id": str(order.id)},
            content_type="application/json",
        )
        .json()
    )
    line = opened["lines"][0]
    short = line["quantity"] - 20

    client_as(owner_a).patch(
        f"/api/goods-receipts/{opened['id']}",
        data={
            "lines": [
                {
                    "id": line["id"],
                    "item_id": line["item_id"],
                    "purchase_order_line_id": line["purchase_order_line_id"],
                    "quantity": short,
                    "lot_code": "L-2291",
                    "expires_at": "2027-03-01",
                }
            ]
        },
        content_type="application/json",
    )
    client_as(owner_a).post(f"/api/goods-receipts/{opened['id']}/confirm")

    with pin_tenant(tenant_a.id):
        fresh = PurchaseOrder.objects.get(id=order.id)
        assert fresh.status == PurchaseOrderStatus.PARTIALLY_RECEIVED
        assert (
            PurchaseOrderLine.objects.get(purchase_order=fresh).received_quantity
            == short
        )
        assert Lot.objects.filter(lot_code="L-2291").exists()


def test_over_delivery_is_accepted_and_reaches_the_shelf(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 9 · **never refused.** The supplier sent them, they are on the
    shelf, and a receiving screen that refuses reality gets bypassed with a
    manual adjustment."""
    item, _seller, order = sent_order(tenant_a, sede_a)
    opened = (
        client_as(owner_a)
        .post(
            "/api/goods-receipts",
            data={"purchase_order_id": str(order.id)},
            content_type="application/json",
        )
        .json()
    )
    line = opened["lines"][0]
    over = line["quantity"] + 20

    client_as(owner_a).patch(
        f"/api/goods-receipts/{opened['id']}",
        data={
            "lines": [
                {
                    "id": line["id"],
                    "item_id": line["item_id"],
                    "purchase_order_line_id": line["purchase_order_line_id"],
                    "quantity": over,
                    "lot_code": "L-9",
                    "expires_at": "2027-05-01",
                }
            ]
        },
        content_type="application/json",
    )
    client_as(owner_a).post(f"/api/goods-receipts/{opened['id']}/confirm")

    with pin_tenant(tenant_a.id):
        held = sum(
            row.quantity
            for row in StockOnHand.objects.filter(location=sede_a, item=item)
        )
        assert held == 10 + over
        assert PurchaseOrder.objects.get(id=order.id).status == (
            PurchaseOrderStatus.RECEIVED
        )


def test_confirming_twice_moves_stock_exactly_once(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 10 · `stock_on_hand` after the second confirmation equals
    `stock_on_hand` after the first."""
    _item, _seller, order = sent_order(tenant_a, sede_a)
    opened = (
        client_as(owner_a)
        .post(
            "/api/goods-receipts",
            data={"purchase_order_id": str(order.id)},
            content_type="application/json",
        )
        .json()
    )
    client_as(owner_a).post(f"/api/goods-receipts/{opened['id']}/confirm")
    with pin_tenant(tenant_a.id):
        after_first = ledger.projection_totals(tenant_a.id, sede_a.id)
    client_as(owner_a).post(f"/api/goods-receipts/{opened['id']}/confirm")
    with pin_tenant(tenant_a.id):
        assert ledger.projection_totals(tenant_a.id, sede_a.id) == after_first


def test_a_lot_tracked_line_without_a_code_is_refused_on_its_own_box(
    client_as, owner_a, tenant_a, sede_a
):
    """§B.10.3 · one refused line of a multi-line entry names the line and the
    control, so the surface marks that box rather than blocking the page."""
    _item, _seller, order = sent_order(tenant_a, sede_a)
    opened = (
        client_as(owner_a)
        .post(
            "/api/goods-receipts",
            data={"purchase_order_id": str(order.id)},
            content_type="application/json",
        )
        .json()
    )
    line = opened["lines"][0]
    client_as(owner_a).patch(
        f"/api/goods-receipts/{opened['id']}",
        data={
            "lines": [
                {
                    "id": line["id"],
                    "item_id": line["item_id"],
                    "quantity": 5,
                    "lot_code": "",
                }
            ]
        },
        content_type="application/json",
    )
    refused = client_as(owner_a).post(f"/api/goods-receipts/{opened['id']}/confirm")
    assert refused.status_code == 422
    body = refused.json()
    assert body["field"] == "lot_code"
    assert body["line"] == 1
    with pin_tenant(tenant_a.id):
        assert GoodsReceipt.objects.get(id=opened["id"]).status == (
            GoodsReceiptStatus.DRAFT
        )


def test_cost_and_lead_time_are_observed_rather_than_quoted(
    client_as, owner_a, tenant_a, sede_a
):
    """Acceptance 12 · a confirmed receipt updates `supplier_items.cost` from
    what was paid and `suppliers.lead_time_days` from what the delivery took."""
    item, seller, order = sent_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        PurchaseOrder.objects.filter(id=order.id).update(
            sent_at=timezone.now() - timezone.timedelta(days=4)
        )
    opened = (
        client_as(owner_a)
        .post(
            "/api/goods-receipts",
            data={"purchase_order_id": str(order.id)},
            content_type="application/json",
        )
        .json()
    )
    line = opened["lines"][0]
    client_as(owner_a).patch(
        f"/api/goods-receipts/{opened['id']}",
        data={
            "lines": [
                {
                    "id": line["id"],
                    "item_id": line["item_id"],
                    "purchase_order_line_id": line["purchase_order_line_id"],
                    "quantity": line["quantity"],
                    "lot_code": "L-77",
                    "expires_at": "2028-01-01",
                    "unit_cost": "4100.00",
                }
            ]
        },
        content_type="application/json",
    )
    client_as(owner_a).post(f"/api/goods-receipts/{opened['id']}/confirm")

    with pin_tenant(tenant_a.id):
        assert SupplierItem.objects.get(supplier=seller, item=item).cost == Decimal(
            "4100.00"
        )
        observed = receiving.refresh_lead_time(tenant_a.id, seller.id)
        assert observed == 4
        assert Supplier.objects.get(id=seller.id).lead_time_days == 4
        assert Lot.objects.get(lot_code="L-77").unit_cost == Decimal("4100.00")


def test_a_supplier_return_takes_stock_off_the_shelf(
    client_as, owner_a, tenant_a, sede_a
):
    """The ledger assigns `supplier_return` moves to S6, and this stage models
    them as a receipt with the sign reversed -- the same document, the same
    lots, the same one call into the ledger."""
    item, seller, _order = sent_order(tenant_a, sede_a)
    opened = client_as(owner_a).post(
        "/api/goods-receipts",
        data={
            "type": "supplier_return",
            "location_id": str(sede_a.id),
            "supplier_id": str(seller.id),
            "lines": [
                {
                    "item_id": str(item.id),
                    "quantity": 4,
                    "lot_code": "R-1",
                    "expires_at": "2028-02-01",
                }
            ],
        },
        content_type="application/json",
    )
    assert opened.status_code == 200
    receipt = opened.json()
    client_as(owner_a).post(f"/api/goods-receipts/{receipt['id']}/confirm")

    with pin_tenant(tenant_a.id):
        moves = StockMove.objects.filter(document_id=receipt["id"])
        assert moves.count() == 1
        assert moves.first().type == StockMoveType.SUPPLIER_RETURN
        assert moves.first().quantity == -4


def test_a_receipt_cannot_be_opened_without_an_order_or_a_return(
    client_as, owner_a, tenant_a, sede_a
):
    """Merchandise arriving from nowhere is S3's `Cargar mercancía`, which
    records the reason it requires."""
    response = client_as(owner_a).post(
        "/api/goods-receipts",
        data={"type": "receipt", "location_id": str(sede_a.id)},
        content_type="application/json",
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Manual orders, the settings group and the role gate (acceptance 21)
# ---------------------------------------------------------------------------


def test_a_manual_order_proposes_nothing(client_as, owner_a, tenant_a, sede_a):
    """**`suggested_quantity` is null on every line, not zero.** A zero would
    enter the deviation measurement as a proposal of nothing."""
    item = make_item(tenant_a, "Gasa estéril", tracks_lots=False)
    seller = supplier(tenant_a)
    response = client_as(owner_a).post(
        "/api/purchase-orders",
        data={
            "location_id": str(sede_a.id),
            "supplier_id": str(seller.id),
            "lines": [{"item_id": str(item.id), "quantity": 12}],
        },
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["source"] == "manual"
    with pin_tenant(tenant_a.id):
        line = PurchaseOrderLine.objects.get(purchase_order_id=response.json()["id"])
    assert line.suggested_quantity is None
    assert line.approved_quantity == 12


def test_a_cashier_reaches_nothing_in_this_stage(client_as, cashier_a):
    """Acceptance 21 · Compras is not on that role's nav and a direct URL
    refuses naming the role required -- not a redirect, not a blank page."""
    for path in (
        "/api/purchase-orders",
        "/api/goods-receipts",
        "/api/demand-forecasts?location_id=" + "0" * 8 + "-0000-0000-0000-000000000000",
        "/api/settings/purchasing",
    ):
        response = client_as(cashier_a).get(path)
        assert response.status_code == 403, path
        assert "Propietaria" in response.json()["detail"]


def test_the_settings_group_writes_one_key_group_and_no_other(
    client_as, owner_a, tenant_a
):
    """Rule 5 · one `jsonb_set`, every other group byte-identical."""
    with pin_tenant(tenant_a.id):
        tenant = Tenant.objects.get(id=tenant_a.id)
        tenant.settings = {"tenant": {"legal_name": "Droguerías La 45 S.A.S."}}
        tenant.save(update_fields=["settings"])

    response = client_as(owner_a).patch(
        "/api/settings/purchasing",
        data={"target_coverage_days": 45},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["target_coverage_days"] == 45

    with pin_tenant(tenant_a.id):
        fresh = Tenant.objects.get(id=tenant_a.id)
    assert fresh.settings["tenant"] == {"legal_name": "Droguerías La 45 S.A.S."}
    assert fresh.settings["purchasing"]["target_coverage_days"] == 45


def test_the_two_promotion_thresholds_may_not_meet(client_as, owner_a):
    """Hysteresis, or the regime flickers from Monday to Tuesday and an
    administrator learns to distrust both readings."""
    response = client_as(owner_a).patch(
        "/api/settings/purchasing",
        data={"learned_max_rse": 0.4, "learned_demote_rse": 0.4},
        content_type="application/json",
    )
    assert response.status_code == 422
    assert "cada mañana" in response.json()["detail"]


def test_the_nav_counter_counts_work_waiting_and_nothing_else(
    client_as, owner_a, tenant_a, sede_a
):
    """§B.8.2 · **órdenes sugeridas**, never a total of every order, and zero
    renders nothing at all."""
    counters = client_as(owner_a).get("/api/nav-counters").json()["counters"]
    assert counters["purchasing"] == 0

    _item, _seller, order = build_order(tenant_a, sede_a)
    assert (
        client_as(owner_a).get("/api/nav-counters").json()["counters"]["purchasing"]
        == 1
    )
    client_as(owner_a).post(f"/api/purchase-orders/{order.id}/discard")
    assert (
        client_as(owner_a).get("/api/nav-counters").json()["counters"]["purchasing"]
        == 0
    )


def test_a_second_generation_on_one_day_updates_the_order_in_place(tenant_a, sede_a):
    """*Jobs* · a second run **never creates a second order**, and an
    administrator's edits from an hour ago are not overwritten."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    line = order.lines.get()
    with pin_tenant(tenant_a.id):
        order_service.set_line_quantity(order, line, 7)
        again = order_service.generate(tenant_a.id, sede_a.id)
        assert [one.id for one in again] == [order.id]
        assert PurchaseOrder.objects.count() == 1
        assert PurchaseOrderLine.objects.get(id=line.id).approved_quantity == 7


def test_generation_never_touches_an_order_past_suggested(tenant_a, sede_a):
    """The 05:00 job's own retry must not reopen an order somebody approved."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order_service.approve(order, actor=None)
        order_service.generate(tenant_a.id, sede_a.id)
        assert PurchaseOrder.objects.get(id=order.id).status == (
            PurchaseOrderStatus.APPROVED
        )
        # A fresh `suggested` order may be created beside it; the approved one
        # is untouched, which is the invariant.
        assert (
            PurchaseOrder.objects.filter(status=PurchaseOrderStatus.APPROVED).count()
            == 1
        )


def test_stock_on_order_is_not_ordered_twice(tenant_a, sede_a):
    """`on_order` counts outstanding lines on approved and sent orders, so two
    consecutive mornings do not order the same shortfall twice."""
    _item, _seller, order = build_order(tenant_a, sede_a)
    with pin_tenant(tenant_a.id):
        order_service.approve(order, actor=None)
        second = order_service.generate(tenant_a.id, sede_a.id)
    proposed = sum(
        line.suggested_quantity or 0 for one in second for line in one.lines.all()
    )
    assert proposed == 0


def test_a_second_sede_is_never_read_by_the_first(client_as, owner_a, tenant_a, sede_a):
    """A2 · the location-scoping helper, not a filter this stage wrote."""
    other = make_location(tenant_a, "SUB", "Suba")
    _item, _seller, order = build_order(tenant_a, sede_a)
    body = client_as(owner_a).get(f"/api/purchase-orders?location_id={other.id}").json()
    assert body["row_count"] == 0
    del order


def test_generate_returns_the_handle_and_not_the_orders(
    client_as, owner_a, tenant_a, sede_a
):
    """*API surface* · **generation is asynchronous.** A request that waited for
    twenty sedes' forecasts would time out on the one screen that has a button
    for it, so the endpoint answers with what it queued."""
    del tenant_a
    response = client_as(owner_a).post(
        "/api/purchase-orders/generate",
        data={"location_id": str(sede_a.id)},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 1
    assert body["location_ids"] == [str(sede_a.id)]


def test_the_receipts_list_answers_by_order_and_by_type(
    client_as, owner_a, tenant_a, sede_a
):
    _item, _seller, order = sent_order(tenant_a, sede_a)
    opened = (
        client_as(owner_a)
        .post(
            "/api/goods-receipts",
            data={"purchase_order_id": str(order.id)},
            content_type="application/json",
        )
        .json()
    )
    listed = (
        client_as(owner_a)
        .get(f"/api/goods-receipts?purchase_order_id={order.id}&type=receipt")
        .json()
    )
    assert listed["row_count"] == 1
    assert listed["rows"][0]["id"] == opened["id"]
    assert listed["rows"][0]["order_number"] == order.number

    returns = client_as(owner_a).get("/api/goods-receipts?type=supplier_return").json()
    assert returns["row_count"] == 0


def test_a_confirmed_receipt_cannot_be_edited_afterwards(
    client_as, owner_a, tenant_a, sede_a
):
    """It moved stock. A correction is an adjustment in Inventario or a
    supplier return -- both of which append rather than rewrite (A3)."""
    _item, _seller, order = sent_order(tenant_a, sede_a)
    opened = open_receipt(client_as, owner_a, order)
    type_receipt(client_as, owner_a, opened)
    confirmed = client_as(owner_a).post(f"/api/goods-receipts/{opened['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.json()
    refused = client_as(owner_a).patch(
        f"/api/goods-receipts/{opened['id']}",
        data={"notes": "otra cosa"},
        content_type="application/json",
    )
    assert refused.status_code == 409


def test_the_forecast_endpoint_reads_one_sede_at_a_time(
    client_as, owner_a, tenant_a, sede_a
):
    _item, _seller, _order = build_order(tenant_a, sede_a)
    body = (
        client_as(owner_a).get(f"/api/demand-forecasts?location_id={sede_a.id}").json()
    )
    assert body["row_count"] >= 1
    row = body["rows"][0]
    assert row["basis"] in ("parametric", "learning", "learned")
    assert row["band"] in ("alta", "media", "baja")
    assert row["model_version"]
