import type { Family } from "@/ui/status";
import type { SuggestionKind } from "./pipeline";

/**
 * What the assistant column says, in the words it says it.
 *
 * **The advisory notice is deliberately not here.** It lives inside the
 * suggestions component itself and comes from no prop, no setting, no role and
 * no payload (A8) — a sentence in a vocabulary module is a sentence somebody
 * can import somewhere else and render conditionally, which is the beginning of
 * a notice that can be removed.
 */

export const EYEBROW_TRANSCRIPT = "Qué dice el cliente";
export const EYEBROW_SUGGESTIONS = "Sugerencias sobre existencias de la sede";
export const EYEBROW_LOCAL = "Modo local";

export const PLACEHOLDER = "Escriba o dicte lo que dice el cliente";

/** §B.10.1 · after 2,5 s, and it is a line under the skeleton rather than a
 *  spinner: the card already has a local recommendation to fall back to. */
export const SLOW = "El asistente está tardando más de lo normal.";
export const SLOW_AFTER_MS = 2500;

export const ADD = "Agregar";
export const ADDED = "Agregado";

export const CONFIGURE_TITLE =
  "El asistente todavía no conoce el catálogo de esta droguería";
export const CONFIGURE_BODY =
  "Un administrador debe asociar los síntomas a las categorías del catálogo.";
export const CONFIGURE_ACTION = "Configurar el asistente";

/** §A.16 · the three type pills, and the tint is the only thing that separates
 *  them — which is why `TypePill` carries no dot. */
export const TYPE_LABELS: Record<SuggestionKind, string> = {
  first_choice: "Primera opción",
  conditional: "Con condición",
  bought_together: "Se lleva junto",
};

export const TYPE_FAMILY: Record<SuggestionKind, Family> = {
  first_choice: "positive",
  conditional: "warning",
  bought_together: "info",
};

/** The header counter, `3 de 12 referencias`: cards shown of candidates that
 *  survived the filter. */
export function counterLabel(shown: number, surviving: number): string {
  return `${shown} de ${surviving} ${surviving === 1 ? "referencia" : "referencias"}`;
}

/** The context line is always **units at this sede** then **the reason**,
 *  because the units are the fact the cashier verifies by looking at the shelf
 *  and the reason is the sentence they repeat out loud. */
export function unitsLabel(quantity: number, locationName: string): string {
  return `${quantity} ${quantity === 1 ? "unidad" : "unidades"} en ${locationName}`;
}

// ---------------------------------------------------------------------------
// The office's own vocabulary
//
// **Every status label in the product is declared in exactly one
// `vocabulary.ts`**, which is what the conformance test in `purchasing`
// asserts across the whole tree: a label written twice is a label that drifts,
// and `Baja` meaning two different things on two screens is how the wrong
// number ends up under the wrong word.
// ---------------------------------------------------------------------------

/** `confidence_band` — **how much the miner knew**, banded. It is not
 *  `cross_sell_rules.confidence`, which is P(B | A) and renders under
 *  `% del ancla`. */
export const BAND_LABEL: Record<"low" | "medium" | "high", string> = {
  low: "Baja",
  medium: "Media",
  high: "Alta",
};

export const BAND_MEANING: Record<
  "low" | "medium" | "high",
  { family: "neutral" | "positive"; dot: "solid" | "hollow" }
> = {
  low: { family: "neutral", dot: "hollow" },
  medium: { family: "neutral", dot: "solid" },
  high: { family: "positive", dot: "solid" },
};

/** Which sale population a mining run consumed. */
export const BASIS_LABEL: Record<"counter" | "imported" | "mixed", string> = {
  counter: "Venta propia",
  imported: "Historial importado",
  mixed: "Ambas",
};

export const WARNING_TYPE_LABEL: Record<string, string> = {
  interaction: "Interacción",
  contraindication: "Contraindicación",
  do_not_suggest_if: "No ofrecer si",
};

/** §B.7.1 · `blocking` is what the filter reads; `advisory` is what a card
 *  carries as its reason. There is no third value and no numeric scale. */
export const SEVERITY_MEANING: Record<
  string,
  { label: string; family: "critical" | "warning" }
> = {
  blocking: { label: "Bloqueante", family: "critical" },
  advisory: { label: "Informativa", family: "warning" },
};

export const WARNING_SOURCE_LABEL: Record<string, string> = {
  catalog: "Catálogo",
  manual: "Ajustes",
};

/** A warning whose trigger keys have not appeared in an extraction in thirty
 *  days. **This is the reading that keeps the closed vocabulary honest.** */
export const NEVER_MATCHED = "nunca se ha activado";

/** §B.7.4 · **`Descartada` is neutral rather than critical.** The output check
 *  discarding an answer is the product working. */
export const OUTPUT_CHECK: Record<
  "passed" | "rejected",
  { label: string; family: "positive" | "neutral"; dot: "solid" | "hollow" }
> = {
  passed: { label: "Pasó", family: "positive", dot: "solid" },
  rejected: { label: "Descartada", family: "neutral", dot: "hollow" },
};

export const MODE_LABEL: Record<"model" | "local", string> = {
  model: "Con modelo",
  local: "Modo local",
};
