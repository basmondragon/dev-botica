#!/usr/bin/env node

/**
 * §B.16 · the conformance greps, run as a gate.
 *
 * After a port this is what catches a value that came across unconverted, and
 * it is the cheapest of the checks, so it runs before anything that needs a
 * browser. It answers the mechanical half of the checklist: the closed type
 * scale, the token discipline, the one focus ring, the motion budget, and the
 * absence of a `dark:` variant.
 *
 * What it cannot answer -- geometry, density, the solid/hollow rule -- belongs
 * to the checks in S0's *Verification* section and to a person reading a screen.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SRC = join(ROOT, "src");
const TOKENS = readFileSync(join(SRC, "index.css"), "utf8");

/** §B.1 · the eight steps, and nothing else. */
const TYPE_STEPS = new Set(["10", "11", "12", "14", "16", "20", "28", "36"]);

/** §A.8 · seven radii under Botica's own names. */
const RADIUS_NAMES = new Set([
  "mark",
  "check",
  "icon",
  "segment",
  "control",
  "card",
  "panel",
  "pill",
]);

const RULES = [
  {
    id: "B.1 · the closed type scale",
    test: (line) =>
      [...line.matchAll(/\btext-(\d+)\b/g)]
        .filter((match) => !TYPE_STEPS.has(match[1]))
        .map((match) => match[0]),
    why: "App surfaces draw from the eight steps in §B.1 only.",
  },
  {
    id: "B.1 · no arbitrary type size",
    test: (line) =>
      [...line.matchAll(/\btext-\[[^\]]*(px|rem|em)[^\]]*\]/g)].map(
        (m) => m[0],
      ),
    why: "An arbitrary font size is invisible to the closed-scale check.",
  },
  {
    id: "B.16·2 · no hex literal in a component",
    test: (line) => [...line.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0]),
    why: "Every colour is a token from Part A or §B.15.",
  },
  {
    id: "A.8 · seven radii, under Botica's own names",
    test: (line) =>
      [...line.matchAll(/\brounded-([a-z-]+)\b/g)]
        .filter(
          (match) =>
            !RADIUS_NAMES.has(match[1]) &&
            ![
              "t",
              "b",
              "l",
              "r",
              "tl",
              "tr",
              "bl",
              "br",
              "none",
              "full",
            ].includes(match[1]),
        )
        .map((match) => match[0]),
    why: "Every radius is 4, 6, 7, 9, 12, 16 or 999, under the §A.8 names.",
  },
  {
    id: "B.14 · no transition-all",
    test: (line) => (line.includes("transition-all") ? ["transition-all"] : []),
    why: 'Enumerate the properties — differently-timed arrivals are what read as "not smooth".',
  },
  {
    id: "B.14 · nothing translates",
    test: (line) =>
      [
        ...line.matchAll(/(hover|active|group-hover):(-?translate|scale)-/g),
      ].map((m) => m[0]),
    why: "The plane holds its position. No lift on hover or press (§A.7, §B.14).",
  },
  {
    id: "B.5.1 · focus is never suppressed",
    test: (line) =>
      [
        ...line.matchAll(
          /\b(?:group-)?focus(?:-visible|-within)?:outline-none\b/g,
        ),
      ].map((m) => m[0]),
    why: "A focus:outline-none with no replacement is a defect, not a style choice.",
  },
  {
    id: "B.5.1 · there is exactly one focus ring",
    test: (line) =>
      [...line.matchAll(/focus-visible:(?:ring|shadow|outline)-/g)].map(
        (m) => m[0],
      ),
    why: "One ring, defined once in index.css. No per-surface variant.",
  },
  {
    id: "B.16·20 · no dark variant exists",
    test: (line) => [...line.matchAll(/\bdark:/g)].map((m) => m[0]),
    why: "There is no dark theme (architecture §12). Do not scaffold one.",
  },
  {
    id: "A.11 · Intl is touched in one module",
    test: (line) =>
      [...line.matchAll(/\btoLocaleString\b|\bIntl\.[A-Za-z]+/g)].map(
        (m) => m[0],
      ),
    why: "Every figure goes through src/ui/format.ts. No call site formats its own.",
  },
];

/** Files a rule does not apply to, and why. */
const EXEMPT = {
  "B.16·2 · no hex literal in a component": [
    "src/index.css", // the token layer is where the values live
    "src/ui/tile.tsx", // §A.5's eleven drawn ramp steps, stated once
    "src/ui/tokens.test.ts", // the check that the token layer holds them
  ],
  "B.5.1 · there is exactly one focus ring": ["src/index.css"],
  "A.11 · Intl is touched in one module": ["src/ui/format.ts"],
  "B.16·20 · no dark variant exists": [],
};

const SKIP_FILES = new Set(["src/routeTree.gen.ts", "src/api/schema.gen.ts"]);

/**
 * A rule reads code, not the prose beside it. Comments are blanked in place --
 * same length, same newlines -- so a hit still reports the line it is on.
 */
function blankComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(
      /(^|[^:])\/\/[^\n]*/g,
      (line, lead) => lead + " ".repeat(line.length - lead.length),
    );
}

function* walk(directory) {
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) {
      yield* walk(full);
    } else if (/\.(tsx?|css)$/.test(entry)) {
      yield full;
    }
  }
}

let failures = 0;
for (const file of walk(SRC)) {
  const path = relative(ROOT, file);
  if (SKIP_FILES.has(path)) continue;
  const lines = blankComments(readFileSync(file, "utf8")).split("\n");
  lines.forEach((code, index) => {
    for (const rule of RULES) {
      if ((EXEMPT[rule.id] ?? []).includes(path)) continue;
      for (const hit of rule.test(code)) {
        failures += 1;
        console.error(
          `${path}:${index + 1}  ${rule.id}\n    ${hit} — ${rule.why}`,
        );
      }
    }
  });
}

/** The token layer itself has to carry what the rules assume. */
const REQUIRED_TOKENS = [
  "--radius-control: 9px",
  "--radius-card: 12px",
  "--radius-panel: 16px",
  "--color-brand: #0071e3",
  "--color-tint-warning: #ece7df",
  "--text-16: 16px",
  "--spacing-row-counter: 56px",
];
for (const token of REQUIRED_TOKENS) {
  if (!TOKENS.includes(token)) {
    failures += 1;
    console.error(`src/index.css  the token layer is missing \`${token}\``);
  }
}

if (/outline:\s*2px solid var\(--color-brand\)/.test(TOKENS) === false) {
  failures += 1;
  console.error(
    "src/index.css  the focus ring must be `outline: 2px solid var(--color-brand)` at `outline-offset: 2px` (§B.5.1)",
  );
}

if (failures > 0) {
  console.error(`\n${failures} conformance failure(s).`);
  process.exit(1);
}
console.log(
  "conformance: type scale, tokens, radii, focus ring, motion — all clear",
);
