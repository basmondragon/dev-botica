import type { Family, Meaning } from "@/ui/status";
import type {
  ConfidenceBand,
  ProposalBasis,
  RowStateFilter,
} from "@/api/pricing";

/**
 * §B.7.4 · the `Estado` column, and it is the one badge column this table is
 * permitted (§B.7.3).
 *
 * The enum lost three values to A11 and gained three; **the two the design
 * system already fixed keep their family and their label**, and the three that
 * arrived take the family the value they replaced carried rather than inventing
 * a sixth reading (§B.7.1). Two more are **derived** row states in the sense
 * §B.7.4 gives the stock state -- they are not `price_proposal_status` values
 * and never reach the database.
 */
export const ROW_STATE: Record<RowStateFilter, Meaning> = {
  //: The screen's own default, `Estado · Con propuesta` (§B.8.4·1). It admits
  //: the compliance finding as well as the ordinary suggestion, because an
  //: `above_cap` row is the one a person most needs to see and hiding it behind
  //: a filter is the opposite of what the tile beside it is for.
  live: { family: "neutral", dot: "hollow", label: "Con propuesta" },
  //: Nothing could evaluate this: no cost basis, or inactive. The panel names
  //: which. **It must not be conflated with `no_proposal`** -- *we could not
  //: look* and *we looked and there is nothing to do* send a reader to two
  //: different places, and that distinction is what this whole screen is about.
  unevaluated: { family: "neutral", dot: "hollow", label: "Sin evaluar" },
  no_proposal: { family: "neutral", dot: "hollow", label: "Sin propuesta" },
  proposed: { family: "neutral", dot: "hollow", label: "Propuesta" },
  above_cap: {
    family: "critical",
    dot: "solid",
    label: "Sobre el tope regulado",
  },
  //: The good terminal state, and the family §B.7.4 gave `approved`.
  taken: { family: "positive", dot: "solid", label: "Tomada" },
  //: **Positive, not warning.** A price changed and the suggestion informed it,
  //: which is the outcome this stage exists to produce; the number differing is
  //: the measurement rather than a fault.
  modified: { family: "positive", dot: "solid", label: "Modificada" },
  //: §B.7.4's `rejected` family, re-used for the value that replaced it, for
  //: the reason it gives `purchase_orders.discarded`: declining a model's
  //: suggestion is an owner using their judgement, and colouring it as a
  //: failure teaches them not to.
  dismissed: { family: "neutral", dot: "solid", label: "Descartada" },
  superseded: { family: "neutral", dot: "solid", label: "Reemplazada" },
};

export const ROW_STATE_ORDER: RowStateFilter[] = [
  "live",
  "proposed",
  "above_cap",
  "taken",
  "modified",
  "dismissed",
  "no_proposal",
  "unevaluated",
];

/** The `Base` chip's two values -- the one control that answers *how much of
 *  this screen is measured*. */
export const BASIS_LABEL: Record<ProposalBasis, string> = {
  margin_rule: "Margen",
  elasticity: "Elasticidad",
};

export const CONFIDENCE_LABEL: Record<ConfidenceBand, string> = {
  high: "Alta",
  medium: "Media",
  low: "Baja",
};

/**
 * The second line under the `Estado` badge: the engine, and where it was
 * measured, the confidence band.
 *
 * **The band is omitted on a margin row** because it is `low` by construction,
 * and repeating it in every one of two hundred rows teaches an owner to stop
 * reading it. Two words, because the column is ten units wide and a truncated
 * basis is worse than no basis; the full sentence is one row-click away.
 */
export function basisLine(
  basis: ProposalBasis | null,
  confidence: ConfidenceBand | null,
): string {
  if (!basis) return "";
  if (basis === "margin_rule") return BASIS_LABEL.margin_rule;
  return `Elasticidad · ${(CONFIDENCE_LABEL[confidence ?? "low"] ?? "").toLowerCase()}`;
}

export const CAP_STATUS_LABEL = {
  capped: "Con tope regulado",
  not_regulated: "Sin regulación de precio",
  unknown: "Tope desconocido",
} as const;

/** The family the `Propuestas sobre el tope` tile takes once the count is above
 *  zero. A compliance finding is the one thing on this surface that is a
 *  problem rather than an opportunity. */
export function tileFamily(aboveCap: number): Family {
  return aboveCap > 0 ? "critical" : "neutral";
}
