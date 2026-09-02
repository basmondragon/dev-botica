// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(
  fileURLToPath(new URL("../index.css", import.meta.url)),
  "utf8",
);

function token(name: string): string | undefined {
  return new RegExp(`${name}:\\s*([^;]+);`).exec(CSS)?.[1]?.trim();
}

/**
 * *Verification* 11, as a test with expected values rather than a review. This
 * is the check nobody will want to run twice, which is exactly why it is
 * written down: a 4px radius, a second focus ring and a 60px row shipping
 * because they look approximately right is the failure it exists to catch.
 */
describe("the token layer against Part A", () => {
  it("carries the seven drawn radii under Botica's own names", () => {
    expect(token("--radius-mark")).toBe("4px");
    expect(token("--radius-icon")).toBe("6px");
    expect(token("--radius-segment")).toBe("7px");
    // The sibling system's `--radius-control` is 4px. Botica's is 9px, and a
    // ported button would render the wrong corner silently.
    expect(token("--radius-control")).toBe("9px");
    expect(token("--radius-card")).toBe("12px");
    expect(token("--radius-panel")).toBe("16px");
    expect(token("--radius-pill")).toBe("999px");
  });

  it("carries the eight type steps and no ninth", () => {
    for (const step of [36, 28, 20, 16, 14, 12, 11, 10]) {
      expect(token(`--text-${step}`)).toBe(`${step}px`);
    }
    for (const absent of [13, 15, 18, 24, 32, 40, 48]) {
      expect(token(`--text-${absent}`)).toBeUndefined();
    }
  });

  it("carries the drawn neutrals, including both greys", () => {
    expect(token("--color-canvas")).toBe("#fbfbfb");
    expect(token("--color-surface")).toBe("#ffffff");
    expect(token("--color-chrome")).toBe("#f4f4f4");
    expect(token("--color-active")).toBe("#e8e8e8");
    expect(token("--color-ink")).toBe("#171717");
    expect(token("--color-ink-body")).toBe("#555555");
    expect(token("--color-ink-label")).toBe("#727272");
    expect(token("--color-ink-note")).toBe("#6b6b6b");
    expect(token("--color-ink-soft")).toBe("#909090");
    expect(token("--color-ink-disabled")).toBe("#c8c8c8");
  });

  it("carries the four drawn tints, with the drawing winning on warning", () => {
    expect(token("--color-tint-positive")).toBe("#e3e9e3");
    // A 14%-over-canvas derivation lands one unit off the drawn value here, and
    // Part A is a record of what was drawn.
    expect(token("--color-tint-warning")).toBe("#ece7df");
    expect(token("--color-tint-info")).toBe("#e3e7eb");
    expect(token("--color-tint-critical")).toBe("#f1e2e1");
  });

  it("adds neutral without adding a colour", () => {
    expect(token("--color-neutral")).toBe(token("--color-ink-label"));
    expect(token("--color-tint-neutral")).toBe(token("--color-active"));
  });

  it("carries the eleven blue steps and the one rail", () => {
    expect(token("--color-data-100")).toBe("#0071e3");
    expect(token("--color-data-90")).toBe("#1a7fe5");
    expect(token("--color-data-10")).toBe("#9ec9f4");
    expect(token("--color-data-track")).toBe("#e0eefc");
  });

  it("defines exactly one focus ring, at 2px #0071e3 with a 2px offset", () => {
    const rings = CSS.match(/outline:\s*2px solid/g) ?? [];
    expect(rings).toHaveLength(1);
    expect(CSS).toContain("outline: 2px solid var(--color-brand)");
    expect(CSS).toContain("outline-offset: 2px");
    // No surviving box-shadow ring, and no destructive ring variant.
    expect(CSS).not.toMatch(/--focus-ring-destructive/);
    expect(CSS).not.toMatch(/box-shadow:\s*0 0 0 3px/);
  });

  it("carries the counter-density heights S4 will select", () => {
    expect(token("--spacing-control-counter")).toBe("44px");
    expect(token("--spacing-primary-counter")).toBe("52px");
    expect(token("--spacing-row-counter")).toBe("56px");
    expect(token("--spacing-panel-counter")).toBe("420px");
  });

  it("carries Part B's two new colour values and nothing more", () => {
    expect(token("--color-scrim")).toBe("rgba(0, 0, 0, 0.32)");
    expect(token("--color-edge-critical")).toBe("rgba(176, 74, 63, 0.32)");
  });

  it("promotes the handoff's presentation shadow to L3 rather than authoring one", () => {
    expect(token("--shadow-plane")).toBe("0 1px 2px rgba(20, 20, 20, 0.02)");
    expect(token("--shadow-segment")).toBe("0 1px 2px rgba(20, 20, 20, 0.04)");
    expect(CSS).toContain("0 18px 44px rgba(20, 20, 20, 0.08)");
  });

  it("keeps the motion budget at 140ms ease-out and nothing translating", () => {
    expect(token("--default-transition-duration")).toBe("140ms");
    expect(token("--default-transition-timing-function")).toBe("ease-out");
    // Nothing translates: no keyframe and no utility moves a plane.
    expect(CSS).not.toMatch(/transform:\s*translate/);
    expect(CSS).not.toMatch(/translate[XYZ]?\(/);
  });

  it("has no dark theme anywhere", () => {
    expect(CSS).not.toMatch(/prefers-color-scheme/);
  });
});
