import type { TriggerClause } from "@/sync/registry";
import type { Fact } from "./extract";

/**
 * The safety layer, evaluated on the device — **the same three outcomes the
 * server applies, because it is the same rule** (S8, *3 · Filter*).
 *
 * A trigger's clauses are ORed and the trigger's outcome is the worst of them:
 *
 *   `satisfied`   some clause is decided **true** by the extracted set
 *   `irrelevant`  every clause is decided **false** by it
 *   `unresolved`  neither — the extracted set **cannot decide it**
 *
 * What each outcome means for a card is asymmetric by warning type, and that
 * asymmetry is the whole of `Con condición`:
 *
 *   `do_not_suggest_if`  asks *has anyone ruled this out?* An unresolved
 *                        trigger is a question nobody put to the customer, so
 *                        the card carries the warning's own text.
 *   the other two        ask *does this apply to the person in front of you?*
 *                        Only a satisfied trigger reaches a card — as a filter
 *                        where it is `blocking`, as a caution where it is
 *                        `advisory`.
 *
 * *What breaks if every unresolved trigger of every type became a card* is a
 * counter screen carrying four caution lines on every query, a cashier who
 * stops reading them, and the one that mattered being the one nobody read.
 */

export const SATISFIED = "satisfied";
export const UNRESOLVED = "unresolved";
export const IRRELEVANT = "irrelevant";

export type Outcome = typeof SATISFIED | typeof UNRESOLVED | typeof IRRELEVANT;

const RANK: Record<Outcome, number> = {
  [SATISFIED]: 0,
  [UNRESOLVED]: 1,
  [IRRELEVANT]: 2,
};

export interface Extraction {
  symptoms: Map<string, Fact>;
  denied: Set<string>;
  populations: Set<string>;
  treatments: Set<string>;
  duration: number | null;
  /** The ages that decide another age false. An adult is not a child; an adult
   *  may perfectly well be diabetic, which is why the chronic states are not
   *  in this set. */
  agePopulations: Set<string>;
}

export function readExtraction(
  facts: Fact[],
  agePopulations: string[],
): Extraction {
  const held: Extraction = {
    symptoms: new Map(),
    denied: new Set(),
    populations: new Set(),
    treatments: new Set(),
    duration: null,
    agePopulations: new Set(agePopulations),
  };
  for (const fact of facts) {
    if (!fact.key) continue;
    if (fact.kind === "symptom") {
      if (fact.negated) held.denied.add(fact.key);
      else held.symptoms.set(fact.key, fact);
    } else if (fact.kind === "population" && !fact.negated) {
      held.populations.add(fact.key);
    } else if (fact.kind === "active_treatment" && !fact.negated) {
      held.treatments.add(fact.key);
    } else if (fact.kind === "duration") {
      held.duration = fact.value ?? null;
    }
  }
  return held;
}

export function evaluate(
  triggers: TriggerClause[] | null | undefined,
  extraction: Extraction,
): Outcome {
  const clauses = triggers ?? [];
  if (clauses.length === 0) return IRRELEVANT;
  let worst: Outcome = IRRELEVANT;
  for (const clause of clauses) {
    const outcome = one(clause, extraction);
    if (RANK[outcome] < RANK[worst]) worst = outcome;
  }
  return worst;
}

function one(clause: TriggerClause, extraction: Extraction): Outcome {
  if (clause.symptom !== undefined) return symptom(clause, extraction);
  if (clause.population !== undefined) return population(clause, extraction);
  if (clause.interacts_with_ingredient !== undefined) {
    if (extraction.treatments.has(clause.interacts_with_ingredient)) {
      return SATISFIED;
    }
    // **Naming one treatment decides the others false.** *"toma losartán"* is
    // an answer to *"¿está tomando algo?"*.
    return extraction.treatments.size > 0 ? IRRELEVANT : UNRESOLVED;
  }
  if (clause.duration_days !== undefined) {
    if (extraction.duration === null) return UNRESOLVED;
    return compare(
      clause.duration_days.operator,
      extraction.duration,
      clause.duration_days.value,
    );
  }
  return IRRELEVANT;
}

function symptom(clause: TriggerClause, extraction: Extraction): Outcome {
  const key = clause.symptom!;
  // The customer said it is not so. This is the one thing that makes a clause
  // irrelevant on the symptom side, and it is why the lexicon handles negation.
  if (extraction.denied.has(key)) return IRRELEVANT;
  const fact = extraction.symptoms.get(key);
  if (clause.operator === undefined) return fact ? SATISFIED : UNRESOLVED;
  if (!fact) return UNRESOLVED;
  // *fiebre* is stated and no temperature is — the handoff's own Loperamida
  // card, and the whole of `conditional`.
  if (fact.value === undefined || fact.value === null) return UNRESOLVED;
  return compare(clause.operator, fact.value, clause.value ?? 0);
}

function population(clause: TriggerClause, extraction: Extraction): Outcome {
  const key = clause.population!;
  if (extraction.populations.has(key)) return SATISFIED;
  if (extraction.agePopulations.has(key)) {
    for (const held of extraction.populations) {
      if (extraction.agePopulations.has(held)) return IRRELEVANT;
    }
  }
  return UNRESOLVED;
}

function compare(
  operator: string,
  measured: number,
  threshold: number,
): Outcome {
  switch (operator) {
    case ">":
      return measured > threshold ? SATISFIED : IRRELEVANT;
    case ">=":
      return measured >= threshold ? SATISFIED : IRRELEVANT;
    case "<":
      return measured < threshold ? SATISFIED : IRRELEVANT;
    case "<=":
      return measured <= threshold ? SATISFIED : IRRELEVANT;
    case "==":
      return measured === threshold ? SATISFIED : IRRELEVANT;
    default:
      return IRRELEVANT;
  }
}

/** Whether one evaluated warning puts the card in **Con condición**. Stated
 *  once, because three copies of it would be three chances to disagree about a
 *  safety string. */
export function makesConditional(
  type: string | null | undefined,
  severity: string | null | undefined,
  outcome: Outcome,
): boolean {
  if (outcome === IRRELEVANT) return false;
  if (type === "do_not_suggest_if") return true;
  return outcome === SATISFIED && severity === "advisory";
}
