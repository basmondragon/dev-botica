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

  it("ships exactly one sync-state component, and it is S2's", () => {
    // S0 asserted this file did **not** exist, because §B.9 was S2's to write.
    // S2 has written it, and the assertion flips rather than disappears: the
    // property that matters is not "there is none" but "there is exactly one",
    // and a second one is how two surfaces end up disagreeing about what
    // `pending` means (§B.9.1).
    const components = files.filter(
      (file) => /sync-status/i.test(file) && !/\.test\.tsx?$/.test(file),
    );
    expect(components.map((file) => file.replace(SRC, ""))).toEqual([
      "sync/sync-status.tsx",
    ]);
  });
});

/**
 * *Acceptance* 30 · **the boundary §5 requires, checked as a boundary rather
 * than as a convention.**
 *
 * All client sync code lives in one module and no domain code calls the
 * replication API directly, so that if §4's measurements ever justify a sync
 * engine — PowerSync, ElectricSQL, logical replication — it replaces that
 * module and nothing else in the product moves. A convention nobody can fail is
 * a convention that quietly stops being true around the fourth stage.
 */
describe("§5 · the client sync boundary", () => {
  /**
   * What actually ships. The generated schema names every path in the product
   * and calls none of them, and a test file is the check rather than the code —
   * neither is in the bundle, and both would make these greps report
   * themselves.
   */
  const shipped = [...walk(SRC)].filter(
    (file) =>
      /\.tsx?$/.test(file) &&
      !/\.test\.tsx?$/.test(file) &&
      !/\.gen\.ts$/.test(file),
  );

  /** A rule reads code, not the prose beside it — the same blanking the §B.16
   *  conformance greps do, and for the same reason. */
  function code(file: string) {
    return readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  }

  const outside = (file: string) => !file.replace(SRC, "").startsWith("sync/");
  const named = (files: string[]) => files.map((file) => file.replace(SRC, ""));

  it("imports rxdb nowhere outside src/sync", () => {
    const offenders = shipped.filter(
      (file) => outside(file) && /from ["']rxdb/.test(code(file)),
    );
    expect(named(offenders)).toEqual([]);
  });

  it("issues a cursor query nowhere outside src/sync", () => {
    const offenders = shipped.filter(
      (file) =>
        outside(file) && /api\/sync\/(pull|push|digest)/.test(code(file)),
    );
    expect(named(offenders)).toEqual([]);
  });

  it("ships no numbering string anywhere in the bundle", () => {
    // `blocked` ships with its geometry and **without its words** (A6, §8,
    // §B.9.1). A string naming an exhausted numbering range would be live copy
    // for a condition that cannot occur, which is how a stale string ships:
    // nothing renders it, so no review catches it, and it reads as a promise
    // the product still makes.
    //
    // **Numbering has no exception, at any stage.** Botica allocates no fiscal
    // number, so there is nowhere in the product a resolution or a range could
    // honestly be named.
    const offenders = shipped.filter((file) =>
      /numeraci[oó]n|resoluci[oó]n dian|rango fiscal/i.test(code(file)),
    );
    expect(named(offenders)).toEqual([]);
  });

  it("names a CUDE nowhere outside src/fiscal", () => {
    // **Narrowed by S5, and only narrowed.** Botica generates no CUDE and signs
    // nothing (A9) -- what changed is that a *target* may return one, and
    // `fiscal_documents.cude` records whatever the client's own invoicing
    // system answered (ledger, disputed columns). Showing it on that stage's
    // own office surface is the whole of the exception: the till still carries
    // the string nowhere, which is what this guard was written for.
    const inFiscal = (file: string) =>
      file.replace(SRC, "").startsWith("fiscal/");
    const offenders = shipped.filter(
      (file) => !inFiscal(file) && /CUDE|CUFE/i.test(code(file)),
    );
    expect(named(offenders)).toEqual([]);
  });
});
