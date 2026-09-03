"""The office's read of the counter, and its two corrections.

The office gets a list, a record panel, a void and a forced close, and nothing
else: every sale, line, payment, shift and return originates on a device. What
is checked here is that the scoping is S0's helper rather than a filter written
in this stage, that both mutations land an `audit_log` row, and that a forced
close is never rendered as a count.
"""

from decimal import Decimal

import pytest

from core.counter import sales as sale_service
from core.models import (
    AuditLog,
    Sale,
    SaleStatus,
    Shift,
    ShiftStatus,
    StockMoveType,
    StockMove,
    StockOnHand,
)
from core.tenancy import pin_tenant
from core.tests.conftest import make_location, make_user
from core.tests.test_counter_push import Till, apply, price, stock
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


def ring(tenant, location, user, *, quantity=4, unit_price="3900", label="Caja 1"):
    """One closed ticket, rung the way a till rings it."""
    device, _key = make_device(tenant, location, label=label)
    item = make_item(tenant, f"Producto {label} {location.code}", tracks_lots=True)
    lot = make_lot(tenant, item, code=f"L-{location.code}{label[-1]}")
    price(tenant, item, unit_price)
    stock(tenant, location, item, lot, 100)
    till = Till(device, user)
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, quantity, unit_price, lot=lot),
            till.payment("cash", str(Decimal(unit_price) * quantity)),
            till.close_sale(),
        ],
        batch_id=f"batch-{location.code}-{label}",
    )
    with pin_tenant(tenant.id):
        return Sale.objects.get(tenant=tenant, device=device), item, lot


def test_the_office_reads_every_sede_and_a_cashier_reads_one(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """Acceptance 24 · a `cashier` reaching another sede gets nothing for it; an
    `owner` sees every sede in one query (A2). The scoping is S0's helper and
    not a filter written here."""
    suba = make_location(tenant_a, "SUB", "Suba")
    other = make_user(tenant_a, "cashier", "suba@la45.co", location=suba)
    ring(tenant_a, sede_a, cashier_a)
    ring(tenant_a, suba, other, label="Caja 2")

    owner = client_as(owner_a).get("/api/sales").json()
    assert owner["row_count"] == 2
    assert {row["location_name"] for row in owner["rows"]} == {"Chapinero", "Suba"}

    scoped = client_as(cashier_a).get("/api/sales").json()
    assert scoped["row_count"] == 1
    assert scoped["rows"][0]["location_name"] == "Chapinero"

    # An explicit filter outside the identity's set is **rejected, not
    # intersected away**: a silently emptied result is indistinguishable from a
    # sede with nothing in it.
    denied = client_as(cashier_a).get(f"/api/sales?location_id={suba.id}")
    assert denied.status_code == 403


def test_the_list_carries_what_the_drawn_columns_read(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """`Venta · Sede · Turno · Fecha · Ítems · Total · Medio · Estado`, and the
    two aggregates behind `Ítems` and `Medio` are over the page rather than per
    row."""
    ring(tenant_a, sede_a, cashier_a)
    row = client_as(owner_a).get("/api/sales").json()["rows"][0]
    assert row["item_count"] == 4
    assert row["methods"] == ["cash"]
    assert row["status"] == "closed"
    assert row["shift_id"]
    assert row["device_label"] == "Caja 1"
    assert row["returned"] is False


def test_a_void_reverses_the_stock_and_deletes_nothing(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """The reversing moves go through S3's service and `status` becomes
    `voided`. **No row is ever deleted.**"""
    sale, item, lot = ring(tenant_a, sede_a, cashier_a)
    with pin_tenant(tenant_a.id):
        before = StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity

    response = client_as(owner_a).post(
        f"/api/sales/{sale.id}/void",
        data={"reason": "Tiquete mal digitado"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "voided"

    with pin_tenant(tenant_a.id):
        sale.refresh_from_db()
        assert sale.status == SaleStatus.VOIDED
        assert sale.lines.count() == 1
        assert StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity == before + 4
        reversal = StockMove.objects.get(
            tenant=tenant_a, type=StockMoveType.CUSTOMER_RETURN
        )
        # It is not a customer returning anything and does not pretend to be:
        # the move names the voided sale as its document.
        assert reversal.document_type == "sales"
        assert reversal.document_id == sale.id


def test_a_void_lands_an_audit_row_with_both_values(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """Acceptance 25 · every mutation an `owner` or `admin` makes through this
    stage's endpoints carries the actor, the entity and both values."""
    sale, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    client_as(owner_a).post(
        f"/api/sales/{sale.id}/void",
        data={"reason": "Tiquete mal digitado"},
        content_type="application/json",
    )
    with pin_tenant(tenant_a.id):
        row = AuditLog.objects.get(entity_type="sales")
        assert row.actor_user_id == owner_a.id
        assert row.before["status"] == "closed"
        assert row.after["status"] == "voided"
        assert row.after["reason"] == "Tiquete mal digitado"


def test_a_cashier_cannot_void_through_the_office_endpoint(
    tenant_a, sede_a, cashier_a, client_as
):
    """A permissive void is how a till is robbed. The cashier's own same-shift
    void is a client write and does not call this."""
    sale, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    response = client_as(cashier_a).post(
        f"/api/sales/{sale.id}/void", data={}, content_type="application/json"
    )
    assert response.status_code == 403


def test_the_turno_list_restates_its_own_arithmetic(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """`Diferencia` is recomputed on every read rather than trusted from the
    stored figure, because an offline sale attributed to that turno may have
    arrived since. The two agree on a drained till, which is the point of
    showing both."""
    ring(tenant_a, sede_a, cashier_a)
    row = client_as(owner_a).get("/api/shifts").json()["rows"][0]
    assert row["opening_float"] == "150000.00"
    assert row["cash_sales"] == "15600.00"
    assert row["expected_total"] == "165600.00"
    assert row["declared_total"] is None
    assert row["variance"] is None
    assert row["status"] == "open"
    assert row["sale_count"] == 1


def test_a_forced_close_is_not_a_count_and_is_never_rendered_as_one(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """`closed_at` is set and `declared_total` and `variance` stay **null**. A
    zero here would claim an empty drawer was counted, which is a different and
    much worse thing to record."""
    ring(tenant_a, sede_a, cashier_a)
    with pin_tenant(tenant_a.id):
        shift = Shift.objects.get(tenant=tenant_a)

    refused = client_as(owner_a).post(
        f"/api/shifts/{shift.id}/force-close",
        data={"reason": "   "},
        content_type="application/json",
    )
    assert refused.status_code == 422

    response = client_as(owner_a).post(
        f"/api/shifts/{shift.id}/force-close",
        data={"reason": "El equipo se dañó y la cajera se fue"},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "closed"
    assert body["declared_total"] is None
    assert body["variance"] is None
    assert body["forced_close_reason"].startswith("El equipo")

    with pin_tenant(tenant_a.id):
        shift.refresh_from_db()
        assert shift.status == ShiftStatus.CLOSED
        assert shift.closed_at is not None
        assert AuditLog.objects.filter(entity_type="shifts").count() == 1


def test_the_shift_record_panel_breaks_the_takings_down_by_method(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """A split ticket is what makes the cash figure readable as a share rather
    than as the whole."""
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a, "Omeprazol 20 mg × 30", tracks_lots=True)
    lot = make_lot(tenant_a, item, code="O-5027")
    price(tenant_a, item, "10000")
    stock(tenant_a, sede_a, item, lot, 20)
    till = Till(device, cashier_a)
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 2, "10000", lot=lot),
            till.payment("cash", "12000"),
            till.payment("debit_card", "8000", reference="REF001"),
            till.close_sale(),
        ],
    )
    with pin_tenant(tenant_a.id):
        shift = Shift.objects.get(tenant=tenant_a)
    body = client_as(owner_a).get(f"/api/shifts/{shift.id}").json()
    assert {one["method"]: one["amount"] for one in body["payments"]} == {
        "cash": "12000.00",
        "debit_card": "8000.00",
    }
    assert body["cash_sales"] == "12000.00"
    assert len(body["sales"]) == 1


def test_the_nav_counter_is_ventas_abiertas_for_the_office_and_absent_for_a_till(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """§B.8.2, A4 · a `cashier` reads the same number from their own local store
    at zero latency and never asks the server for it."""
    device, _key = make_device(tenant_a, sede_a)
    till = Till(device, cashier_a)
    apply(device, [till.open_shift(), till.open_sale()])

    assert client_as(owner_a).get("/api/nav-counters").json()["counters"] == {
        "counter": 1
    }
    assert client_as(cashier_a).get("/api/nav-counters").json()["counters"] == {}
    with pin_tenant(tenant_a.id):
        assert sale_service.open_sales(tenant_a.id, [sede_a.id]) == 1


def test_the_two_networks_cannot_read_each_others_counters(
    tenant_a, tenant_b, sede_a, owner_a, cashier_a, client_as
):
    """A1 · the tenant is the security boundary, and the counter's tables are
    inside it like every other."""
    ring(tenant_a, sede_a, cashier_a)
    other_sede = make_location(tenant_b, "EST", "La Estrella")
    other_cashier = make_user(
        tenant_b, "cashier", "cashier@estrella.co", location=other_sede
    )
    other_owner = make_user(tenant_b, "owner", "owner@estrella.co")
    ring(tenant_b, other_sede, other_cashier)

    assert client_as(owner_a).get("/api/sales").json()["row_count"] == 1
    assert client_as(other_owner).get("/api/sales").json()["row_count"] == 1


def test_a_cashier_must_name_who_they_are_looking_for(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """The role reaches `/api/customers` so the counter can attach an acquirer,
    which is always a search for one person.

    An unfiltered page would hand a till the network's whole customer table with
    every document number, phone, email and address on it — the data S2's
    recency window and its departure scrub exist to keep off a device.
    """
    from core.models import Customer

    with pin_tenant(tenant_a.id):
        Customer.objects.create(
            tenant=tenant_a, document_type="CC", document="1020304050", name="Ana Gómez"
        )

    assert client_as(cashier_a).get("/api/customers").json()["row_count"] == 0
    assert client_as(cashier_a).get("/api/customers?q=10").json()["row_count"] == 0
    found = client_as(cashier_a).get("/api/customers?q=102030").json()
    assert found["row_count"] == 1
    # And the office reads the list unfiltered, or the check above proves
    # nothing about the role rather than about the endpoint.
    assert client_as(owner_a).get("/api/customers").json()["row_count"] == 1


def test_a_cashier_never_receives_a_cost_figure(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """S1 fixed the rule on the catalog and this record panel would otherwise
    walk around it: a ticket's lines carry the lot's acquisition cost, stamped
    at the moment of sale."""
    sale, _item, _lot = ring(tenant_a, sede_a, cashier_a)
    scoped = client_as(cashier_a).get(f"/api/sales/{sale.id}").json()
    assert scoped["lines"][0]["unit_cost"] is None
    elevated = client_as(owner_a).get(f"/api/sales/{sale.id}").json()
    assert elevated["lines"][0]["unit_cost"] is not None


def test_the_sale_list_narrows_to_one_cashier(
    tenant_a, sede_a, owner_a, cashier_a, client_as
):
    """The `cashier` filter the API surface names: an owner asking who rang
    which ticket."""
    ring(tenant_a, sede_a, cashier_a)
    body = client_as(owner_a).get(f"/api/sales?sold_by_user_id={cashier_a.id}").json()
    assert body["row_count"] == 1
    other = client_as(owner_a).get(f"/api/sales?sold_by_user_id={owner_a.id}").json()
    assert other["row_count"] == 0
