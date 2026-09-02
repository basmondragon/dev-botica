import { describe, expect, it } from "vitest";
import {
  count,
  dayMonth,
  decimal,
  money,
  monthYear,
  percent,
  points,
  range,
  since,
  stamp,
  time,
  withUnit,
} from "./format";

/**
 * §A.11 · one locale, one formatter. Every figure in the product goes through
 * this module, so these are the values the whole interface is measured against.
 */
describe("the Colombian formatter", () => {
  it("groups thousands with a dot", () => {
    expect(count(4284)).toBe("4.284");
    expect(count(112480900)).toBe("112.480.900");
  });

  it("separates decimals with a comma", () => {
    expect(decimal(24.8)).toBe("24,8");
    expect(percent(58.6)).toBe("58,6%");
  });

  it("prefixes the peso sign, unspaced and with no decimals", () => {
    expect(money(15600)).toBe("$15.600");
    expect(money(9412600)).toBe("$9.412.600");
  });

  it("abbreviates above a million only where a display figure asks", () => {
    expect(money(9400000, { abbreviate: true })).toBe("$9,4 M");
    // A table cell is where the exact figure is read, so it never abbreviates.
    expect(money(9400000)).toBe("$9.400.000");
  });

  it("uses a non-breaking space before M and before a unit", () => {
    expect(money(412800000, { abbreviate: true })).toContain(" M");
    expect(withUnit(27.5, "g")).toBe("27,5 g");
  });

  it("uses U+2212 for a negative, never a hyphen", () => {
    expect(money(-2100000, { abbreviate: true })).toBe("−$2,1 M");
    expect(money(-2100000).startsWith("−")).toBe(true);
    expect(money(-2100000).includes("-")).toBe(false);
  });

  it("writes percentage points with a space and a sign", () => {
    expect(points(1.9)).toBe("+1,9 pp");
    expect(points(-1.9)).toBe("−1,9 pp");
  });

  it("writes a range with an unspaced ASCII hyphen", () => {
    expect(range(1, 15, 4284)).toBe("1-15 de 4.284");
  });

  it("writes a lot expiry as MM/AAAA", () => {
    expect(monthYear(new Date(2027, 2, 15))).toBe("03/2027");
    expect(monthYear(new Date(2026, 10, 1))).toBe("11/2026");
  });

  it("writes a time of day in 24 hours", () => {
    expect(time(new Date(2026, 0, 1, 9, 14))).toBe("09:14");
    expect(dayMonth(new Date(2026, 8, 12))).toBe("12/09");
  });

  it("climbs the relative-time ladder and then prints the stamp", () => {
    const now = new Date(2026, 7, 31, 18, 0);
    expect(since(new Date(now.getTime() - 4_000), now)).toBe("hace 4 s");
    expect(since(new Date(now.getTime() - 3 * 60_000), now)).toBe("hace 3 min");
    expect(since(new Date(now.getTime() - 2 * 3_600_000), now)).toBe(
      "hace 2 h",
    );
    const old = new Date(2026, 7, 31, 6, 0);
    expect(since(old, now)).toBe(stamp(old));
    expect(stamp(old)).toBe("al 31/08 06:00");
  });
});
