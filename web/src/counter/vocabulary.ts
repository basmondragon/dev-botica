import type { Family } from "@/ui/status";

/**
 * The counter's Spanish, in one module.
 *
 * §B.7.4 is the only definition of the enum map, and this transcribes the rows
 * it fixes for this stage rather than inventing a second vocabulary beside it.
 * Strings the handoff draws are reproduced verbatim.
 */

export interface Meaning {
  label: string;
  family: Family;
  dot?: "solid" | "hollow";
}

/** §B.7.4 · `Abierta` informative · `Cerrada` positive · `Anulada` neutral. */
export const SALE_STATUS: Record<string, Meaning> = {
  open: { label: "Abierta", family: "info" },
  closed: { label: "Cerrada", family: "positive" },
  voided: { label: "Anulada", family: "neutral" },
};

export const SHIFT_STATUS: Record<string, Meaning> = {
  open: { label: "Abierto", family: "info" },
  closed: { label: "Cerrado", family: "neutral" },
};

/** The five methods, in the order the payment dialog renders them. `Efectivo`
 *  is first because it is preselected and because it is half the takings. */
export const PAYMENT_METHODS: { value: string; label: string }[] = [
  { value: "cash", label: "Efectivo" },
  { value: "debit_card", label: "Débito" },
  { value: "credit_card", label: "Crédito" },
  { value: "transfer", label: "Transferencia" },
  { value: "other", label: "Otro" },
];

export const PAYMENT_METHOD: Record<string, string> = Object.fromEntries(
  PAYMENT_METHODS.map((one) => [one.value, one.label]),
);

/** The three methods that carry a voucher or transfer reference. */
export const REFERENCED_METHODS = new Set([
  "debit_card",
  "credit_card",
  "transfer",
]);

export const SALE_SOURCE: Record<string, string> = {
  counter: "Mostrador",
  imported: "Historial cargado",
};

export const DOCUMENT_TYPES: { value: string; label: string }[] = [
  { value: "CC", label: "Cédula de ciudadanía" },
  { value: "CE", label: "Cédula de extranjería" },
  { value: "NIT", label: "NIT" },
  { value: "TI", label: "Tarjeta de identidad" },
  { value: "PA", label: "Pasaporte" },
  { value: "PEP", label: "PEP" },
  { value: "PPT", label: "PPT" },
];

/**
 * The two flags a ticket line can carry, and **they are not the same kind of
 * thing** (§7, §12, §B.7.4).
 *
 * `Requiere receta` is a control on how a sale is made: the line is added like
 * any other, and `Cobrar` asks for one acknowledgement that the cashier has
 * seen the prescription. `Registro vencido` is a state of a product the
 * pharmacy has already decided to keep selling: it is shown and nothing else is
 * asked. Botica surfaces the state and records the decision; it does not decide
 * the consequence and does not validate against INVIMA's register.
 */
export const LINE_FLAGS = {
  prescription: { label: "Requiere receta", family: "warning" as Family },
  expiredRegistration: {
    label: "Registro vencido",
    family: "critical" as Family,
  },
};

/**
 * §B.7.4 · the stock state beside a search result, as a **dot and a label**
 * rather than the tinted badge: §B.7.3 allows one badge column per surface, and
 * on the till that column is the ticket's own line flags.
 */
export const STOCK_STATE: Record<string, Meaning> = {
  stockout: { label: "Quiebre", family: "critical" },
  reorder_point: { label: "Punto de reorden", family: "warning" },
  sufficient: { label: "Suficiente", family: "positive" },
};

export function stockState(quantity: number, reorderPoint: number | null) {
  if (quantity <= 0) return STOCK_STATE.stockout!;
  if (reorderPoint !== null && quantity <= reorderPoint)
    return STOCK_STATE.reorder_point!;
  return STOCK_STATE.sufficient!;
}
