import type { StockRow, StockState, TransferRow } from "@/api/inventory";
import { DOT } from "@/ui/format";
import type { Meaning } from "@/ui/status";

/**
 * The interface strings for S3's states, in one place.
 *
 * Every identifier is English and every interface string is Spanish (§3), and
 * the two never mix. A component renders `stateBadge(row)` and never a literal.
 */

/**
 * §B.7.4 · the seven stock states, their families and their dots.
 *
 * **The two expiry states are one visual family with two tints**, and the ring
 * stays while the tint escalates: warning with a hollow dot in the notice
 * window, critical with a hollow dot in the alert window, solid critical once
 * the lot is `expired`. Where the edges fall is not this file's: they are S3's
 * `expiry_alert_days` and `expiry_notice_days`, and the month count is computed
 * from the lot's own `expires_at` at render time.
 *
 * **A horizon written into the string is how a badge ends up announcing a
 * window a tenant moved months ago**, and an expiry badge that states the wrong
 * horizon is worse than no badge, because a pharmacist acts on it.
 */
export const STOCK_STATE: Record<StockState, Meaning> = {
  expired: { family: "critical", dot: "solid", label: "Vencido" },
  stockout: { family: "critical", dot: "solid", label: "Quiebre" },
  expiring_urgent: { family: "critical", dot: "hollow", label: "Vence" },
  expiring: { family: "warning", dot: "hollow", label: "Vence" },
  reorder_point: { family: "warning", dot: "solid", label: "Punto de reorden" },
  overstock: { family: "info", dot: "solid", label: "Sobrestock" },
  sufficient: { family: "positive", dot: "solid", label: "Suficiente" },
};

/** The order the `Estado` column sorts in, and the order the chip lists. */
export const STATE_ORDER: StockState[] = [
  "expired",
  "stockout",
  "expiring_urgent",
  "expiring",
  "reorder_point",
  "overstock",
  "sufficient",
];

/** What the chip's own options read. The badge labels carry a clause the chip
 *  cannot, so the two are not the same string. */
export const STATE_FILTER_LABEL: Record<StockState, string> = {
  expired: "Vencido",
  stockout: "Quiebre",
  expiring_urgent: "Vence pronto",
  expiring: "Vence",
  reorder_point: "Punto de reorden",
  overstock: "Sobrestock",
  sufficient: "Suficiente",
};

/** Whole months between today and a date, floored, and never negative. */
export function monthsUntil(date: string, now = new Date()): number {
  const [year, month, day] = date.split("-").map(Number);
  const target = new Date(year!, (month ?? 1) - 1, day ?? 1);
  let months =
    (target.getFullYear() - now.getFullYear()) * 12 +
    (target.getMonth() - now.getMonth());
  if (target.getDate() < now.getDate()) months -= 1;
  return Math.max(0, months);
}

/**
 * The badge one row renders, label and all.
 *
 * **`Sobrestock` carries no day figure at S3**, and must not: the cover clause
 * needs `demand_forecasts.coverage_days`, which is S6's, and §B.9.2 tier 3 is
 * why a fabricated day count is worse than none. `Sobrestock · 0 días` would be
 * the most expensive kind of lie this screen can tell.
 */
export function stateBadge(row: StockRow): Meaning {
  const meaning = STOCK_STATE[row.state];
  if (row.state === "expiring" || row.state === "expiring_urgent") {
    const months = row.expires_at ? monthsUntil(row.expires_at) : null;
    return {
      ...meaning,
      label:
        months === null
          ? meaning.label
          : months === 0
            ? "Vence este mes"
            : months === 1
              ? "Vence en 1 mes"
              : `Vence en ${months} meses`,
    };
  }
  return meaning;
}

/**
 * The `Quiebre · hay 96 en Suba` clause, and it is a §B.9.2 tier-2 figure: it
 * is another sede's stock, so it renders **with the staleness marker** wherever
 * a till reads it. In the office it is a server-authoritative read of the same
 * query the badge came from, so no marker applies.
 */
export function stockoutClause(row: StockRow): string | null {
  if (row.state !== "stockout" || !row.elsewhere) return null;
  return `${DOT} hay ${row.elsewhere.quantity} en ${row.elsewhere.location_name}`;
}

/**
 * §B.7.4 · `transfers`, whose grammar the design system already fixes.
 */
export const TRANSFER_STATUS: Record<TransferRow["status"], Meaning> = {
  draft: { family: "neutral", dot: "hollow", label: "Borrador" },
  dispatched: { family: "info", dot: "solid", label: "Despachado" },
  received: { family: "positive", dot: "solid", label: "Recibido" },
  partial: { family: "warning", dot: "solid", label: "Recibido parcial" },
};

export const COUNT_STATUS: Record<string, Meaning> = {
  draft: { family: "neutral", dot: "hollow", label: "Borrador" },
  counting: { family: "info", dot: "solid", label: "En conteo" },
  closed: { family: "positive", dot: "solid", label: "Cerrado" },
};

/**
 * The reason vocabulary, rendered. The first four are what the direct-movement
 * dialog offers; the rest are written by a document and only ever read.
 */
export const MOVE_REASON: Record<string, string> = {
  opening_stock: "Inventario inicial",
  standalone_receipt: "Entrada directa",
  correction: "Corrección",
  damage: "Daño",
  theft: "Robo",
  loss: "Pérdida",
  expired: "Vencido",
  count_adjustment: "Ajuste por conteo",
  negative_resolution: "Resolución de negativo",
};

/** What the dialog offers per type. A `Merma` is not a `Vencimiento`, and the
 *  reason list is what tells them apart in a report six months later. */
export const REASONS_BY_TYPE: Record<string, string[]> = {
  adjustment: ["correction"],
  shrinkage: ["damage", "theft", "loss"],
  expiry: ["expired"],
};

export const MOVE_TYPE: Record<string, string> = {
  receipt: "Recepción",
  sale: "Venta",
  customer_return: "Devolución de cliente",
  supplier_return: "Devolución a proveedor",
  transfer_out: "Traslado · salida",
  transfer_in: "Traslado · entrada",
  adjustment: "Ajuste",
  shrinkage: "Merma",
  expiry: "Vencimiento",
  count: "Conteo",
};

/** The three types a person writes by hand, in the order the dialog lists. */
export const DIRECT_TYPES = ["adjustment", "shrinkage", "expiry"] as const;

/**
 * The `Vencimiento` chip. Each option maps to a horizon the tenant configured
 * rather than to a hand-typed number, so a pilot that moves a window moves the
 * chip with it -- and the labels below are written from the defaults the
 * settings group ships.
 */
export const EXPIRY_FILTERS = [
  { value: "expired", label: "Vencidos" },
  { value: "valuation", label: "Vence en 90 días" },
  { value: "alert", label: "Vence en 6 meses" },
  { value: "notice", label: "Vence en 12 meses" },
  { value: "none", label: "Sin fecha" },
] as const;
