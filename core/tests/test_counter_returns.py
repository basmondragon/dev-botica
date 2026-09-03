"""Devoluciones: what the stock does, and what the money does.

Two rules carry this whole surface, and both are about *when* a figure was
decided. The stock goes back **to the lot the line originally sold**, or a
recall becomes unanswerable (§6). The money is stamped **from the original sale
line**, not from today's price list, because a credit note must reverse what was
charged (§5).
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from core.counter import sales as sale_service
from core.models import (
    ItemPrice,
    Sale,
    SaleReturn,
    SaleReturnLine,
    SaleStatus,
    StockMove,
    StockMoveType,
    StockOnHand,
)
from core.sync import push
from core.tenancy import pin_tenant
from core.tests.test_counter_push import Till, apply, envelope, price, stock
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


@pytest.fixture
def sold(tenant_a, sede_a, cashier_a):
    """A closed sale of four units, on a lot, at $3.900."""
    device, _key = make_device(tenant_a, sede_a)
    item = make_item(tenant_a, "Acetaminofén 500 mg × 100", tracks_lots=True)
    lot = make_lot(tenant_a, item, code="A-2291")
    price(tenant_a, item, "3900")
    stock(tenant_a, sede_a, item, lot, 40)
    till = Till(device, cashier_a)
    apply(
        device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 4, "3900", lot=lot),
            till.payment("cash", "15600"),
            till.close_sale(),
        ],
    )
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a)
        line = sale.lines.get()
    return {
        "till": till,
        "device": device,
        "item": item,
        "lot": lot,
        "sale": sale,
        "line": line,
    }


def return_batch(sold, quantity, number="C1D-1", **extra):
    till = sold["till"]
    return [
        envelope(
            "sale_returns",
            {
                "sale_id": str(sold["sale"].id),
                "number": number,
                "shift_id": till.shift_key,
                "reason": "El cliente compró la presentación equivocada.",
                "refund_method": "cash",
                "returned_by_user_id": str(till.user.id),
                **extra,
            },
        ),
        envelope(
            "sale_return_lines",
            {
                "sale_return_id": None,
                "sale_line_id": str(sold["line"].id),
                "quantity": quantity,
            },
        ),
    ]


def send_return(sold, quantity, number="C1D-1"):
    """The header and its line, in one batch, with the line naming the header's
    id the way a till does."""
    rows = return_batch(sold, quantity, number=number)
    header_id = rows[0]["payload"].setdefault(
        "id", str(sold["sale"].id).replace(str(sold["sale"].id)[0], "a", 1)
    )
    rows[1]["payload"]["sale_return_id"] = header_id
    return apply(sold["device"], rows, batch_id=number)


def test_a_partial_return_puts_the_units_back_on_the_original_lot(tenant_a, sold):
    """Acceptance 17 · two of four units come back, and they go back on the lot
    they left on."""
    with pin_tenant(tenant_a.id):
        before = StockOnHand.objects.get(tenant=tenant_a, lot=sold["lot"]).quantity

    result = send_return(sold, 2)
    assert [one.outcome for one in result.outcomes] == [push.APPLIED] * 2

    with pin_tenant(tenant_a.id):
        assert (
            StockOnHand.objects.get(tenant=tenant_a, lot=sold["lot"]).quantity
            == before + 2
        )
        move = StockMove.objects.get(
            tenant=tenant_a, type=StockMoveType.CUSTOMER_RETURN
        )
        assert move.lot_id == sold["lot"].id
        assert move.quantity == 2
        assert move.document_type == "sale_returns"


def test_the_refund_is_the_money_that_was_charged_and_not_todays_price(tenant_a, sold):
    """Acceptance 17 · the item's price changes between the sale and the return,
    and the refund does not move. **A credit note must reverse what was
    charged.**"""
    with pin_tenant(tenant_a.id):
        ItemPrice.objects.filter(tenant=tenant_a, item=sold["item"]).update(
            effective_to=timezone.localdate()
        )
    price(tenant_a, sold["item"], "9900", days_back=0)

    send_return(sold, 2)
    with pin_tenant(tenant_a.id):
        row = SaleReturnLine.objects.get(tenant=tenant_a)
        assert row.unit_price == Decimal("3900.00")
        header = SaleReturn.objects.get(tenant=tenant_a)
        assert header.total == Decimal("7800.00")


def test_the_sale_stays_closed_and_is_not_voided(tenant_a, sold):
    """A fully-returned sale is a closed sale with returns against it, not a
    voided one."""
    send_return(sold, 4)
    with pin_tenant(tenant_a.id):
        sold["sale"].refresh_from_db()
        assert sold["sale"].status == SaleStatus.CLOSED
        assert sale_service.returnable(sold["sale"]) == {sold["line"].id: 0}


def test_returning_more_than_remains_is_refused_on_its_own_row(tenant_a, sold):
    """The stepper is capped at what remains returnable; the server holds the
    same line, because a client is a browser."""
    send_return(sold, 3)
    result = send_return(sold, 2, number="C1D-2")
    assert result.outcomes[1].outcome == push.REJECTED
    with pin_tenant(tenant_a.id):
        assert SaleReturnLine.objects.filter(tenant=tenant_a).count() == 1


def test_a_return_against_an_open_sale_is_refused(tenant_a, sede_a, cashier_a):
    """Only a **closed** sale can be returned against: an open ticket has moved
    no stock and taken no money."""
    device, _key = make_device(tenant_a, sede_a)
    till = Till(device, cashier_a)
    apply(device, [till.open_shift(), till.open_sale()])
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a)
    result = apply(
        device,
        [
            envelope(
                "sale_returns",
                {
                    "sale_id": str(sale.id),
                    "number": "C1D-1",
                    "reason": "x",
                    "refund_method": "cash",
                },
            )
        ],
        batch_id="return",
    )
    assert result.outcomes[0].outcome == push.REJECTED


def test_a_return_with_no_reason_is_refused(tenant_a, sold):
    """`Motivo` is required, and the table's own CHECK says so too: a
    devolución nobody explained is a refund nobody can audit."""
    rows = return_batch(sold, 1)
    rows[0]["payload"]["reason"] = "   "
    result = apply(sold["device"], rows, batch_id="no-reason")
    assert result.outcomes[0].outcome == push.REJECTED


def test_the_refund_leaves_the_drawer_that_is_open_now(tenant_a, sold):
    """Money leaves the drawer that is open now, whichever turno the sale was
    rung in — which is what makes the close report's `Devoluciones en efectivo`
    line the right number."""
    send_return(sold, 2)
    with pin_tenant(tenant_a.id):
        shift = sold["sale"].shift
        assert sale_service.cash_refunded(shift) == Decimal("7800.00")
        # $150.000 float + $15.600 taken − $7.800 refunded.
        assert sale_service.expected_cash(shift) == Decimal("157800.00")
