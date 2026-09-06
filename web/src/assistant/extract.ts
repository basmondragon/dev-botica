import type { Bundle } from "./bundle";

/**
 * Step 1 of the pipeline: what the customer said, as keys the filter matches on.
 *
 * **The lexicon runs on the device, always, even when the network is up**
 * (S8, *1 · Extract*). When the model is reachable it may add chips and may
 * never remove one this found — a model that could narrow the symptom set could
 * un-filter a product the safety layer had excluded, which is the one thing it
 * must not be able to do.
 *
 * **The algorithm is here and the data is not.** Every surface form, every
 * label and every negation word comes down in the bundle, so there is one
 * lexicon in the product rather than one on each side of the wire — and a
 * vocabulary change is a bundle version rather than a deployment.
 *
 * **Four kinds, and only three of them are chips.** `symptom`, `population` and
 * `active_treatment` are drawn; `duration` is not, because the handoff's own
 * screen draws four chips for a transcript that states a duration.
 */

export type FactKind =
  "symptom" | "population" | "active_treatment" | "duration";

export interface Fact {
  key: string;
  label: string;
  kind: FactKind;
  source: "lexicon" | "model";
  negated?: boolean;
  value?: number;
  unit?: string;
}

/** Accents stripped, lowercased — the same folding `search_name` uses, applied
 *  to both sides of every match so a cashier typing without accents at a
 *  counter is not a cashier the extractor ignores. */
export function fold(text: string): string {
  return (text ?? "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Mn}/gu, "");
}

/** Between kinds the row is drawn symptoms, then who it is for, then what they
 *  are already on — the order the handoff's own chips read in. */
const KIND_ORDER: Record<FactKind, number> = {
  symptom: 0,
  population: 1,
  active_treatment: 2,
  duration: 3,
};

/** Four words is *"no tiene nada de fiebre"* and stops short of *"no le sirvió
 *  el jarabe, tiene fiebre"*. */
const NEGATION_WINDOW_WORDS = 4;

const TEMPERATURE_FLOOR = 34;
const TEMPERATURE_CEILING = 43;

export function extract(transcript: string, bundle: Bundle | null): Fact[] {
  if (!bundle) return [];
  const folded = fold(transcript ?? "");
  if (!folded.trim()) return [];
  const words = [...folded.matchAll(/[a-z0-9]+/g)].map((match) => ({
    at: match.index,
    end: match.index + match[0].length,
    word: match[0],
  }));

  const facts: (Fact & { at: number })[] = [];
  facts.push(
    ...match(folded, words, entries(bundle.symptoms), "symptom", bundle),
  );
  facts.push(
    ...match(folded, words, entries(bundle.populations), "population", bundle),
  );
  facts.push(...treatments(folded, bundle));

  const temperature = readTemperature(folded);
  for (const fact of facts) {
    if (fact.kind === "symptom" && fact.key === "fever" && !fact.negated) {
      if (temperature !== null) {
        fact.value = temperature;
        fact.unit = "celsius";
        fact.label = `fiebre ${spanishNumber(temperature)} °C`;
      }
    }
  }

  const days = readDuration(folded, bundle);
  if (days !== null) {
    facts.push({
      key: "duration_days",
      label: `${spanishNumber(days)} días`,
      kind: "duration",
      source: "lexicon",
      value: days,
      unit: "days",
      at: Number.MAX_SAFE_INTEGER,
    });
  }

  facts.sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.at - b.at);
  return facts.map(({ at: _at, ...fact }) => fact);
}

type Entry = { label: string; forms: string[] };

/** The bundle's tables arrive as open objects because the schema states
 *  them that way; every value in them is one of these. */
function entries(table: unknown): Record<string, Entry> {
  return (table ?? {}) as Record<string, Entry>;
}

function match(
  folded: string,
  words: { at: number; end: number; word: string }[],
  table: Record<string, Entry> | undefined,
  kind: FactKind,
  bundle: Bundle,
): (Fact & { at: number })[] {
  const found: (Fact & { at: number })[] = [];
  for (const [key, entry] of Object.entries(table ?? {})) {
    // Longest surface form first: *"dolor de estomago"* must not be found as
    // *"dolor de cabeza"*'s neighbour, and *"sangre en la deposicion"* must win
    // over the bare *"con sangre"* that sits inside it.
    const forms = [...(entry.forms ?? [])].sort((a, b) => b.length - a.length);
    for (const form of forms) {
      const at = findWhole(folded, fold(form));
      if (at === null) continue;
      const negated = isNegated(folded, words, at, bundle);
      found.push({
        key,
        label: negated ? `sin ${entry.label}` : entry.label,
        kind,
        source: "lexicon",
        at,
        ...(negated ? { negated: true } : {}),
      });
      break;
    }
  }
  return found;
}

/**
 * *"toma losartán"* — an ingredient the customer says they are already on.
 *
 * **The lead word is required.** A transcript naming a molecule the cashier is
 * about to sell is not a statement that the customer already takes it, and an
 * `interacts_with_ingredient` clause fired by the product on the counter is a
 * filter that removes exactly the thing it was asked about.
 */
function treatments(folded: string, bundle: Bundle): (Fact & { at: number })[] {
  const leads = bundle.treatment_leads ?? [];
  const found: (Fact & { at: number })[] = [];
  for (const [key, entry] of Object.entries(entries(bundle.ingredients))) {
    const forms = [...(entry.forms ?? [])].sort((a, b) => b.length - a.length);
    for (const form of forms) {
      const at = findWhole(folded, fold(form));
      if (at === null) continue;
      const lead = folded.slice(Math.max(0, at - 40), at);
      if (!leads.some((word) => lead.includes(fold(word)))) continue;
      found.push({
        key,
        label: `tratamiento activo · ${entry.label}`,
        kind: "active_treatment",
        source: "lexicon",
        at,
      });
      break;
    }
  }
  return found;
}

/** Where `needle` sits on word boundaries, or `null`. A plain `includes` would
 *  find `tos` inside `estomago` and put a cough chip on every stomach complaint
 *  in the country. */
function findWhole(haystack: string, needle: string): number | null {
  if (!needle) return null;
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(needle, from);
    if (at < 0) return null;
    const before = at > 0 ? haystack[at - 1]! : " ";
    const after = haystack[at + needle.length] ?? " ";
    if (!/[a-z0-9]/.test(before) && !/[a-z0-9]/.test(after)) return at;
    from = at + 1;
  }
}

function isNegated(
  folded: string,
  words: { at: number; end: number; word: string }[],
  at: number,
  bundle: Bundle,
): boolean {
  const negations = new Set((bundle.negations ?? []).map(fold));
  const index = words.findIndex((one) => one.at >= at);
  const upper = index < 0 ? words.length : index;
  const window = words.slice(Math.max(0, upper - NEGATION_WINDOW_WORDS), upper);
  for (const one of window) {
    if (!negations.has(one.word)) continue;
    // A comma or a full stop ends the denial: *"no le sirvió el jarabe, tiene
    // fiebre"* is not a transcript that denies a fever.
    const between = folded.slice(one.end, at);
    if (/[,;.]| pero | aunque /.test(between)) continue;
    return true;
  }
  return false;
}

function readDuration(folded: string, bundle: Bundle): number | null {
  const numbers = bundle.number_words ?? {};
  const written = Object.keys(numbers).join("|");
  const pattern = new RegExp(
    `\\b(\\d{1,3}${written ? `|${written}` : ""})\\s+(dias?|semanas?|meses|mes|horas?)\\b`,
  );
  const found = pattern.exec(folded);
  if (!found) return null;
  const raw = found[1]!;
  const count = /^\d+$/.test(raw) ? Number(raw) : (numbers[raw] ?? 0);
  const unit = found[2]!;
  if (unit.startsWith("hora")) return Math.round((count / 24) * 100) / 100;
  if (unit.startsWith("semana")) return count * 7;
  if (unit.startsWith("mes")) return count * 30;
  return count;
}

const TEMPERATURE =
  /(?:fiebre|temperatura|calentura)\D{0,12}(\d{2}(?:[.,]\d)?)|(\d{2}(?:[.,]\d)?)\s*(?:grados|°\s*c|ºc|c\b)/g;

function readTemperature(folded: string): number | null {
  TEMPERATURE.lastIndex = 0;
  for (;;) {
    const found = TEMPERATURE.exec(folded);
    if (!found) return null;
    const raw = found[1] ?? found[2];
    if (!raw) continue;
    const value = Number(raw.replace(",", "."));
    // A `39` that came out of a pack size or a price is not a fever, and a
    // threshold compared against one is a filter firing on nothing.
    if (value >= TEMPERATURE_FLOOR && value <= TEMPERATURE_CEILING)
      return value;
  }
}

/** §A.11 · the decimal separator is a comma, and a whole number carries none. */
function spanishNumber(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(1).replace(".", ",");
}
