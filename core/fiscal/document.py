"""The canonical sale document. **This payload is the deliverable.**

Everything else in the stage is plumbing around it, and its completeness is the
difference between a working handoff and a cashier keying every sale twice: a
field the receiving system needs and we did not send is a field somebody
re-types, and a handoff that gets re-typed is worse than no handoff (§8).

**It is a pure function of the sale and the mapping version in force**, built
when it is about to be sent and never stored as input. Four things follow, and
they are why the whole product ships with this integration switched off: an
instance can run unconfigured for months and lose nothing; a correction upstream
is picked up by the next retry; a mapping fix re-renders every stuck document;
and nothing is ever backfilled, because nothing was ever due.

**Two of its groups are not a design choice.** Resolución DIAN 000165 de 2023
and its Anexo Técnico DEE 1.0 require that the acquirer be identifiable by name
and identification number, and that tax be stated per line rather than per
ticket -- which is also why `items.vat_class` exists per item. That is the whole
of the regulation's presence here: it explains why the payload has the fields it
has. The obligation itself is the pharmacy's and is met by the system it already
runs (§8, A9).

**Every amount is an integer number of COP; the unit price is not an amount.**
That distinction is the whole of the money model here, and it is forced by the
product rather than chosen.

**An amount is money that moved**, so it is stated in whole pesos: a line's
total, a discount, a tax, the ticket's four figures, a payment. Colombia's
smallest coin is fifty pesos and every invoicing system in the country takes
whole ones.

**A unit price is a rate, and it is fractional by construction.** `items` are
priced per pack and sold per base unit, so a splittable box of fourteen at
`$15.450` sells at `$1.103,57` a tablet -- and `sale_lines.unit_price` records
exactly that, because it is what the line was actually charged at (§5). Rounding
it would make `unit_price × quantity` disagree with the line's own total by six
pesos, which is precisely the arithmetic a receiving system checks. So it
travels **as a decimal string**: exact, and unambiguous in JSON, where a float
is neither.

**The rounding residue is apportioned, never dropped.** Tax comes from a
division and a per-line discount is a share of a ticket's, so both round; the
residue then goes to the lines whose own fraction was largest -- the
largest-remainder method every invoicing system applies to the same problem --
so the lines sum to the ticket exactly. Rounding each line independently would
leave a document whose lines add up to a peso more than its total, which is the
most common way an integration is rejected at the far end.

**Nothing here corrects the sale.** `sale_lines` is untouched; what changes is
only how the same money is stated. And `build` compares the lines' own decimal
sums against the ticket's stored figures **before** any rounding, so a line
edited after close is caught rather than smoothed away.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from core.counter import money
from core.models import VAT_RATES, Sale, VatClass

#: A service renders exactly like a product -- code, description, quantity,
#: price, `vat_class`, tax (A7). That is the whole cost of supporting toma de
#: presión and inyectología on a fiscal document, and it is why they are the
#: same table.
CURRENCY = "COP"

#: The quantum a fiscal document is stated in.
ONE = Decimal("1")
#: Where `money.cents` half-up rounding turns down into up.
HALF = Decimal("0.5")

SALE = "sale"
CREDIT_NOTE = "credit_note"


class Incomplete(Exception):
    """The payload is not one a receiving system could act on.

    **Never a refused sale and never an error at a counter** (§5 rule 2). The
    sale is closed and the customer has left; this lands the document `failed`
    with a sentence an administrator can act on, on the work list, and nowhere
    else.
    """


def price(value) -> str:
    """One unit price, exactly as it was charged, as a decimal string.

    A string rather than a JSON number: `1103.57` through IEEE 754 and back is
    not `1103.57`, and this is the figure a receiving system multiplies by a
    quantity to check a line. Two places always, so a mapping can parse it with
    a fixed scale.
    """
    return f"{money.cents(value):.2f}"


def rounded(value) -> int:
    """One derived figure as whole pesos, **rounded**.

    The one place in this module that rounds, and it rounds because tax and a
    prorated discount are fractional by construction. Half-up, the same way
    `core.counter.money` rounds at the counter, so the two never disagree about
    which way a half goes.
    """
    return int(money.cents(value).quantize(ONE, rounding=ROUND_HALF_UP))


def rate_for(vat_class: str) -> int:
    rate = VAT_RATES.get(vat_class)
    if rate is None:
        raise Incomplete(
            f"«{vat_class}» no es una clase de IVA reconocida, así que no se "
            "puede declarar el impuesto de esta línea."
        )
    return int(rate)


@dataclass(frozen=True)
class Source:
    """What a document is built from: a sale, or a reversal of part of one.

    One shape for all three cases -- a sale, a return's credit note and a void's
    credit note -- so the builder has no branches beyond the lines it is handed
    and the `type` it is told. A void is a credit note, always (§8), and the
    alternative -- cancelling a queued document so the target never hears of
    that sale -- was rejected: it produces a state where our record says a sale
    exists and the target has never seen the `sales.number` both systems
    reconcile on.
    """

    #: Always the **sale**, on all three: a credit note describes a reversal of
    #: one and takes its emitter, its sede, its acquirer and the `sale_number`
    #: both systems reconcile on from it.
    sale: Sale
    type: str
    document_key: str
    #: The till's clock, and whose it is depends on what this document is: a
    #: sale's own, a return's own, a void's moment. Never adjusted (§5 rule 4).
    occurred_at: object
    #: The server's clock for **this** document -- the return's, not the sale's,
    #: on a credit note. Every count in this stage reads `recorded_at`, so a
    #: credit note carrying the sale's would date a September reversal in July.
    recorded_at: object
    #: `(sale_line, quantity, unit_price, discount, vat_class, tax_amount)` in
    #: ticket order. For a return these are `sale_return_lines` at **the prices
    #: originally charged**, read from the row rather than re-derived from a
    #: price list -- the price may have changed since, and the document must
    #: describe the money that actually moved (§5).
    lines: tuple
    #: For a credit note: the original's `sale_number` and `document_key`.
    references: dict | None = None
    #: The payments the money actually moved through. A credit note reverses the
    #: sale's own split unless the return declared a refund method.
    payments: tuple = ()


def build(source: Source, *, tenant, location, customer, items, barcodes) -> dict:
    """One canonical document, complete or not at all.

    Every lookup is passed in rather than queried here, because this runs inside
    the transaction that lands the sale and a builder that issued its own
    queries per line would put a join per ticket line on the push's critical
    path (§4).
    """
    if source.type != CREDIT_NOTE:
        # **Checked on the sale's own figures, before any rounding**, so a line
        # edited after the ticket closed is caught rather than smoothed into the
        # apportionment below.
        _agrees(_gross(source), source.sale.subtotal, "El importe")
        _agrees(_summed(source, "discount"), source.sale.discount, "El descuento")
        _agrees(_summed(source, "tax_amount"), source.sale.tax, "El IVA")
    lines = [
        _line(line, items=items, barcodes=barcodes, position=index + 1)
        for index, line in enumerate(source.lines)
    ]
    totals = _totals(lines, source)
    payments = _payments(source, totals["total"])
    document = {
        "document": {
            "document_key": source.document_key,
            "type": source.type,
            # **The key both systems share** (§8). Without it, reconciling a day
            # of sales against a day of invoices is manual and gets done at
            # month end by someone who did not make the sale.
            "sale_number": source.sale.number,
            "references": source.references,
            # **Both clocks travel, because they answer different questions**
            # (§5 rule 4). `occurred_at` is what the customer's receipt says;
            # `recorded_at` is what every count in this stage uses.
            "occurred_at": _stamp(source.occurred_at),
            "recorded_at": _stamp(source.recorded_at),
            "currency": CURRENCY,
            "device_code": getattr(source.sale.device, "code", "") or "",
            "sold_by": source.sale.sold_by_name or "",
        },
        # The receiving system issues under the pharmacy's own resolution, and
        # that resolution is per sede (§2). A target that has to guess which
        # sede a sale came from issues it against the wrong range.
        "emitter": {
            "nit": (tenant.nit or "").strip(),
            "name": tenant.name,
            "location": {
                "code": location.code,
                "name": location.name,
                "address": location.address or "",
                "city": location.city or "",
            },
        },
        "acquirer": _acquirer(customer),
        "lines": lines,
        "totals": totals,
        "payments": payments,
    }
    validate(document)
    return document


def _payments(source, total: int) -> list:
    """What the money moved through, in whole pesos summing to the total.

    **Checked before it is smoothed.** A payment is what was applied to the
    sale, so the split has to add up to the ticket -- and if it does not, that
    is a sale whose recorded payments genuinely disagree with what it charged,
    which is a `failed` row naming the arithmetic and never a document sent with
    a corrected figure. S4 rejects a malformed `payments` row on its own
    savepoint while the sale and its lines still apply (S2's batch rule), so a
    ticket really can land holding a strict subset of its split -- and a builder
    that absorbed the difference would file that sale as fully paid by whichever
    method survived.

    What is smoothed afterwards is only the rounding residue, which is at most a
    peso per payment and comes from stating each amount in whole pesos.
    """
    applied = [
        {
            "method": payment.method,
            "amount": rounded(payment.amount),
            "reference": payment.reference or "",
        }
        for payment in source.payments
    ]
    if not applied:
        return applied

    charged = sum((money.cents(one.amount) for one in source.payments), money.ZERO)
    if rounded(charged) != total:
        raise Incomplete(
            f"Los pagos suman {charged} y la venta dice {money.cents(total)}. "
            "El documento no se envía con esa diferencia."
        )
    residue = total - sum(one["amount"] for one in applied)
    if residue:
        largest = max(range(len(applied)), key=lambda index: applied[index]["amount"])
        applied[largest]["amount"] += residue
    return applied


def _agrees(charged, stated, label) -> None:
    if charged != money.cents(stated):
        raise Incomplete(
            f"{label} de las líneas suma {charged} y la venta dice "
            f"{money.cents(stated)}. El documento no se envía con esa "
            "diferencia."
        )


def _stamp(value) -> str:
    return value.isoformat() if value is not None else ""


def _acquirer(customer) -> dict:
    """`is_final_consumer` with the other three **absent from the object**, not
    present and null, for the majority case.

    A target's mapping supplies whatever generic identifier it expects for an
    unidentified acquirer; the exact form belongs to the target and not to this
    payload. A null where a field should be absent is a null a mapping has to
    special-case, and the first one that forgets sends `"name": null` to a
    system that stores it as the string `null`.
    """
    if customer is None:
        return {"is_final_consumer": True}
    # **A sale that names a customer is not a final-consumer sale.** Falling
    # back to `is_final_consumer` for a customer whose document number is blank
    # would send a valid-looking document that names the wrong acquirer, and
    # nothing downstream could tell. The hole is reported instead, on the work
    # list, where somebody can fix the customer.
    return {
        "is_final_consumer": False,
        "document_type": customer.document_type,
        "document": customer.document,
        "name": customer.name,
    }


def _line(line, *, items, barcodes, position) -> dict:
    """One line. **Tax is per line** because most medicines are excluded from
    IVA and a large share of what a droguería actually sells is not (§3)."""
    item = items.get(line.item_id)
    if item is None:
        raise Incomplete(
            f"La línea {position} nombra un producto que ya no está en el "
            "catálogo, así que no se puede describir en el documento."
        )
    quantity = int(line.quantity)
    # The three amounts are provisional here: `_totals` apportions the rounding
    # residue across the lines and restates `line_total` from what it leaves
    # behind.
    return {
        "position": position,
        # **The item's primary barcode** -- what the pharmacy and its accountant
        # already recognise -- with `item_id` beside it, because a uuid is what
        # makes a mapping reproducible when a barcode is re-used or missing.
        "item_code": barcodes.get(line.item_id, "") or "",
        "item_id": str(line.item_id),
        "description": _description(item),
        "quantity": quantity,
        "unit": item.unit or "",
        "unit_price": price(line.unit_price),
        "discount": rounded(line.discount),
        "vat_class": line.vat_class,
        "tax_rate": rate_for(line.vat_class),
        "tax_amount": rounded(line.tax_amount),
        "line_total": rounded(money.line_net(line.unit_price, quantity, line.discount)),
    }


def _description(item) -> str:
    """What a person reading the invoice recognises: the name, its presentation
    and its strength, in the order the ticket already prints them."""
    parts = [item.name, item.strength or "", item.presentation or ""]
    return " ".join(part.strip() for part in parts if part and part.strip())


def _totals(lines, source) -> dict:
    """**Copied from the sale, never recomputed** -- and then made to agree with
    the lines to the peso.

    `tax_by_class` exists because targets ask for the base and the tax per rate,
    and deriving it at the far end is how two systems come to disagree about a
    peso.
    """
    if source.type == CREDIT_NOTE:
        # A credit note's totals are its own lines': it reverses part of a sale,
        # and the sale's header describes the whole of it. **Amounts are
        # positive; the sign is carried by the type**, so that no target's
        # mapping has to guess whether a negative total means a credit or a data
        # error.
        subtotal = rounded(_gross(source))
        discount = rounded(_summed(source, "discount"))
        tax = rounded(_summed(source, "tax_amount"))
    else:
        subtotal = rounded(source.sale.subtotal)
        discount = rounded(source.sale.discount)
        tax = rounded(source.sale.tax)

    # `subtotal` and `discount` are each rounded once from the ticket's own
    # figure, so stating the total as their difference keeps the three one
    # arithmetic rather than three independent roundings.
    total = subtotal - discount
    _apportion(lines, source, "discount", discount)
    _apportion(lines, source, "tax_amount", tax)
    _apportion(lines, source, "line_total", total, fraction=_net_fraction)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "tax": tax,
        "tax_by_class": _by_class(lines),
        "total": total,
    }


def _gross(source) -> Decimal:
    """The lines' own money before any rounding: price times quantity."""
    return sum(
        (money.line_gross(line.unit_price, line.quantity) for line in source.lines),
        money.ZERO,
    )


def _summed(source, attribute) -> Decimal:
    """One of the lines' own decimal figures, before any rounding. What the
    ticket actually charged, and what the ticket's stored total has to equal."""
    return sum(
        (money.cents(getattr(line, attribute)) for line in source.lines), money.ZERO
    )


def _net_fraction(line) -> Decimal:
    """A line's own fractional part of its net amount, for the apportionment."""
    return _fraction(money.line_net(line.unit_price, line.quantity, line.discount))


def _apportion(lines, source, field: str, target: int, *, fraction=None) -> None:
    """Move the rounding residue onto the lines that earned it.

    **The lines must sum to the ticket's own figure exactly**, or the document
    arrives carrying an arithmetic a target will either reject or, worse, accept
    -- filing a figure the pharmacy's own books do not have.

    Largest remainder, in both directions, and **only ever against the way a
    line was already rounded**: a peso short goes to the line rounded *down* by
    the smallest margin, and a peso over comes off the line rounded *up* by the
    smallest margin. Sorting every line together instead would take a peso off a
    line whose amount was exact -- leaving the ticket right and that line wrong,
    which is the harder error to find because the totals still add up.
    """
    if not lines:
        return
    residue = target - sum(line[field] for line in lines)
    if residue == 0:
        return
    weigh = fraction or (lambda line: _fraction(getattr(line, field)))
    fractions = [weigh(one) for one in source.lines]
    up = residue > 0
    # `money.cents` rounds half-up, so a line with a fraction at or above a half
    # was rounded up and one below it was rounded down.
    movable = [index for index in range(len(lines)) if (fractions[index] < HALF) is up]
    order = sorted(movable, key=lambda index: fractions[index], reverse=up)
    if len(order) < abs(residue):
        # Unreachable while the lines agree with the ticket, which `_agrees`
        # refuses a document without -- but the total has to come out exact
        # either way, so the remaining lines take the rest rather than the
        # document going out carrying an arithmetic nobody can reproduce.
        order = order + [index for index in range(len(lines)) if index not in movable]
    step = 1 if up else -1
    for index in order[: abs(residue)]:
        lines[index][field] += step


def _fraction(value) -> Decimal:
    amount = money.cents(value)
    return amount - amount.to_integral_value(rounding=ROUND_FLOOR)


def _by_class(lines) -> list:
    """The taxable base and the tax per rate, in the enum's own order."""
    bases: dict[str, dict] = {}
    for line in lines:
        row = bases.setdefault(
            line["vat_class"],
            {
                "vat_class": line["vat_class"],
                "tax_rate": line["tax_rate"],
                "taxable_base": 0,
                "tax_amount": 0,
            },
        )
        # The base is the line net **less the tax it already contains**: a
        # Colombian shelf price is tax-inclusive (S4, `core.counter.money`), and
        # a target asking for a base wants the figure the rate applies to.
        row["taxable_base"] += line["line_total"] - line["tax_amount"]
        row["tax_amount"] += line["tax_amount"]
    return [bases[value] for value in VatClass.values if value in bases]


def validate(document: dict) -> None:
    """**Validation is on the canonical payload, once, for every target** --
    not inside a mapping, where each new client would rediscover the same
    missing NIT.

    A failure here is a `failed` row on the work list with a sentence in it, and
    it never reaches a counter (§5 rule 2).
    """
    header = document["document"]
    if not header.get("sale_number"):
        raise Incomplete("La venta no tiene número interno, que es la clave del envío.")
    if not document["emitter"]["nit"]:
        raise Incomplete(
            "La droguería no tiene NIT registrado. Se configura en Ajustes · General."
        )

    acquirer = document["acquirer"]
    if not acquirer.get("is_final_consumer"):
        if not acquirer.get("document"):
            raise Incomplete("El adquiriente no tiene número de documento.")
        if not acquirer.get("document_type"):
            raise Incomplete("El adquiriente no tiene tipo de documento.")
        if not (acquirer.get("name") or "").strip():
            raise Incomplete("El adquiriente no tiene nombre.")

    lines = document["lines"]
    if not lines:
        raise Incomplete("El documento no tiene líneas.")
    for line in lines:
        position = line["position"]
        if not line["item_code"] and not line["item_id"]:
            raise Incomplete(f"La línea {position} no tiene código de producto.")
        if not (line["description"] or "").strip():
            raise Incomplete(f"La línea {position} no tiene descripción.")
        if line["quantity"] <= 0:
            raise Incomplete(f"La línea {position} no tiene cantidad.")
        # The unit price is a decimal string, so it is read back rather than
        # compared as a number -- and an unreadable one is a defect worth
        # naming, because it is the figure a receiving system multiplies.
        try:
            charged = Decimal(line["unit_price"])
        except (ArithmeticError, TypeError, ValueError) as failure:
            raise Incomplete(
                f"La línea {position} no tiene un precio unitario legible."
            ) from failure
        if charged < 0:
            raise Incomplete(f"La línea {position} tiene un precio negativo.")
        if not line["vat_class"]:
            raise Incomplete(f"La línea {position} no tiene clase de IVA.")

    reconcile(document)

    if not document["payments"]:
        raise Incomplete("El documento no registra ningún medio de pago.")


def reconcile(document: dict) -> None:
    """**Lines must reconcile to the totals, and the payments to the total.**

    Sending a document whose lines contradict its total means the target either
    refuses it or, worse, accepts it and files a figure the pharmacy's own books
    do not have. So a payload that fails this is **never sent**: it is written
    `failed` with the arithmetic named.
    """
    lines = document["lines"]
    totals = document["totals"]
    net = sum(line["line_total"] for line in lines)
    discount = sum(line["discount"] for line in lines)
    tax = sum(line["tax_amount"] for line in lines)

    # **The lines' own money is their net**, because that is the amount that
    # moved. Their gross is not restated as an integer: `unit_price × quantity`
    # is exact only in decimals, and a whole-peso `subtotal` compared against a
    # sum of rounded grosses would fail on every splittable item in the catalog.
    if net != totals["total"]:
        raise Incomplete(
            f"Las líneas suman {net} y el total dice {totals['total']}. "
            "El documento no se envía con esa diferencia."
        )
    if discount != totals["discount"]:
        raise Incomplete(
            f"Los descuentos de las líneas suman {discount} y el documento "
            f"dice {totals['discount']}."
        )
    if tax != totals["tax"]:
        raise Incomplete(
            f"El IVA de las líneas suma {tax} y el documento dice {totals['tax']}."
        )
    if totals["subtotal"] - totals["discount"] != totals["total"]:
        raise Incomplete(
            f"El total {totals['total']} no es el subtotal "
            f"{totals['subtotal']} menos el descuento {totals['discount']}."
        )
    paid = sum(payment["amount"] for payment in document["payments"])
    if document["payments"] and paid != totals["total"]:
        raise Incomplete(f"Los pagos suman {paid} y el total es {totals['total']}.")


def line_tax(unit_price, quantity, discount, vat_class) -> Decimal:
    """A credit note's per-line tax, computed the way the ticket computed it.

    Re-exported from S4's module rather than restated, so a rate change moves
    one constant and not two.
    """
    return money.line_tax(unit_price, quantity, discount, vat_class)
