// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));

/**
 * *Verification* 12 · the five modules that carry no meaning in Botica are
 * **absent from the tree, not merely unused**, and that is a claim only a grep
 * settles. A file left in place with no importer passes every other check in
 * this document, which is exactly why this one is a grep and not a review.
 *
 * `provenance` is the one that matters: it marks a field's data *source*, where
 * §B.9 wants how far behind a figure is -- which makes it the most attractive
 * wrong port in the whole layer, and a module still sitting on disk is a module
 * S2 finds the day it goes looking for a sync-state component.
 */
const DROPPED = ["us-map", "evidence", "provenance", "profile", "brand"];

function* walk(directory: string): Generator<string> {
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else yield full;
  }
}

describe("the five dropped modules", () => {
  const files = [...walk(SRC)];

  it("leaves no file whose path carries any of the five names", () => {
    // `brand` is the exception the port makes deliberately: ELOS's module is a
    // logo and a wordmark, and Botica's brand is a 24px square drawn in §A.12 --
    // so the name is reused for something the design system actually fixes.
    const forbidden = DROPPED.filter((name) => name !== "brand");
    const offenders = files.filter((file) =>
      forbidden.some((name) => file.includes(name)),
    );
    expect(offenders).toEqual([]);
  });

  it("leaves no import of any of them", () => {
    const forbidden = DROPPED.filter((name) => name !== "brand");
    const offenders = files
      .filter((file) => /\.(tsx?|css)$/.test(file))
      .filter((file) => {
        const source = readFileSync(file, "utf8");
        return forbidden.some((name) =>
          new RegExp(`from ["'][^"']*${name}["']`).test(source),
        );
      });
    expect(offenders).toEqual([]);
  });

  it("ships no sync-state component, because §B.9 is S2's", () => {
    const offenders = files.filter((file) =>
      /sync-status|staleness/i.test(file),
    );
    expect(offenders).toEqual([]);
  });
});
