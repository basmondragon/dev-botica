import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  BASIS_LABEL,
  CONFIDENCE_LABEL,
  ROW_STATE,
  ROW_STATE_ORDER,
  basisLine,
  tileFamily,
} from "./vocabulary";

/**
 * What this stage owes its screen, checked where a person would look.
 *
 * The rules under test are the ones a reviewer cannot see by reading a
 * component: that **nothing in this module can write a price**, that the two
 * derived row states are not conflated, that a resolved suggestion keeps both
 * numbers, and that the `Estado` badge families are the ones §B.7.4 fixed for
 * the values these three replaced.
 */

const SRC = join(process.cwd(), "src");

function* walk(directory: string): Generator<string> {
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (/\.(tsx?)$/.test(full)) yield full;
  }
}

function pricingSources() {
  return [...walk(join(SRC, "pricing"))]
    .filter((file) => !/\.test\.tsx?$/.test(file))
    .map((file) => ({ file, code: readFileSync(file, "utf8") }));
}

describe("A11 · this surface has no write path", () => {
  /**
   * **The structural half, read off the source.** The Precios screen calls one
   * mutation and its whole consequence is that a screen changes; the row action
   * is a navigation. A component here that reached `useSetPrice` would be the
   * amendment undone, and it would pass every visual review.
   */
  it("never imports a price writer", () => {
    for (const { file, code } of pricingSources()) {
      expect(code, file).not.toMatch(/useSetPrice|useWithdrawPrice/);
      expect(code, file).not.toMatch(/api\.(POST|PUT|PATCH)\(/);
    }
  });

  /**
   * The row action **changes route**. The suggestion travels in the URL to S1's
   * editor, and the write that follows is S1's -- which is what makes
   * "no request reaches any `/api/pricing/` path from the press onward" true by
   * construction rather than by discipline.
   */
  it("carries the suggestion to the catalog editor rather than saving it", () => {
    const page = readFileSync(join(SRC, "pricing/pricing-page.tsx"), "utf8");
    expect(page).toMatch(/to: "\/inventory\/catalog"/);
    expect(page).toMatch(/proposal: prefill \? proposal\.id : undefined/);
    expect(page).toMatch(
      /sugerido: prefill \? String\(proposal\.suggested_price\)/,
    );
  });

  /** §B.4.4, §B.4.5 · no checkbox column and no bulk bar: there is no bulk
   *  action left for them to carry. */
  it("renders no selection and no bulk bar", () => {
    for (const { file, code } of pricingSources()) {
      expect(code, file).not.toMatch(/BulkBar|selection=\{/);
    }
  });
});

describe("the Estado badge", () => {
  /**
   * §B.7.4 · the two values the design system already fixed keep their family
   * and their label; the three that arrived take the family the value they
   * replaced carried, rather than inventing a sixth reading.
   */
  it("keeps the families §B.7.4 fixed", () => {
    expect(ROW_STATE.proposed).toEqual({
      family: "neutral",
      dot: "hollow",
      label: "Propuesta",
    });
    expect(ROW_STATE.above_cap.family).toBe("critical");
    // `approved` was the good terminal state, and `taken` is what reached for it.
    expect(ROW_STATE.taken.family).toBe("positive");
    // **Positive, not warning**: a price changed and the suggestion informed it.
    expect(ROW_STATE.modified.family).toBe("positive");
    // §B.7.4's `rejected` family: declining a model's suggestion is an owner
    // using their judgement, and colouring it as a failure teaches them not to.
    expect(ROW_STATE.dismissed.family).toBe("neutral");
  });

  /**
   * **The distinction the whole stage is about.** *We could not look* and *we
   * looked and there is nothing to do* send a reader to two different places,
   * so they are two states with two labels.
   */
  it("does not conflate «Sin evaluar» with «Sin propuesta»", () => {
    expect(ROW_STATE.unevaluated.label).toBe("Sin evaluar");
    expect(ROW_STATE.no_proposal.label).toBe("Sin propuesta");
    expect(ROW_STATE.unevaluated.label).not.toBe(ROW_STATE.no_proposal.label);
  });

  it("offers every state to the filter", () => {
    for (const state of ROW_STATE_ORDER) expect(ROW_STATE[state]).toBeTruthy();
  });
});

describe("the basis line under the badge", () => {
  /**
   * *UI* · two words, because the column is ten units wide and a truncated
   * basis is worse than no basis. **The band is omitted on a margin row**: it
   * is `low` by construction, and repeating it in two hundred rows teaches an
   * owner to stop reading it.
   */
  it("names the engine, and the band only where it was measured", () => {
    expect(basisLine("elasticity", "high")).toBe("Elasticidad · alta");
    expect(basisLine("elasticity", "medium")).toBe("Elasticidad · media");
    expect(basisLine("margin_rule", "low")).toBe("Margen");
    expect(basisLine(null, null)).toBe("");
  });

  it("labels the two engines the way the Base chip does", () => {
    expect(BASIS_LABEL.margin_rule).toBe("Margen");
    expect(BASIS_LABEL.elasticity).toBe("Elasticidad");
    expect(CONFIDENCE_LABEL.high).toBe("Alta");
  });
});

describe("the compliance tile", () => {
  /** It is the one thing on this surface that reports a problem rather than
   *  proposing an improvement, so it takes the critical tint only when there is
   *  one — a marker that is always lit stops meaning anything (§B.9.2). */
  it("goes critical only when a reference is above its cap", () => {
    expect(tileFamily(0)).toBe("neutral");
    expect(tileFamily(2)).toBe("critical");
  });
});

describe("the sentences on this surface", () => {
  /**
   * Acceptance 23 · **every sentence is drawn from the fixed reason-code
   * vocabulary with figures interpolated**, composed server-side. No component
   * here writes prose of its own about a suggestion, and no endpoint in this
   * stage calls a model gateway.
   */
  it("renders the server's reason rather than composing one", () => {
    const page = readFileSync(join(SRC, "pricing/pricing-page.tsx"), "utf8");
    const panel = readFileSync(join(SRC, "pricing/record-panel.tsx"), "utf8");
    expect(panel).toMatch(/row\.reason/);
    expect(panel).toMatch(/estimate\.reason/);
    for (const code of page.matchAll(/reason_code/g)) expect(code).toBeTruthy();
  });

  /**
   * §A.11 · every figure goes through the formatter, and **`M` never appears in
   * a table cell** -- a cell is where the exact figure is read. The tiles are
   * the only place the abbreviation is permitted.
   */
  it("abbreviates only in a tile", () => {
    const page = readFileSync(join(SRC, "pricing/pricing-page.tsx"), "utf8");
    const cells = page.slice(
      page.indexOf("function columns"),
      page.indexOf("function Tiles"),
    );
    expect(cells).not.toMatch(/abbreviate/);
    expect(page.slice(page.indexOf("function Tiles"))).toMatch(
      /abbreviate: true/,
    );
  });
});
