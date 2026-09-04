import type {
  ConfidenceBand,
  ForecastBasis,
  PurchaseOrderLineRow,
  PurchaseOrderStatus,
} from "@/api/purchasing";
import { dayMonth, time } from "@/ui/format";
import type { Family, Meaning } from "@/ui/status";

/**
 * The Spanish this module renders, in one place.
 *
 * design-system §B.7.4 owns the family and the dot each `purchase_orders.status`
 * renders in and reproduces the six labels verbatim; this file is where they
 * live for the two tables that show them.
 */
export const ORDER_STATUS: Record<PurchaseOrderStatus, Meaning> = {
  suggested: { label: "Sugerida", family: "neutral", dot: "hollow" },
  approved: { label: "Aprobada", family: "info", dot: "solid" },
  sent: { label: "Enviada al proveedor", family: "info", dot: "solid" },
  partially_received: {
    label: "Recibida parcial",
    family: "warning",
    dot: "solid",
  },
  received: { label: "Recibida", family: "positive", dot: "solid" },
  // **Neutral, not critical.** Discarding a suggestion is the product working
  // -- it is the measurement `suggested_quantity` versus `approved_quantity`
  // exists to capture -- and colouring it as a failure would tell an
  // administrator that using their judgement is an error (§B.7.4).
  discarded: { label: "Descartada", family: "neutral", dot: "solid" },
};

export const ORDER_STATUS_ORDER: PurchaseOrderStatus[] = [
  "suggested",
  "approved",
  "sent",
  "partially_received",
  "received",
  "discarded",
];

/** §1 · the three regimes, in the words the `Confianza del modelo` menu uses. */
export const BASIS_LABEL: Record<ForecastBasis, string> = {
  parametric: "Paramétrica",
  learning: "Aprendiendo",
  learned: "Aprendida",
};

export const BASIS_ORDER: ForecastBasis[] = [
  "parametric",
  "learning",
  "learned",
];

export const BAND_LABEL: Record<ConfidenceBand, string> = {
  alta: "Alta",
  media: "Media",
  baja: "Baja",
};

export const BAND_ORDER: ConfidenceBand[] = ["alta", "media", "baja"];

/**
 * §B.7.4 · the four `Cobertura` bands, and the numeral takes the family's
 * colour with no dot. **The `Por qué` cell always restates the reading in
 * words**, so the colour is never the only signal (§B.12.3).
 */
export function coverageFamily(days: number | null): Family {
  if (days === null) return "neutral";
  if (days <= 4) return "critical";
  if (days <= 20) return "warning";
  if (days <= 90) return "neutral";
  return "info";
}

export const COVERAGE_INK: Record<Family, string> = {
  critical: "text-critical",
  warning: "text-warning",
  // The `normal` band is the ordinary secondary ink the rest of the row takes,
  // not a colour of its own: three of the four bands say something and the
  // fourth says nothing, which is the point.
  neutral: "text-ink-body",
  info: "text-info",
  positive: "text-positive",
};

/**
 * §B.9.2 tier 3 · why a `Cobertura` cell is an em dash rather than a figure.
 *
 * Two readings, and they are **not** the same thing. A null `weekly_sales` is a
 * row with no demand estimate at all -- the parametric regime -- and reads `sin
 * histórico`. A zero one is a row the model measured and found nothing in, and
 * reads `sin ventas en el periodo`. Never a `0`, never an `∞`.
 */
export function coverageReading(line: PurchaseOrderLineRow): string {
  if (line.weekly_sales === null || line.weekly_sales === undefined)
    return "sin histórico";
  return "sin ventas en el periodo";
}

/**
 * What the `Por qué` cell renders: the model's prose where it wrote any, and
 * the line's own deterministic string where it did not.
 *
 * **The order is fully usable either way** (§10). Quantities are arithmetic and
 * never wait on a model, so a gateway that is unreachable costs this cell a
 * sentence and nothing else.
 */
export function reasonText(line: PurchaseOrderLineRow): string {
  // **A manual line has no code and never will**: nobody proposed it, so there
  // is nothing for the arithmetic to explain. The cell says who wrote it rather
  // than standing empty -- `Por qué` is the one column on this screen that must
  // never be blank.
  if (!line.basis) return "Escrita a mano";
  return line.reason || line.reason_fallback;
}

/**
 * §B.7.3 · the band as an incidental prefix -- dot and label at `t-12`, no
 * fill, no pill -- **and only where the band is not `Alta`**. Marking the rows
 * that are fine is how a marker stops meaning anything (§B.9.2).
 */
export function bandPrefix(line: PurchaseOrderLineRow): string | null {
  if (!line.band || line.band === "alta") return null;
  return BAND_LABEL[line.band];
}

export const BAND_FAMILY: Record<ConfidenceBand, Family> = {
  alta: "positive",
  media: "warning",
  baja: "critical",
};

/**
 * The `Confianza del modelo` menu's first entry, and the affordance the chip
 * exists for: it selects `Paramétrica` and `Baja` together and leaves the
 * administrator looking at exactly the lines the model knows least about.
 */
export const REVIEW_FIRST = {
  basis: ["parametric"] as ForecastBasis[],
  band: ["baja"] as ConfidenceBand[],
};

/**
 * `hoy 06:00`, or the absolute stamp once it is not today.
 *
 * **Not §B.9.1's relative ladder**, and the departure is deliberate: the ladder
 * marks how stale a figure a till read from its local store is, and this is not
 * that. It is the hour a nightly job ran, which an administrator reads once at
 * the top of the morning -- `actualizado hace 3 h` makes them do the
 * subtraction the drawn `actualizado hoy 06:00` already did.
 */
export function refreshedAt(value: string | null | undefined): string {
  if (!value) return "todavía sin correr";
  const at = new Date(value);
  const now = new Date();
  const sameDay =
    at.getFullYear() === now.getFullYear() &&
    at.getMonth() === now.getMonth() &&
    at.getDate() === now.getDate();
  return sameDay ? `hoy ${time(at)}` : `al ${dayMonth(at)} ${time(at)}`;
}

/**
 * §B.8.5 · the filter bar's right slot, in three forms chosen by the regime the
 * majority of the order's lines were generated in (*UI*).
 *
 * **The figure is the window the refresh actually read**, never a string from a
 * drawing: a screen telling a prospect the model trained on eighteen months of
 * their sales, on a tenant that holds six, is the cheapest possible way to lose
 * a pilot and the hardest to recover from.
 */
export function provenanceLine(
  basis: ForecastBasis,
  window: string,
  updated: string,
  modelProse: boolean,
): string {
  const head =
    basis === "learned"
      ? `Modelo entrenado con ${window} de venta`
      : basis === "learning"
        ? `Aprendiendo de ${window} de venta propia`
        : "Sin histórico cargado · sugerido por parámetros de la sede";
  const line = `${head} · actualizado ${updated}`;
  return modelProse ? line : `${line} · sin redacción del modelo`;
}
