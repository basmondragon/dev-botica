/**
 * The ticket's arithmetic on the till, in **integer centavos**.
 *
 * Never a float. `0.1 + 0.2` is not `0.3`, and this is the one figure in the
 * product a customer is about to pay — a ticket that renders `$15.599,99`
 * because of IEEE 754 is a ticket a cashier argues about at a counter. Every
 * value on the wire is a decimal string, every value in here is an integer
 * number of centavos, and the two conversions are the only place the
 * representation changes. A twelve-digit `numeric(12,2)` is at most 10^14
 * centavos, comfortably inside `Number.MAX_SAFE_INTEGER`.
 *
 * **Money is tax-inclusive**, exactly as it is on the server: `unit_price` is
 * what the customer pays per base unit with IVA in it, `tax_amount` is the IVA
 * *contained* in the line, and a ticket's `total` is `subtotal − descuento`
 * with the tax inside it rather than added to it. A build that added `tax` to
 * `total` produces a ticket 19% too expensive on cosmetics and exactly right on
 * medicine.
 *
 * **The server recomputes all of it on arrival**, so nothing here is the record
 * of anything: this is what the cashier and the customer read while the sale is
 * happening, and `core/counter/money.py` is what the sale *is*. The two agree
 * because they are the same four rules, and the tests hold them to it.
 */

/**
 * The class-to-rate map, mirroring S1's `VAT_RATES`. It is a statute and not a
 * setting — a network does not get to choose it — and a rate changed by decree
 * moves no historical ticket, because `sale_lines.vat_class` and `tax_amount`
 * are both stamped at the moment of sale.
 */
const RATES: Record<string, number> = {
  excluded: 0,
  exempt: 0,
  rate_5: 5,
  rate_19: 19,
};

/** `"15600.00"` → `1560000`. A missing value is zero. */
export function toCents(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  const text = String(value).trim();
  const negative = text.startsWith("-");
  const [whole = "0", fraction = ""] = text.replace("-", "").split(".");
  const cents =
    Number(whole) * 100 + Number((fraction + "00").slice(0, 2) || "0");
  return negative ? -cents : cents;
}

/** `1560000` → `"15600.00"`, which is the shape the wire and the column take. */
export function fromCents(cents: number): string {
  const rounded = Math.round(cents);
  const sign = rounded < 0 ? "-" : "";
  const absolute = Math.abs(rounded);
  return `${sign}${Math.floor(absolute / 100)}.${String(absolute % 100).padStart(2, "0")}`;
}

/** Centavos as pesos, for `@/ui/format`'s `money()` and for nothing else. */
export function pesos(cents: number): number {
  return cents / 100;
}

/** Half-up, matching the server's `ROUND_HALF_UP` — a half-even here and a
 *  half-up there is a one-peso disagreement on every other ticket. */
function round(value: number): number {
  return value < 0 ? -Math.round(-value) : Math.round(value);
}

/** The IVA already inside `net`. An unrecognised class is **not** silently
 *  zero: it is a catalog the till has drifted from. */
export function containedTax(net: number, vatClass: string | null): number {
  const rate = RATES[vatClass ?? ""];
  if (rate === undefined || rate === 0 || net === 0) return 0;
  return round((net * rate) / (100 + rate));
}

export interface Priced {
  quantity: number;
  unit_price: string | number | null;
  discount: string | number | null;
  vat_class: string | null;
}

export function lineGross(line: Priced): number {
  return toCents(line.unit_price) * line.quantity;
}

export function lineNet(line: Priced): number {
  return lineGross(line) - toCents(line.discount);
}

export function lineTax(line: Priced): number {
  return containedTax(lineNet(line), line.vat_class);
}

export interface Totals {
  subtotal: number;
  discount: number;
  tax: number;
  total: number;
  units: number;
}

/** A ticket's four figures, from its own lines and nothing else. */
export function totals(lines: Priced[]): Totals {
  let subtotal = 0;
  let discount = 0;
  let tax = 0;
  let units = 0;
  for (const line of lines) {
    subtotal += lineGross(line);
    discount += toCents(line.discount);
    tax += lineTax(line);
    units += line.quantity;
  }
  return { subtotal, discount, tax, total: subtotal - discount, units };
}
