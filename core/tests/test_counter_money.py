"""The ticket's arithmetic, which is the single most likely error in the stage.

**Money is stored tax-inclusive**, so every check here is about the same two
sentences: `total = subtotal − discount`, and `tax` is *contained* in `total`
rather than added to it. A build that adds tax to the total produces a ticket
19% too expensive on cosmetics and exactly right on medicine, which is the kind
of defect a pilot finds three weeks in.
"""

from decimal import Decimal

import pytest

from core.counter import money

pytestmark = pytest.mark.django_db


class Line:
    """The four fields the arithmetic reads, which is what both `SaleLine` and
    `SaleReturnLine` carry."""

    def __init__(self, quantity, unit_price, discount="0.00", vat_class="excluded"):
        self.quantity = quantity
        self.unit_price = Decimal(unit_price)
        self.discount = Decimal(discount)
        self.vat_class = vat_class


def test_tax_is_contained_in_the_price_and_never_added_to_it():
    """Acceptance 14 · a `rate_19` line of $11.900 carries $1.900 of IVA
    **inside** it, not beside it."""
    assert money.contained_tax(Decimal("11900"), "rate_19") == Decimal("1900.00")
    assert money.contained_tax(Decimal("10500"), "rate_5") == Decimal("500.00")


def test_excluded_and_exempt_both_contain_nothing():
    """Two values and not one: the difference matters to an accountant and to
    S5's document rather than to this sum (§3)."""
    assert money.contained_tax(Decimal("15600"), "excluded") == Decimal("0.00")
    assert money.contained_tax(Decimal("15600"), "exempt") == Decimal("0.00")


def test_an_unknown_class_is_refused_rather_than_charged_at_zero():
    """A class this module does not recognise is a catalog the stage has drifted
    from, and charging no tax on it would be invisible until an accountant found
    it."""
    with pytest.raises(ValueError):
        money.contained_tax(Decimal("1000"), "rate_16")


def test_a_mixed_ticket_composes_the_way_the_handoff_draws_it():
    """Acceptance 14 · one `excluded` line and one `rate_19` line.

    `total = subtotal − discount`, and `tax` is **less than** `total` because it
    is inside it. Wrong reads as a ticket 19% over the shelf price on the
    non-medicine line.
    """
    figures = money.totals(
        [
            Line(4, "3900", vat_class="excluded"),
            Line(1, "11900", vat_class="rate_19"),
        ]
    )
    assert figures.subtotal == Decimal("27500.00")
    assert figures.discount == Decimal("0.00")
    assert figures.total == Decimal("27500.00")
    assert figures.tax == Decimal("1900.00")
    assert figures.tax < figures.total


def test_the_drawn_ticket_reads_back():
    """The handoff's own totals: `Subtotal $15.600 · Descuento $0 · Total
    $15.600`, with no tax line and no tax added to the total."""
    figures = money.totals([Line(4, "3900")])
    assert figures.subtotal == Decimal("15600.00")
    assert figures.total == Decimal("15600.00")
    assert figures.tax == Decimal("0.00")


def test_a_discount_comes_off_the_total_and_off_the_tax_with_it():
    """The IVA contained in a line is contained in what was actually charged, so
    a discount reduces both. Taxing the pre-discount amount would charge IVA on
    money nobody paid."""
    figures = money.totals([Line(1, "11900", discount="1900", vat_class="rate_19")])
    assert figures.subtotal == Decimal("11900.00")
    assert figures.discount == Decimal("1900.00")
    assert figures.total == Decimal("10000.00")
    assert figures.tax == Decimal("1596.64")


def test_a_pack_and_its_units_are_the_same_money():
    """Acceptance 11 · quantity is in base units, always. A `splittable` item
    sold as one pack of twenty and as twenty singles produces identical
    figures, because nothing about a line distinguishes them -- and nothing
    should."""
    pack = money.totals([Line(20, "650")])
    singles = money.totals([Line(1, "650") for _ in range(20)])
    assert pack.subtotal == singles.subtotal == Decimal("13000.00")
    assert pack.total == singles.total
    assert pack.tax == singles.tax


def test_rounding_is_half_up_and_never_a_float():
    """A half-even here and a half-up on the till is a one-peso disagreement on
    every other ticket, on the one figure a customer is about to pay."""
    assert money.cents("0.125") == Decimal("0.13")
    assert money.cents("0.135") == Decimal("0.14")
    assert money.cents(None) == Decimal("0.00")
