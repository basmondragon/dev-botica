import { describe, expect, it } from "vitest";
import {
  containedTax,
  fromCents,
  lineTax,
  pesos,
  toCents,
  totals,
} from "./money";

/**
 * The till's arithmetic, held to the same four rules the server's is.
 *
 * The two implementations exist because the counter has no server on its path
 * and the server does not take a browser's word for the money — so they have to
 * agree, and these are the cases they agree on. Every figure the server checks
 * in `core/tests/test_counter_money.py` is checked here against the same
 * expected value, deliberately: a divergence is a cashier reading one total and
 * an accountant reading another.
 */

function line(
  quantity: number,
  unit_price: string,
  discount = "0.00",
  vat_class = "excluded",
) {
  return { quantity, unit_price, discount, vat_class };
}

describe("centavos on the wire and in the store", () => {
  it("parses and prints without ever touching a float", () => {
    expect(toCents("15600.00")).toBe(1560000);
    expect(toCents("15600")).toBe(1560000);
    expect(toCents("0.1")).toBe(10);
    expect(toCents(null)).toBe(0);
    expect(fromCents(1560000)).toBe("15600.00");
    expect(fromCents(-300000)).toBe("-3000.00");
    expect(pesos(1560000)).toBe(15600);
  });

  it("survives the sum that a float would not", () => {
    // 0.1 + 0.2 in IEEE 754 is not 0.3, and this is the one figure in the
    // product a customer is about to pay.
    expect(fromCents(toCents("0.1") + toCents("0.2"))).toBe("0.30");
  });
});

describe("tax is contained in the price and never added to it", () => {
  it("carries the IVA a Colombian shelf price already includes", () => {
    expect(containedTax(1190000, "rate_19")).toBe(190000);
    expect(containedTax(1050000, "rate_5")).toBe(50000);
  });

  it("finds nothing in an excluded or exempt line", () => {
    expect(containedTax(1560000, "excluded")).toBe(0);
    expect(containedTax(1560000, "exempt")).toBe(0);
  });

  it("finds nothing it can name in an unrecognised class", () => {
    // Not silently zero on the server, where it raises; here the line renders
    // no tax and the server's own recomputation is what refuses the row.
    expect(containedTax(1000000, "rate_16")).toBe(0);
  });
});

describe("the ticket's four figures", () => {
  it("reads back the totals the handoff draws", () => {
    const figures = totals([line(4, "3900")]);
    expect(fromCents(figures.subtotal)).toBe("15600.00");
    expect(fromCents(figures.total)).toBe("15600.00");
    expect(fromCents(figures.tax)).toBe("0.00");
    expect(figures.units).toBe(4);
  });

  it("keeps tax inside the total on a mixed ticket", () => {
    const figures = totals([
      line(4, "3900", "0.00", "excluded"),
      line(1, "11900", "0.00", "rate_19"),
    ]);
    expect(fromCents(figures.subtotal)).toBe("27500.00");
    expect(fromCents(figures.total)).toBe("27500.00");
    expect(fromCents(figures.tax)).toBe("1900.00");
    expect(figures.tax).toBeLessThan(figures.total);
  });

  it("takes a discount off the total and off the tax with it", () => {
    const figures = totals([line(1, "11900", "1900", "rate_19")]);
    expect(fromCents(figures.total)).toBe("10000.00");
    expect(fromCents(figures.tax)).toBe("1596.64");
  });

  it("prices a pack and its units identically", () => {
    // Quantity is in base units, always: nothing about a line distinguishes a
    // box of twenty from twenty singles, because nothing should.
    const pack = totals([line(20, "650")]);
    const singles = totals(Array.from({ length: 20 }, () => line(1, "650")));
    expect(pack.subtotal).toBe(singles.subtotal);
    expect(pack.total).toBe(singles.total);
    expect(pack.tax).toBe(singles.tax);
    expect(fromCents(pack.total)).toBe("13000.00");
  });

  it("computes one line's tax the way the ticket does", () => {
    expect(fromCents(lineTax(line(2, "11900", "0.00", "rate_19")))).toBe(
      "3800.00",
    );
  });
});
