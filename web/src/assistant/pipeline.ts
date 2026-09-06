import type { ItemDoc, LotDoc, PolicyDoc, StockDoc } from "@/sync/registry";
import type { SyncDatabase } from "@/sync/store";
import { currentPrices } from "@/sync/local";
import { businessDay } from "@/ui/format";
import { toCents } from "@/counter/money";
import type { Bundle } from "./bundle";
import { fold, type Fact } from "./extract";
import {
  IRRELEVANT,
  SATISFIED,
  evaluate,
  makesConditional,
  readExtraction,
  type Extraction,
  type Outcome,
} from "./filters";

/**
 * The candidate pipeline on the device: seed, filter, rank, label.
 *
 * **The model participates in exactly one step and it is not this one.** Every
 * product that reaches a card is chosen here, from this sede's own stock, this
 * tenant's own rules and the safety layer, with no request anywhere on the
 * path — which is why the three cards paint in under 150 ms with the network
 * physically disconnected, and why they paint at all when it is.
 *
 * **It is the same five steps the server runs** (`core/assistant/pipeline.py`).
 * The duplication is inherent rather than incidental: a pipeline that only ran
 * on a server would make every criterion in *Offline* false. What is not
 * duplicated is the data — the lexicon, the vocabulary, the symptom map and
 * every Spanish sentence come down in one bundle.
 */

export type SuggestionKind = "first_choice" | "conditional" | "bought_together";

export interface Card {
  item: ItemDoc;
  type: SuggestionKind;
  reasonCode: string;
  reason: string;
  /** In centavos, and **the same figure S4 stamps on the line**: both read the
   *  effective price from the same local rows. */
  price: number;
  availableQuantity: number;
  warningId: string | null;
  ruleConfidence: "low" | "medium" | "high" | null;
  rank: number;
}

export interface Excluded {
  item_id: string;
  item_name: string;
  reason: string;
  warning_id: string | null;
}

export interface Outcome_ {
  cards: Card[];
  /** What survived the filter — the `3 de 12 referencias` denominator, which is
   *  **not** the number of cards. */
  candidateCount: number;
  /** How many references the symptom map reached before the filter, which is
   *  what card C's empty state counts. */
  seededCount: number;
  excluded: Excluded[];
  facts: Fact[];
}

interface Candidate {
  item: ItemDoc;
  seedStrength: number;
  seedKeys: Set<string>;
  quantity: number;
  price: number;
  rule: PolicyDoc | null;
  anchorName: string;
  warning: PolicyDoc | null;
  warningOutcome: Outcome;
  substitute: boolean;
  fromTicket: boolean;
}

/** *Rank, and label*'s three seed strengths, in the order it fixes them. */
const EXACT_CATEGORY = 1.0;
const INGREDIENT_MATCH = 0.8;
const NAME_MATCH = 0.5;

export const EXCLUDED_OUT_OF_STOCK = "out_of_stock";
export const EXCLUDED_PRESCRIPTION = "requires_prescription";
export const EXCLUDED_CONTROLLED = "controlled";
export const EXCLUDED_INVIMA_EXPIRED = "invima_expired";
export const EXCLUDED_WARNING = "warning_blocking";

const TYPE_ORDER: SuggestionKind[] = [
  "first_choice",
  "conditional",
  "bought_together",
];

export async function run(
  database: SyncDatabase,
  {
    locationId,
    facts,
    bundle,
    ticketItemIds = [],
    cardCount,
  }: {
    locationId: string;
    facts: Fact[];
    bundle: Bundle;
    ticketItemIds?: string[];
    cardCount?: number;
  },
): Promise<Outcome_> {
  const extraction = readExtraction(facts, bundle.age_populations ?? []);
  const seeded = await seed(database, extraction, bundle);
  const reached = await throughRules(
    database,
    locationId,
    seeded,
    ticketItemIds,
  );
  const everything = new Map(seeded);
  for (const [key, candidate] of reached) {
    if (!everything.has(key)) everything.set(key, candidate);
  }

  const { survivors, excluded } = await filter(
    database,
    locationId,
    everything,
    extraction,
  );
  const ranked = rank(survivors);
  markSubstitutes(ranked, everything, excluded);
  return {
    cards: compose(
      ranked,
      cardCount ?? bundle.suggestion_card_count ?? 3,
      bundle,
    ),
    candidateCount: ranked.length,
    seededCount: seeded.size,
    excluded,
    facts,
  };
}

// ---------------------------------------------------------------------------
// 2 · seed
// ---------------------------------------------------------------------------

async function seed(
  database: SyncDatabase,
  extraction: Extraction,
  bundle: Bundle,
): Promise<Map<string, Candidate>> {
  const map = (bundle.symptom_category_map ?? {}) as Record<string, string[]>;
  const keys = [...extraction.symptoms.keys()].sort();
  const candidates = new Map<string, Candidate>();
  if (keys.length === 0) return candidates;

  const byCategory = new Map<string, string[]>();
  const unmapped: string[] = [];
  for (const key of keys) {
    const categories = map[key] ?? [];
    if (categories.length === 0) {
      unmapped.push(key);
      continue;
    }
    for (const category of categories) {
      byCategory.set(category, [...(byCategory.get(category) ?? []), key]);
    }
  }

  const catalogue = await readCatalogue(database);
  for (const item of catalogue) {
    const keys = byCategory.get(item.category_id ?? "");
    if (keys) note(candidates, item, EXACT_CATEGORY, keys);
  }
  // **The fallback covers a key the map does not cover**, and it is weaker on
  // purpose. It does not stand in for a map that is entirely empty: a fallback
  // that fires on every key is not a fallback, and `knowsTheCatalog` draws that
  // state as configuration rather than as cold start.
  for (const key of unmapped) {
    for (const { item, strength } of bySurfaceForm(catalogue, key, bundle)) {
      note(candidates, item, strength, [key]);
    }
  }
  return candidates;
}

/**
 * The catalog, read once per session and kept in memory.
 *
 * **A till holds four thousand references and the store indexes only
 * `search_name`**, so a per-question scan by `category_id` would be a full pass
 * over the collection on the one path §4 budgets at 150 ms. The catalog is a
 * snapshot the till re-pulls rather than edits, so it is read once and dropped
 * whenever the collection changes — which is the sync engine writing a delta,
 * on a cadence measured in seconds rather than in keystrokes.
 */
let catalogue_: ItemDoc[] | null = null;
let watching: { unsubscribe: () => void } | null = null;

async function readCatalogue(database: SyncDatabase): Promise<ItemDoc[]> {
  if (catalogue_) return catalogue_;
  catalogue_ = (await database.collections
    .items!.find()
    .exec()) as unknown as ItemDoc[];
  watching ??= database.collections.items!.$.subscribe(() => {
    catalogue_ = null;
  });
  return catalogue_;
}

/** For a browser re-claimed as a different till, and for the tests. */
export function forgetCatalogue() {
  catalogue_ = null;
  watching?.unsubscribe();
  watching = null;
}

function note(
  candidates: Map<string, Candidate>,
  item: ItemDoc,
  strength: number,
  keys: string[],
) {
  const held = candidates.get(item.id);
  if (!held) {
    candidates.set(item.id, {
      item,
      seedStrength: strength,
      seedKeys: new Set(keys),
      quantity: 0,
      price: 0,
      rule: null,
      anchorName: "",
      warning: null,
      warningOutcome: IRRELEVANT,
      substitute: false,
      fromTicket: false,
    });
    return;
  }
  held.seedStrength = Math.max(held.seedStrength, strength);
  for (const key of keys) held.seedKeys.add(key);
}

function bySurfaceForm(
  catalogue: ItemDoc[],
  key: string,
  bundle: Bundle,
): { item: ItemDoc; strength: number }[] {
  const table = (bundle.symptoms ?? {}) as Record<
    string,
    { label: string; forms: string[] }
  >;
  const entry = table[key];
  if (!entry) return [];
  const terms = [...new Set([entry.label, ...(entry.forms ?? [])].map(fold))];
  const found: { item: ItemDoc; strength: number }[] = [];
  for (const item of catalogue) {
    const ingredient = fold(item.active_ingredient ?? "");
    if (ingredient && terms.some((term) => ingredient.includes(term))) {
      found.push({ item, strength: INGREDIENT_MATCH });
      continue;
    }
    if (terms.some((term) => item.search_name.includes(term))) {
      found.push({ item, strength: NAME_MATCH });
    }
  }
  return found;
}

/**
 * Step 2's other half: what a `cross_sell_rule` reaches from here.
 *
 * Anchors are the symptom-seeded items **and the lines already on the
 * ticket** — the second is what `ticket_companion` is, and it is why a
 * suggestion appears for a customer who described nothing and scanned one box.
 * **The sede's rule wins over the network's**, which is what makes *"en este
 * punto el 64%"* a true sentence rather than a network claim.
 */
async function throughRules(
  database: SyncDatabase,
  locationId: string,
  seeded: Map<string, Candidate>,
  ticketItemIds: string[],
): Promise<Map<string, Candidate>> {
  const anchors = [...new Set([...seeded.keys(), ...ticketItemIds])];
  const reached = new Map<string, Candidate>();
  if (anchors.length === 0) return reached;
  const held = (await database.collections
    .stock_policies!.find({
      selector: { kind: "rule", item_id: { $in: anchors } },
    })
    .exec()) as unknown as PolicyDoc[];
  // The registry serves this sede's rules and the network's and no other
  // sede's, so this predicate is normally the whole set. It is stated anyway:
  // a till moved between sedes wipes and re-pulls its location-scoped streams,
  // and until that has drained it is holding the previous sede's rules.
  const rows = held.filter(
    (row) => row.location_id === null || row.location_id === locationId,
  );
  if (rows.length === 0) return reached;

  const names = await itemNames(database, [
    ...new Set(rows.flatMap((row) => [row.item_id, row.item_b_id ?? ""])),
  ]);
  const ticket = new Set(ticketItemIds);
  const best = new Map<string, PolicyDoc>();
  for (const row of rows) {
    const key = row.item_b_id ?? "";
    if (!key) continue;
    const held = best.get(key);
    if (!held) {
      best.set(key, row);
      continue;
    }
    // A sede row beats a network row whatever their lifts say; between two rows
    // at one scope the higher lift wins.
    if (held.location_id === null && row.location_id !== null) {
      best.set(key, row);
    } else if (
      (held.location_id === null) === (row.location_id === null) &&
      Number(row.lift ?? 0) > Number(held.lift ?? 0)
    ) {
      best.set(key, row);
    }
  }
  for (const [key, rule] of best) {
    if (seeded.has(key) || ticket.has(key)) continue;
    const item = names.get(key);
    if (!item) continue;
    reached.set(key, {
      item,
      seedStrength: 0,
      seedKeys: new Set(),
      quantity: 0,
      price: 0,
      rule,
      anchorName: names.get(rule.item_id)?.name ?? "",
      warning: null,
      warningOutcome: IRRELEVANT,
      substitute: false,
      fromTicket: ticket.has(rule.item_id),
    });
  }
  return reached;
}

async function itemNames(database: SyncDatabase, ids: string[]) {
  const wanted = new Set(ids.filter(Boolean));
  const catalogue = await readCatalogue(database);
  return new Map(
    catalogue
      .filter((item) => wanted.has(item.id))
      .map((item) => [item.id, item]),
  );
}

// ---------------------------------------------------------------------------
// 3 · filter
// ---------------------------------------------------------------------------

async function filter(
  database: SyncDatabase,
  locationId: string,
  candidates: Map<string, Candidate>,
  extraction: Extraction,
): Promise<{ survivors: Candidate[]; excluded: Excluded[] }> {
  const ids = [...candidates.keys()];
  if (ids.length === 0) return { survivors: [], excluded: [] };
  const quantities = await onHand(database, locationId, ids);
  const warnings = await warningsFor(database, ids);
  const prices = await currentPrices(database, ids);

  const survivors: Candidate[] = [];
  const excluded: Excluded[] = [];
  const drop = (
    candidate: Candidate,
    reason: string,
    warningId: string | null = null,
  ) =>
    excluded.push({
      item_id: candidate.item.id,
      item_name: candidate.item.name,
      reason,
      warning_id: warningId,
    });

  for (const [key, candidate] of candidates) {
    const item = candidate.item;
    const quantity = quantities.get(key) ?? 0;
    // A service (`tracks_stock = false`) is eligible and skips the stock test:
    // there is nothing on a shelf to be out of. **`items.active` is not tested
    // here** — a deactivated item leaves the till as a departure marker, so a
    // device only ever holds active ones.
    if (item.tracks_stock && quantity <= 0) {
      drop(candidate, EXCLUDED_OUT_OF_STOCK);
      continue;
    }
    // **Never suggested** (§7). It stays sellable through S4's search by a
    // person who has seen the prescription; the assistant simply never proposes
    // it.
    if (item.requires_prescription) {
      drop(candidate, EXCLUDED_PRESCRIPTION);
      continue;
    }
    if (item.controlled) {
      drop(candidate, EXCLUDED_CONTROLLED);
      continue;
    }
    // Botica does not block the **sale** of a lapsed registration — it surfaces
    // the state and records the decision — but it declines to **recommend**
    // one. Suggesting is not selling.
    if (item.invima_status === "expired") {
      drop(candidate, EXCLUDED_INVIMA_EXPIRED);
      continue;
    }

    let blocked: PolicyDoc | null = null;
    let conditional: PolicyDoc | null = null;
    let conditionalOutcome: Outcome = IRRELEVANT;
    for (const warning of warnings.get(key) ?? []) {
      const outcome = evaluate(warning.triggers, extraction);
      if (outcome === SATISFIED && warning.severity === "blocking") {
        blocked = warning;
        break;
      }
      if (
        !conditional &&
        makesConditional(warning.type, warning.severity, outcome)
      ) {
        conditional = warning;
        conditionalOutcome = outcome;
      }
    }
    if (blocked) {
      drop(candidate, EXCLUDED_WARNING, blocked.id);
      continue;
    }
    const price = prices.get(key);
    if (price === undefined) {
      // A card with no price has no `Agregar` behind it: S4 refuses the line,
      // so offering it would be offering something the till cannot put on the
      // ticket.
      drop(candidate, EXCLUDED_OUT_OF_STOCK);
      continue;
    }
    candidate.warning = conditional;
    candidate.warningOutcome = conditionalOutcome;
    candidate.quantity = quantity;
    candidate.price = toCents(price);
    survivors.push(candidate);
  }
  return { survivors, excluded };
}

/** This sede's units, **excluding lots already expired**: a lot whose expiry
 *  has passed is stock the sede holds and must not sell. */
async function onHand(
  database: SyncDatabase,
  locationId: string,
  itemIds: string[],
): Promise<Map<string, number>> {
  const rows = (await database.collections
    .stock_on_hand!.find({ selector: { item_id: { $in: itemIds } } })
    .exec()) as unknown as StockDoc[];
  const mine = rows.filter((row) => row.location_id === locationId);
  const lotIds = [
    ...new Set(mine.map((row) => row.lot_id).filter(Boolean)),
  ] as string[];
  const lots = (await database.collections
    .lots!.find({ selector: { id: { $in: lotIds } } })
    .exec()) as unknown as LotDoc[];
  // **The pharmacy's own day, not UTC.** Bogotá is five hours behind, so an
  // ISO date taken off `toISOString()` rolls over at seven in the evening and
  // a lot expiring today would be counted as expired for the last five hours of
  // the trading day — on the one figure a cashier verifies by looking at the
  // shelf.
  const today = businessDay();
  const expired = new Set(
    lots
      .filter((lot) => lot.expires_at !== null && lot.expires_at <= today)
      .map((lot) => lot.id),
  );
  const held = new Map<string, number>();
  for (const row of mine) {
    if (row.lot_id && expired.has(row.lot_id)) continue;
    held.set(row.item_id, (held.get(row.item_id) ?? 0) + row.quantity);
  }
  return held;
}

async function warningsFor(database: SyncDatabase, itemIds: string[]) {
  const rows = (await database.collections
    .stock_policies!.find({
      selector: { kind: "warning", item_id: { $in: itemIds } },
    })
    .exec()) as unknown as PolicyDoc[];
  const held = new Map<string, PolicyDoc[]>();
  for (const row of rows) {
    held.set(row.item_id, [...(held.get(row.item_id) ?? []), row]);
  }
  return held;
}

// ---------------------------------------------------------------------------
// 4 · rank, and label
// ---------------------------------------------------------------------------

/**
 * Seed strength, then `lift`, then units descending, then price ascending.
 *
 * **The ranker deliberately reads no forecast.** S6 and S8 run in parallel off
 * S4, and a ranker that needed one could not be demonstrated until S6 landed.
 * The cost of being wrong is that the first card is occasionally a slow mover
 * the sede happens to be long on, which is a worse suggestion and never an
 * unsafe one. The trailing name is not a tie-break anybody reads: it is what
 * makes two runs over one shelf return one order.
 */
export function rank(candidates: Candidate[]): Candidate[] {
  return [...candidates].sort(
    (a, b) =>
      b.seedStrength - a.seedStrength ||
      Number(b.rule?.lift ?? 0) - Number(a.rule?.lift ?? 0) ||
      b.quantity - a.quantity ||
      a.price - b.price ||
      a.item.name.localeCompare(b.item.name, "es"),
  );
}

/** `substitute_available` — **the last box of its molecule on the shelf.** It
 *  is deliberately narrower than *anything ranked below something out of
 *  stock*: on a catalog of four thousand references some box of some molecule
 *  is always out, and a reason code that fires on every first card says
 *  nothing. */
function markSubstitutes(
  ranked: Candidate[],
  everything: Map<string, Candidate>,
  excluded: Excluded[],
) {
  const dropped = new Set(
    excluded
      .filter((one) => one.reason === EXCLUDED_OUT_OF_STOCK)
      .map((one) => molecule(everything.get(one.item_id)?.item))
      .filter(Boolean),
  );
  if (dropped.size === 0) return;
  const surviving = new Map<string, number>();
  for (const candidate of ranked) {
    const key = molecule(candidate.item);
    if (key) surviving.set(key, (surviving.get(key) ?? 0) + 1);
  }
  for (const candidate of ranked) {
    const key = molecule(candidate.item);
    if (key && dropped.has(key) && surviving.get(key) === 1) {
      candidate.substitute = true;
    }
  }
}

function molecule(item: ItemDoc | undefined): string {
  return (item?.active_ingredient ?? "").trim().toLowerCase();
}

function typeOf(candidate: Candidate): SuggestionKind {
  if (candidate.warning) return "conditional";
  if (candidate.rule) return "bought_together";
  return "first_choice";
}

/**
 * The drawn set: **one card per type, in the order the handoff draws them.**
 *
 * An empty type slot is never backfilled: two cards and no third is the whole
 * of *Sin reglas todavía*, which is a new tenant's normal first state and not
 * an error state. A build agent that filled the gap with a second analgesic
 * would make the cold-start path invisible exactly where the product most needs
 * it seen.
 */
function compose(ranked: Candidate[], limit: number, bundle: Bundle): Card[] {
  const buckets = new Map<SuggestionKind, Candidate[]>(
    TYPE_ORDER.map((kind) => [kind, []]),
  );
  for (const candidate of ranked)
    buckets.get(typeOf(candidate))!.push(candidate);

  const drawn = TYPE_ORDER.map((kind) => buckets.get(kind)![0])
    .filter((one): one is Candidate => !!one)
    .slice(0, limit);
  // Only **above** the three types does the list carry a further candidate,
  // which is what `symptom_secondary` is.
  let extra = limit - TYPE_ORDER.length;
  if (extra > 0) {
    const held = new Set(drawn);
    for (const candidate of ranked) {
      if (extra <= 0) break;
      if (held.has(candidate)) continue;
      drawn.push(candidate);
      held.add(candidate);
      extra -= 1;
    }
  }

  return drawn.map((candidate, index) => {
    const { code, reason } = reasonFor(
      candidate,
      index === 0 || !!candidate.warning,
      bundle,
    );
    return {
      item: candidate.item,
      type: typeOf(candidate),
      reasonCode: code,
      reason,
      price: candidate.price,
      availableQuantity: candidate.quantity,
      warningId: candidate.warning?.id ?? null,
      ruleConfidence: candidate.rule?.confidence_band ?? null,
      rank: index + 1,
    };
  });
}

/**
 * `(reason_code, reason)` — the code, and the Spanish sentence it renders.
 *
 * A `conditional` card's reason **is** the warning's own text, verbatim. It is
 * never templated and never rewritten, because a safety string a model
 * paraphrases has stopped being a safety string.
 */
function reasonFor(
  candidate: Candidate,
  first: boolean,
  bundle: Bundle,
): { code: string; reason: string } {
  const strings = (bundle.strings ?? {}) as Strings;
  if (candidate.warning) {
    return {
      code: "warning_conditional",
      reason: candidate.warning.text ?? "",
    };
  }
  const rule = candidate.rule;
  if (rule) {
    if (candidate.fromTicket) {
      return {
        code: "ticket_companion",
        reason: strings.templates?.ticket_companion ?? "",
      };
    }
    if (rule.location_id === null) {
      return {
        code: "bought_together_network",
        reason: fill(strings.templates?.bought_together_network, {
          anchor: candidate.anchorName,
        }),
      };
    }
    if (rule.confidence_band === "low") {
      // A percentage carried to two significant figures out of forty tickets is
      // a false precision, and it is read out loud to a customer.
      return {
        code: "bought_together_location",
        reason: fill(strings.templates?.bought_together_location_low, {
          anchor: candidate.anchorName,
        }),
      };
    }
    return {
      code: "bought_together_location",
      reason: fill(strings.templates?.bought_together_location, {
        share: share(rule.confidence),
        anchor: candidate.anchorName,
      }),
    };
  }
  if (candidate.substitute) {
    return {
      code: "substitute_available",
      reason: strings.templates?.substitute_available ?? "",
    };
  }
  if (first) {
    const key = [...candidate.seedKeys].sort()[0] ?? "";
    return {
      code: "symptom_primary",
      reason:
        strings.symptom_primary?.[key] ?? strings.symptom_primary_default ?? "",
    };
  }
  return {
    code: "symptom_secondary",
    reason: strings.templates?.symptom_secondary ?? "",
  };
}

export interface Strings {
  symptom_primary?: Record<string, string>;
  symptom_primary_default?: string;
  templates?: Record<string, string>;
  empty?: { title: string; one: string; many: string; none: string };
  local?: {
    primary_first: string;
    primary_conditional: string;
    primary_none: string;
    secondary_pair: string;
    secondary_one: string;
    secondary_many: string;
  };
}

export function stringsOf(bundle: Bundle | null): Strings {
  return ((bundle?.strings ?? {}) as Strings) ?? {};
}

/** The bundle's templates carry `{name}` holes. One interpolator, so a missing
 *  value renders as nothing rather than as the word `undefined`. */
export function fill(
  template: string | undefined,
  values: Record<string, string>,
): string {
  if (!template) return "";
  return template.replace(
    /\{(\w+)\}/g,
    (_whole, key: string) => values[key] ?? "",
  );
}

/** §A.11 · a percentage with a decimal comma and no trailing zero. */
function share(confidence: string | null | undefined): string {
  const value = Math.round(Number(confidence ?? 0) * 1000) / 10;
  return Number.isInteger(value)
    ? `${value}%`
    : `${value.toFixed(1).replace(".", ",")}%`;
}

/**
 * Card B, written from the same ranking by a template over `reason_code`.
 *
 * **In fewer words, and with no cross-sell sentence where there are no rules** —
 * the handoff's own *"La sede tiene las tres referencias disponibles."* is true
 * on a first morning and its *"el 64% de los clientes…"* is not.
 */
export function localProse(
  cards: Card[],
  candidateCount: number,
  bundle: Bundle | null,
): { primary: string; secondary: string } {
  const local = stringsOf(bundle).local;
  if (!local) return { primary: "", secondary: "" };
  const first = cards.find((card) => card.type === "first_choice");
  const conditional = cards.find((card) => card.type === "conditional");
  const primary = first
    ? fill(local.primary_first, { item: first.item.name })
    : conditional
      ? local.primary_conditional
      : local.primary_none;

  const pair = cards.find((card) => card.type === "bought_together");
  let secondary = "";
  if (pair && pair.reasonCode !== "ticket_companion") {
    secondary = fill(local.secondary_pair, {
      item: pair.item.name,
      anchor: anchorOf(pair.reason),
    });
  } else if (candidateCount === 1) {
    secondary = local.secondary_one;
  } else if (candidateCount > 1) {
    secondary = fill(local.secondary_many, { count: String(candidateCount) });
  }
  return { primary, secondary };
}

/** The anchor's name, read back out of the reason the pipeline just wrote —
 *  so card B's second register names the same anchor its own card does. */
function anchorOf(reason: string): string {
  for (const marker of [" junto con ", " con "]) {
    const at = reason.lastIndexOf(marker);
    if (at >= 0) {
      return reason
        .slice(at + marker.length)
        .split(" en esta sede")[0]!
        .trim();
    }
  }
  return "";
}

/** Card C's second line: a **stock** statement, never a history one. */
export function emptyBody(seededCount: number, bundle: Bundle | null): string {
  const empty = stringsOf(bundle).empty;
  if (!empty) return "";
  if (seededCount === 0) return empty.none;
  if (seededCount === 1) return empty.one;
  return fill(empty.many, { count: String(seededCount) });
}
