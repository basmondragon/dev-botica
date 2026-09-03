"""The offline sale, end to end through S2's push.

**No endpoint in this stage creates a counter sale**, so this file is where the
stage's write path actually lives: a till opens a turno, rings a ticket, closes
it with payments, and every one of those arrives as a batch on
`POST /api/sync/push`. What is checked here is what §5 promises — that the batch
is idempotent, that the stock moved through S3's service, that the two
reconciliations S4 owns are raised without correcting anything, and that a sale
is never refused for want of stock.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from core.counter import sales as sale_service
from core.inventory import ledger
from core.models import (
    Customer,
    ItemPrice,
    Payment,
    PriceSource,
    Sale,
    SaleLine,
    SaleStatus,
    Shift,
    ShiftStatus,
    StockMove,
    StockMoveType,
    StockOnHand,
    SyncConflict,
    SyncConflictType,
)
from core.sync import push
from core.sync import settings as sync_settings
from core.tenancy import pin_tenant
from core.tests.conftest import make_location, make_user
from core.tests.test_inventory_ledger import make_lot
from core.tests.test_sync_pull import make_device, make_item

pytestmark = pytest.mark.django_db


def options():
    return dict(sync_settings.DEFAULTS)


def apply(device, rows, batch_id="batch-1"):
    with pin_tenant(device.tenant_id):
        return push.apply_batch(
            device, batch_id, rows, options=options(), request_id="req_test"
        )


#: Envelope keys have to sort in the order a till minted them, because the push
#: applies a batch in `client_uuid` order and that is what puts a shift before
#: the sale that sits in it and a sale before its lines. A till mints uuid v7,
#: which sorts by time; a test that used uuid4 would scramble the batch and then
#: measure the scrambling.
_ORDINAL = {"next": 0}


def ordered_uuid() -> str:
    _ORDINAL["next"] += 1
    return f"01900000-0000-7000-8000-{_ORDINAL['next']:012d}"


def envelope(collection, payload, occurred_at=None, client_uuid=None):
    return {
        "collection": collection,
        "client_uuid": str(client_uuid or ordered_uuid()),
        "occurred_at": (occurred_at or timezone.now()).isoformat(),
        "payload": payload,
    }


def price(tenant, item, amount, location=None, days_back=30):
    return ItemPrice.objects.create(
        tenant=tenant,
        item=item,
        location=location,
        price=Decimal(amount),
        effective_from=timezone.localdate() - timedelta(days=days_back),
        source=PriceSource.MANUAL,
    )


def stock(tenant, location, item, lot, quantity, device=None):
    with pin_tenant(tenant.id):
        ledger.append(
            [
                ledger.Move(
                    location_id=location.id,
                    item_id=item.id,
                    lot_id=lot.id if lot else None,
                    quantity=quantity,
                    type=StockMoveType.ADJUSTMENT,
                    reason="opening_stock",
                    key=f"open:{location.id}:{item.id}:{lot.id if lot else 'none'}",
                )
            ],
            tenant_id=tenant.id,
            device=device,
        )


class Till:
    """What a till sends, in the order it sends it.

    A helper rather than eight literals per test: the *order* is the protocol —
    the shift before the sale that sits in it, the sale before its lines, the
    lines before the close — and a test that assembled it by hand each time
    would be checking its own typing.
    """

    def __init__(self, device, user):
        self.device = device
        self.user = user
        self.shift_key = str(uuid.uuid4())
        self.sale_key = str(uuid.uuid4())
        self.sale_id = str(uuid.uuid4())
        self.sequence = 1

    def open_shift(self, opening_float="150000"):
        return envelope(
            "shifts",
            {
                "client_uuid": self.shift_key,
                "id": self.shift_key,
                "status": "open",
                "user_id": str(self.user.id),
                "opening_float": opening_float,
            },
        )

    def close_shift(self, declared_total, opening_float="150000"):
        # The till sends the whole row on both events, which is what lets an
        # orphaned close reconstruct the drawer it belongs to rather than lose
        # the count.
        return envelope(
            "shifts",
            {
                "client_uuid": self.shift_key,
                "id": self.shift_key,
                "status": "closed",
                "user_id": str(self.user.id),
                "opening_float": opening_float,
                "declared_total": str(declared_total),
                "closed_at": timezone.now().isoformat(),
            },
        )

    def open_sale(self, number=None, occurred_at=None):
        return envelope(
            "sales",
            {
                "client_uuid": self.sale_key,
                "id": self.sale_id,
                "status": "open",
                "number": number or f"{self.device.code}-{self.sequence}",
                "shift_id": self.shift_key,
                "sold_by_user_id": str(self.user.id),
            },
            occurred_at=occurred_at,
        )

    def line(self, item, quantity, unit_price, lot=None, position=0, **extra):
        return envelope(
            "sale_lines",
            {
                "sale_id": self.sale_id,
                "position": position,
                "item_id": str(item.id),
                "lot_id": str(lot.id) if lot else None,
                "quantity": quantity,
                "unit_price": str(unit_price),
                "vat_class": item.vat_class,
                **extra,
            },
        )

    def payment(self, method, amount, reference=""):
        return envelope(
            "payments",
            {
                "sale_id": self.sale_id,
                "method": method,
                "amount": str(amount),
                "reference": reference,
            },
        )

    def close_sale(self, **extra):
        return envelope(
            "sales",
            {
                "client_uuid": self.sale_key,
                "status": "closed",
                "number": f"{self.device.code}-{self.sequence}",
                "shift_id": self.shift_key,
                "sold_by_user_id": str(self.user.id),
                "closed_at": timezone.now().isoformat(),
                **extra,
            },
        )


@pytest.fixture
def till(tenant_a, sede_a, cashier_a):
    device, _key = make_device(tenant_a, sede_a)
    return Till(device, cashier_a)


@pytest.fixture
def product(tenant_a, sede_a):
    item = make_item(tenant_a, "Acetaminofén 500 mg × 100", tracks_lots=True)
    lot = make_lot(tenant_a, item, code="A-2291", days=400)
    price(tenant_a, item, "3900")
    stock(tenant_a, sede_a, item, lot, 50)
    return item, lot


# ---------------------------------------------------------------------------
# The document, in one batch
# ---------------------------------------------------------------------------


def test_a_closed_sale_arrives_as_one_document_and_moves_stock(
    tenant_a, sede_a, till, product
):
    """The unit is a document: the `sales` row, its lines, its payments — and
    the `sale` moves S3's ledger service appends as each line lands."""
    item, lot = product
    result = apply(
        till.device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 4, "3900", lot=lot),
            till.payment("cash", "15600"),
            till.close_sale(),
        ],
    )
    assert [one.outcome for one in result.outcomes] == [push.APPLIED] * 5

    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a)
        assert sale.status == SaleStatus.CLOSED
        assert sale.source == "counter"
        assert sale.shift_id is not None
        # **The server recomputes the totals from the lines it holds**, because
        # a till's totals are a browser's arithmetic.
        assert sale.subtotal == Decimal("15600.00")
        assert sale.total == Decimal("15600.00")
        assert sale.tax == Decimal("0.00")
        assert sale.sold_by_name

        move = StockMove.objects.get(tenant=tenant_a, type=StockMoveType.SALE)
        assert move.quantity == -4
        assert move.lot_id == lot.id
        assert move.document_type == "sales"
        assert move.document_id == sale.id
        # Rule 8 · the till owns the first clock and the server the second.
        assert move.occurred_at <= move.recorded_at
        assert StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity == 46


def test_the_same_batch_replayed_creates_nothing_and_moves_nothing(
    tenant_a, till, product
):
    """Acceptance 19 · a push that timed out after the server committed is
    retried and is a no-op. **This is the defect the whole stage exists to
    prevent.**"""
    item, lot = product
    batch = [
        till.open_shift(),
        till.open_sale(),
        till.line(item, 4, "3900", lot=lot),
        till.payment("cash", "15600"),
        till.close_sale(),
    ]
    apply(till.device, batch)
    with pin_tenant(tenant_a.id):
        held = StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity

    again = apply(till.device, batch, batch_id="batch-2")
    assert [one.outcome for one in again.outcomes] == [push.DUPLICATE] * 5
    with pin_tenant(tenant_a.id):
        assert Sale.objects.filter(tenant=tenant_a).count() == 1
        assert SaleLine.objects.filter(tenant=tenant_a).count() == 1
        assert Payment.objects.filter(tenant=tenant_a).count() == 1
        assert StockMove.objects.filter(type=StockMoveType.SALE).count() == 1
        assert StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity == held


def test_an_open_ticket_reaches_the_server_with_no_lines(tenant_a, till, product):
    """§5 · an open sale is pushed on the ordinary delta cadence and never per
    line, so it exists for `Mostrador 3` and moves no stock."""
    apply(till.device, [till.open_shift(), till.open_sale()])
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a)
        assert sale.status == SaleStatus.OPEN
        assert sale.lines.count() == 0
        assert StockMove.objects.filter(type=StockMoveType.SALE).count() == 0
        assert sale_service.open_sales(tenant_a.id, [sale.location_id]) == 1


def test_a_sale_outside_a_turno_is_refused_rather_than_invented(
    tenant_a, till, product
):
    """A counter sale outside a cash session cannot be reconciled, and the
    table's own `CHECK` refuses it. The row is rejected on its own — inventing a
    shift would make the cash arithmetic unanswerable."""
    result = apply(till.device, [till.open_sale()])
    assert result.outcomes[0].outcome == push.REJECTED
    with pin_tenant(tenant_a.id):
        assert Sale.objects.filter(tenant=tenant_a).count() == 0


def test_a_second_open_turno_on_one_device_is_refused(tenant_a, till):
    """The drawer belongs to the till. Two open drawers on one device is the
    state the partial unique index exists to refuse."""
    apply(till.device, [till.open_shift()])
    second = Till(till.device, till.user)
    result = apply(till.device, [second.open_shift()])
    assert result.outcomes[0].outcome == push.REJECTED
    with pin_tenant(tenant_a.id):
        assert Shift.objects.filter(status=ShiftStatus.OPEN).count() == 1


# ---------------------------------------------------------------------------
# Stock: never a refusal at a counter
# ---------------------------------------------------------------------------


def test_a_sale_of_stock_that_is_not_there_closes_and_raises_the_exception(
    tenant_a, sede_a, till
):
    """Acceptance 10, §5 rule 2 · the ticket closes with nothing shown at the
    counter, the projection goes negative, and the exception is S3's to raise.
    **No configuration makes the till refuse it.**"""
    item = make_item(tenant_a, "Ibuprofeno 400 mg × 50", tracks_lots=True)
    lot = make_lot(tenant_a, item, code="I-9004")
    price(tenant_a, item, "2500")
    stock(tenant_a, sede_a, item, lot, 1)

    result = apply(
        till.device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 5, "2500", lot=lot),
            till.payment("cash", "12500"),
            till.close_sale(),
        ],
    )
    assert [one.outcome for one in result.outcomes] == [push.APPLIED] * 5
    with pin_tenant(tenant_a.id):
        assert Sale.objects.get(tenant=tenant_a).status == SaleStatus.CLOSED
        assert StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity == -4
        assert SyncConflict.objects.filter(
            tenant=tenant_a, type=SyncConflictType.NEGATIVE_STOCK
        ).exists()


def test_the_tills_own_fefo_observation_is_believed(tenant_a, sede_a, till):
    """§6 · the counter showed the lot queue and watched a cashier pick past its
    head, and a server applying the sale three hours later would recompute the
    head against a projection that has moved since."""
    item = make_item(tenant_a, "Losartán 50 mg × 30", tracks_lots=True)
    near = make_lot(tenant_a, item, code="L-7730", days=60)
    far = make_lot(tenant_a, item, code="L-7731", days=600)
    price(tenant_a, item, "5200")
    stock(tenant_a, sede_a, item, near, 10)
    stock(tenant_a, sede_a, item, far, 10)

    apply(
        till.device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "5200", lot=far, fefo_override=True),
            till.payment("cash", "5200"),
            till.close_sale(),
        ],
    )
    with pin_tenant(tenant_a.id):
        move = StockMove.objects.get(tenant=tenant_a, type=StockMoveType.SALE)
        assert move.lot_id == far.id
        assert move.fefo_override is True


def test_a_service_on_a_ticket_moves_no_stock(tenant_a, till):
    """A7 · an item whose `tracks_stock` is false writes nothing at all — not a
    zero-quantity move, not a projection row."""
    service = make_item(
        tenant_a,
        "Toma de presión arterial",
        type="service",
        tracks_stock=False,
        tracks_lots=False,
        tracks_expiry=False,
        service_cost=Decimal("1000"),
    )
    price(tenant_a, service, "5000")
    apply(
        till.device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(service, 1, "5000"),
            till.payment("cash", "5000"),
            till.close_sale(),
        ],
    )
    with pin_tenant(tenant_a.id):
        assert Sale.objects.get(tenant=tenant_a).total == Decimal("5000.00")
        assert StockMove.objects.filter(type=StockMoveType.SALE).count() == 0
        line = SaleLine.objects.get(tenant=tenant_a)
        # Stamped from the item's own standing cost, which is what §3's "a
        # service is a product with no cost of goods unless one is entered"
        # needs a home for.
        assert line.unit_cost == Decimal("1000.00")


# ---------------------------------------------------------------------------
# The two reconciliations S4 owns (§5)
# ---------------------------------------------------------------------------


def test_a_price_that_moved_while_the_till_was_offline_is_reported_not_corrected(
    tenant_a, sede_a, till, product
):
    """Acceptance 30 · the sale stands at the price actually charged,
    `sale_lines.unit_price` is the record, and the difference is reported to the
    office."""
    item, lot = product
    with pin_tenant(tenant_a.id):
        ItemPrice.objects.filter(tenant=tenant_a, item=item).update(
            effective_to=timezone.localdate()
        )
    price(tenant_a, item, "4500", days_back=0)

    apply(
        till.device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot),
            till.payment("cash", "3900"),
            till.close_sale(),
        ],
    )
    with pin_tenant(tenant_a.id):
        line = SaleLine.objects.get(tenant=tenant_a)
        assert line.unit_price == Decimal("3900.00")
        assert Sale.objects.get(tenant=tenant_a).total == Decimal("3900.00")
        conflict = SyncConflict.objects.get(
            tenant=tenant_a, type=SyncConflictType.STALE_PRICE
        )
        assert conflict.detail["charged"] == "3900.00"
        assert conflict.detail["effective"] == "4500.00"
        assert conflict.detail["item_id"] == str(item.id)
        # **Never the payload verbatim** — S2's rule, and it applies to S4's
        # rows too.
        assert "unit_price" not in conflict.detail


def test_an_item_deactivated_while_the_till_was_offline_is_flagged_not_refused(
    tenant_a, sede_a, till, product
):
    """Acceptance 30 · a cashier who sold a box that was on the shelf is right
    about the world and the catalog is late."""
    item, lot = product
    with pin_tenant(tenant_a.id):
        item.active = False
        item.save(update_fields=["active"])

    apply(
        till.device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot),
            till.payment("cash", "3900"),
            till.close_sale(),
        ],
    )
    with pin_tenant(tenant_a.id):
        assert Sale.objects.get(tenant=tenant_a).status == SaleStatus.CLOSED
        conflict = SyncConflict.objects.get(
            tenant=tenant_a, type=SyncConflictType.CATALOG_DIVERGENCE
        )
        assert conflict.detail["state"] == "inactive"


# ---------------------------------------------------------------------------
# The acquirer, offline
# ---------------------------------------------------------------------------


def test_a_customer_registered_during_a_blackout_is_named_on_the_sale(
    tenant_a, till, product
):
    """Acceptance 29 · the customer and the sale travel in one batch, the
    customer applies first, and the sale names them."""
    item, lot = product
    customer_id = str(uuid.uuid4())
    result = apply(
        till.device,
        [
            till.open_shift(),
            envelope(
                "customers",
                {
                    "id": customer_id,
                    "document_type": "CC",
                    "document": "1020304050",
                    "name": "Ana Gómez",
                },
            ),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot),
            till.payment("cash", "3900"),
            till.close_sale(customer_id=customer_id),
        ],
    )
    assert push.REJECTED not in [one.outcome for one in result.outcomes]
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a)
        assert str(sale.customer_id) == customer_id


def test_a_merged_customer_is_still_the_person_the_sale_names(
    tenant_a, sede_a, till, product
):
    """Acceptance 29 · a second till registered the same document during the
    same blackout, so the server's row keeps a **different** id from the one
    this till chose. The sale names the person, not the id."""
    item, lot = product
    with pin_tenant(tenant_a.id):
        existing = Customer.objects.create(
            tenant=tenant_a, document_type="CC", document="1020304050", name="Ana Gómez"
        )
    local_id = str(uuid.uuid4())
    result = apply(
        till.device,
        [
            till.open_shift(),
            envelope(
                "customers",
                {
                    "id": local_id,
                    "document_type": "CC",
                    "document": "1020304050",
                    "name": "Ana G.",
                },
            ),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot),
            till.payment("cash", "3900"),
            till.close_sale(
                customer_id=local_id,
                customer_document_type="CC",
                customer_document="1020304050",
            ),
        ],
    )
    assert push.MERGED in [one.outcome for one in result.outcomes]
    with pin_tenant(tenant_a.id):
        assert Customer.objects.filter(tenant=tenant_a).count() == 1
        assert Sale.objects.get(tenant=tenant_a).customer_id == existing.id


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------


def test_two_tills_selling_offline_in_one_sede_do_not_collide(
    tenant_a, sede_a, cashier_a, product
):
    """Acceptance 18 · `sales.number` is composed `{código de caja}-{consecutivo}`
    and `devices.code` is unique network-wide, so the pair is unique by
    construction with no allocator and no lease."""
    item, lot = product
    first_device, _ = make_device(tenant_a, sede_a, label="Caja 1")
    second_device, _ = make_device(tenant_a, sede_a, label="Caja 2")
    assert first_device.code != second_device.code

    for device in (first_device, second_device):
        till = Till(device, cashier_a)
        apply(
            device,
            [
                till.open_shift(),
                till.open_sale(),
                till.line(item, 1, "3900", lot=lot),
                till.payment("cash", "3900"),
                till.close_sale(),
            ],
            batch_id=str(device.id),
        )

    with pin_tenant(tenant_a.id):
        numbers = list(Sale.objects.values_list("number", flat=True))
        assert len(numbers) == len(set(numbers)) == 2
        assert all(sale_service.valid_number(one) for one in numbers)


def test_a_number_that_is_not_composed_is_refused(tenant_a, till, product):
    """A bare integer could not have been allocated on a till with no
    connection, and it is the shape somebody would reach for first."""
    result = apply(till.device, [till.open_shift(), till.open_sale(number="4821")])
    assert result.outcomes[1].outcome == push.REJECTED


# ---------------------------------------------------------------------------
# The turno's arithmetic
# ---------------------------------------------------------------------------


def test_a_shift_closes_short_and_the_variance_is_stored_as_it_stands(
    tenant_a, till, product
):
    """Acceptance 15 · the drawer is $3.000 short, and **nothing offers to make
    it zero**."""
    item, lot = product
    apply(
        till.device,
        [
            till.open_shift(opening_float="150000"),
            till.open_sale(),
            till.line(item, 4, "3900", lot=lot),
            till.payment("cash", "15600"),
            till.close_sale(),
        ],
    )
    apply(
        till.device,
        [till.close_shift(Decimal("162600"))],
        batch_id="close",
    )
    with pin_tenant(tenant_a.id):
        shift = Shift.objects.get(tenant=tenant_a)
        assert shift.status == ShiftStatus.CLOSED
        assert sale_service.expected_cash(shift) == Decimal("165600.00")
        assert shift.declared_total == Decimal("162600.00")
        assert shift.variance == Decimal("-3000.00")


def test_a_sale_that_lands_after_the_close_is_still_attributed_to_that_turno(
    tenant_a, till, product
):
    """Acceptance 16 · a turno closes with operations still queued, and they
    arrive later against the same shift. **A cash count is not a sync gate.**"""
    item, lot = product
    apply(till.device, [till.open_shift()])
    apply(till.device, [till.close_shift(Decimal("150000"))], batch_id="close")

    late = Till(till.device, till.user)
    late.shift_key = till.shift_key
    result = apply(
        till.device,
        [
            late.open_sale(),
            late.line(item, 1, "3900", lot=lot),
            late.payment("cash", "3900"),
            late.close_sale(),
        ],
        batch_id="late",
    )
    assert push.REJECTED not in [one.outcome for one in result.outcomes]
    with pin_tenant(tenant_a.id):
        shift = Shift.objects.get(tenant=tenant_a)
        assert shift.sales.count() == 1
        # The stored variance is what the cashier counted against; the office's
        # own read recomputes the expectation, which is how a late sale becomes
        # visible rather than silently reconciled.
        assert shift.variance == Decimal("0.00")
        assert sale_service.expected_cash(shift) == Decimal("153900.00")


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_a_batch_naming_another_sede_is_rejected_whole(tenant_a, till, product):
    """Rule 6's fourth context: a client wrong about which sede it is at is a
    client whose other rows are not evidence of anything either."""
    item, lot = product
    other = make_location(tenant_a, "SUB", "Suba")
    make_user(tenant_a, "cashier", "suba@la45.co", location=other)
    row = till.open_shift()
    row["payload"]["location_id"] = str(other.id)
    # `check_provenance` runs over the **entire** batch before a single row is
    # applied, which is what the endpoint does and why a foreign row in the last
    # position rejects the first as surely as one in the first rejects the last.
    with pytest.raises(push.ForeignLocationRow):
        push.check_provenance(till.device, [till.open_sale(), row])
    with pin_tenant(tenant_a.id):
        assert Shift.objects.count() == 0


def test_a_discount_bigger_than_its_own_line_is_refused(tenant_a, till, product):
    """A discount larger than the line is not a discount.

    Left through it makes the line's net negative and the IVA contained in it
    negative with it, and `tax_is_contained_in_the_total` then refuses the whole
    ticket at the database — at the moment a cashier is holding a customer's
    money. One malformed line stays one malformed line.
    """
    item, lot = product
    result = apply(
        till.device,
        [
            till.open_shift(),
            till.open_sale(),
            till.line(item, 1, "3900", lot=lot, discount="9000"),
            till.close_sale(),
        ],
    )
    assert result.outcomes[2].outcome == push.REJECTED
    with pin_tenant(tenant_a.id):
        assert SaleLine.objects.filter(tenant=tenant_a).count() == 0
        # The ticket still closes: a rejected line is one line, not the sale.
        assert Sale.objects.get(tenant=tenant_a).status == SaleStatus.CLOSED


def test_a_late_sale_is_not_moved_into_the_drawer_that_is_open_now(
    tenant_a, till, product
):
    """Acceptance 16, the other half · a sale that arrives after its own turno
    closed stays in that turno.

    Re-attributing it to whatever is open now would put yesterday's takings into
    today's count and take them out of yesterday's — two cash reconciliations
    wrong from one row.
    """
    item, lot = product
    apply(till.device, [till.open_shift()])
    apply(till.device, [till.close_shift(Decimal("150000"))], batch_id="close")
    with pin_tenant(tenant_a.id):
        first = Shift.objects.get(tenant=tenant_a)

    # A new drawer is opened on the same till before the old sale drains.
    second = Till(till.device, till.user)
    apply(till.device, [second.open_shift()], batch_id="second")

    late = Till(till.device, till.user)
    late.shift_key = till.shift_key
    apply(
        till.device,
        [
            late.open_sale(),
            late.line(item, 1, "3900", lot=lot),
            late.payment("cash", "3900"),
            late.close_sale(),
        ],
        batch_id="late",
    )
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a)
        assert sale.shift_id == first.id
        assert (
            Shift.objects.filter(status=ShiftStatus.OPEN).exclude(id=first.id).count()
            == 1
        )


def test_a_till_may_only_name_its_own_drawer(tenant_a, sede_a, cashier_a, product):
    """Two tills at one sede hold each other's turnos in their local store, so a
    row naming the wrong one would put one till's takings in the other's cash
    count and leave both reconciliations wrong.

    The row is **not refused** — a sale is never lost over which drawer it is
    filed in. It falls back to whatever this device has open.
    """
    item, lot = product
    first_device, _ = make_device(tenant_a, sede_a, label="Caja 1")
    second_device, _ = make_device(tenant_a, sede_a, label="Caja 2")

    one = Till(first_device, cashier_a)
    apply(first_device, [one.open_shift()], batch_id="one")
    two = Till(second_device, cashier_a)
    apply(second_device, [two.open_shift()], batch_id="two")

    # The second till rings a ticket but names the first till's turno.
    stray = Till(second_device, cashier_a)
    stray.shift_key = one.shift_key
    apply(
        second_device,
        [
            stray.open_sale(),
            stray.line(item, 1, "3900", lot=lot),
            stray.payment("cash", "3900"),
            stray.close_sale(),
        ],
        batch_id="stray",
    )
    with pin_tenant(tenant_a.id):
        sale = Sale.objects.get(tenant=tenant_a)
        assert sale.shift.device_id == second_device.id
        assert sale_service.cash_taken(sale.shift) == Decimal("3900.00")
        other = Shift.objects.get(device=first_device)
        assert sale_service.cash_taken(other) == Decimal("0.00")


def test_a_close_that_arrives_alone_reconstructs_its_turno(tenant_a, till):
    """**A cash count is never lost to an ordering accident.**

    Ordering makes an orphaned close rare — uuid v7 puts the open event first —
    but it is reachable when the open event was itself rejected, and the client
    removes a rejected row from its outbox. Refusing here would lose the one
    number a turno exists to produce. The event carries the drawer's identity,
    its float and its count, so what is written is a reconstruction.
    """
    result = apply(till.device, [till.close_shift(Decimal("150000"))])
    assert result.outcomes[0].outcome == push.APPLIED
    with pin_tenant(tenant_a.id):
        shift = Shift.objects.get(tenant=tenant_a)
        assert shift.status == ShiftStatus.CLOSED
        assert shift.declared_total == Decimal("150000.00")
        assert shift.variance == Decimal("0.00")


def test_a_close_with_no_count_is_refused(tenant_a, till):
    """Storing a zero would claim the drawer was counted and found empty, which
    is exactly what the forced-close path refuses to do — and a forced close is
    the office's own decision, taken through its own endpoint with a reason."""
    apply(till.device, [till.open_shift()])
    row = till.close_shift(Decimal("0"))
    row["payload"]["declared_total"] = None
    result = apply(till.device, [row], batch_id="close")
    assert result.outcomes[0].outcome == push.REJECTED
    with pin_tenant(tenant_a.id):
        assert Shift.objects.get(tenant=tenant_a).status == ShiftStatus.OPEN


def test_a_void_does_not_credit_back_what_a_return_already_did(
    tenant_a, sede_a, till, product
):
    """A unit a devolución already put on the shelf, credited a second time by a
    void, is merchandise that never existed.

    Both callers refuse a void once anything has been returned; the arithmetic
    lives in the service as well, because a reversal that depended on its caller
    checking first is a reversal a third caller will get wrong.
    """
    item, lot = product
    apply(
        till.device,
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
        before = StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity
        from core.models import SaleReturn, SaleReturnLine

        header = SaleReturn.objects.create(
            tenant=tenant_a,
            sale=sale,
            location=sede_a,
            shift=sale.shift,
            number="C1D-1",
            reason="Caja sin abrir",
            refund_method="cash",
            client_uuid=uuid.uuid4(),
        )
        SaleReturnLine.objects.create(
            tenant=tenant_a,
            sale_return=header,
            sale_line=line,
            location=sede_a,
            item=item,
            lot=lot,
            quantity=3,
            unit_price=Decimal("3900"),
            vat_class=item.vat_class,
            client_uuid=uuid.uuid4(),
        )
        sale_service.void(sale, reason="Tiquete mal digitado")
        # Three units are already back on the shelf, so the void credits the one
        # that is still out — not all four.
        assert StockOnHand.objects.get(tenant=tenant_a, lot=lot).quantity == before + 1
