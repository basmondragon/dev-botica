"""The ticket's arithmetic, in one module, because it is the single most likely
error in the stage.

**Money is stored tax-inclusive.** A Colombian shelf price includes IVA, and the
handoff's own ticket confirms it: `Subtotal $15.600 · Descuento $0 · Total
$15.600`, with no tax line and no tax added to the total. So:

  * `sale_lines.unit_price` is what the customer pays per base unit, IVA
    included;
  * a line's **gross** is `unit_price × quantity` and its **net** is
    `gross − discount`;
  * `tax_amount` is the IVA **contained** in the net, derived from the line's
    own `vat_class` as `net × rate / (100 + rate)`;
  * `sales.subtotal` is the sum of line gross amounts **before** discount,
    `sales.discount` the sum of the line discounts, `sales.total = subtotal −
    discount`, and `sales.tax` the sum of `tax_amount` -- **inside** the total,
    never added to it.

A build that added `tax` to `total` produces a ticket 19% too expensive on
cosmetics and exactly right on medicine, which is the kind of defect a pilot
finds three weeks in. `sales` carries two CHECK constraints saying the same
thing in the database, so the arithmetic is checked on every row this module did
not compute.

**Every figure is a `Decimal` quantized to two places, half-up.** Never a float:
`12345.67` through IEEE 754 and back is not `12345.67`, and this is the one
figure in the product a customer is about to pay.
"""

from decimal import ROUND_HALF_UP, Decimal

from core.models import VAT_RATES

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")

HUNDRED = Decimal("100")


def cents(value) -> Decimal:
    """One money figure, quantized. `None` is zero, because every caller here is
    summing and a null in a sum is a defect two screens away."""
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


def contained_tax(net, vat_class: str) -> Decimal:
    """The IVA already inside `net`, for one line.

    `excluded` is not a taxable operation and `exempt` is taxable at 0%; both
    contain nothing, and the difference between them matters to an accountant
    and to S5's document rather than to this sum (§3). An unknown class is
    **not** silently zero -- a class this module does not recognise is a catalog
    the stage has drifted from, and charging no tax on it would be invisible
    until an accountant found it.
    """
    rate = VAT_RATES.get(vat_class)
    if rate is None:
        raise ValueError(
            f"«{vat_class}» no es una clase de IVA reconocida, así que no se "
            "puede saber cuánto impuesto lleva esta línea."
        )
    amount = cents(net)
    if rate == 0 or amount == ZERO:
        return ZERO
    return (amount * rate / (HUNDRED + rate)).quantize(CENTS, rounding=ROUND_HALF_UP)


def line_gross(unit_price, quantity) -> Decimal:
    return cents(cents(unit_price) * Decimal(int(quantity)))


def line_net(unit_price, quantity, discount) -> Decimal:
    return line_gross(unit_price, quantity) - cents(discount)


def line_tax(unit_price, quantity, discount, vat_class) -> Decimal:
    return contained_tax(line_net(unit_price, quantity, discount), vat_class)


class Totals:
    """What a ticket's four figures are, computed from its lines and nothing
    else -- not from what a till sent, which is a browser's arithmetic."""

    __slots__ = ("subtotal", "discount", "tax", "total")

    def __init__(self, subtotal, discount, tax, total):
        self.subtotal = subtotal
        self.discount = discount
        self.tax = tax
        self.total = total

    def __eq__(self, other):
        return isinstance(other, Totals) and (
            self.subtotal,
            self.discount,
            self.tax,
            self.total,
        ) == (other.subtotal, other.discount, other.tax, other.total)

    def __repr__(self):
        return (
            f"Totals(subtotal={self.subtotal}, discount={self.discount}, "
            f"tax={self.tax}, total={self.total})"
        )

    def as_fields(self) -> dict:
        return {
            "subtotal": self.subtotal,
            "discount": self.discount,
            "tax": self.tax,
            "total": self.total,
        }


def totals(lines) -> Totals:
    """Recompute a ticket's four figures from its own lines.

    **The server recomputes rather than trusts.** A till's totals are a
    browser's arithmetic, and the one figure the whole product is measured on is
    not a place to take a client's word -- while the *prices* the till stamped
    are taken exactly as sent, because `sale_lines.unit_price` records what was
    actually charged and no later price list may restate it (§5).

    `lines` is any iterable of objects carrying `unit_price`, `quantity`,
    `discount` and `vat_class`, which is both `SaleLine` and
    `SaleReturnLine` -- a return's money composes exactly as a sale's, because a
    credit note reverses what was charged.
    """
    subtotal = ZERO
    discount = ZERO
    tax = ZERO
    for line in lines:
        subtotal += line_gross(line.unit_price, line.quantity)
        discount += cents(line.discount)
        tax += line_tax(line.unit_price, line.quantity, line.discount, line.vat_class)
    return Totals(subtotal, discount, tax, subtotal - discount)
