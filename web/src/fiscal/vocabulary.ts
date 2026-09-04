import type { Family } from "@/ui/status";

/**
 * The handoff's Spanish, in one module.
 *
 * **The split, recorded here so it is not re-litigated: the design system owns
 * the status family and the dot treatment (§B.7.1, §B.7.2, §B.7.4); this stage
 * owns the label text.** The families and dots below are §B.7's, transcribed so
 * this surface can be built from one page rather than restated as a competing
 * rule — a wording change is an edit here, a treatment change is an edit there,
 * and neither may make the other's.
 *
 * **These four strings are canonical and are rendered verbatim wherever the
 * value appears**: this work list, the record panel, the settings read-out and
 * any surface a later stage adds. The bundle grep in S5's *Verification* checks
 * each appears exactly once as a literal — more than once means a second
 * surface renders a status this document owns and the two will diverge; zero
 * means a label was translated or paraphrased at the point of use.
 *
 * **And no label may claim a DIAN outcome.** `Confirmado` says the client's
 * invoicing system holds the document and stops there, because Botica never
 * learns what the DIAN did with it (§8, A9). A string reading
 * `Aceptado por la DIAN` on a row we handed to an API is a claim about a filing
 * this product did not perform and cannot see.
 */

export interface Meaning {
  label: string;
  family: Family;
  dot: "solid" | "hollow";
}

export const FISCAL_STATUS: Record<string, Meaning> = {
  //: Built, queued, nothing has failed. Hollow because the handoff has not
  //: happened yet (§B.7.2).
  pending: { label: "Pendiente de envío", family: "neutral", dot: "hollow" },
  //: Delivered; the target has not confirmed yet. Hollow because we are waiting
  //: on something outside this system.
  sent: { label: "Enviado", family: "info", dot: "hollow" },
  //: The target holds it. Terminal success, and a statement about the target
  //: and not about the DIAN.
  acknowledged: { label: "Confirmado", family: "positive", dot: "solid" },
  //: An administrator's work list, never a cashier's.
  failed: { label: "Falló el envío", family: "critical", dot: "solid" },
};

/** The route's own name, and it has no nav item (§B.8.1 caps the sidebar at
 *  seven and Botica is at it). It is reached from the Panel's strip, from the
 *  settings section, and from the link in the digest email. */
export const WORK_LIST = "Envíos a facturación";

/**
 * The three lists the segmented control holds, and the values the URL carries.
 *
 * Spanish in the address bar, like every other search param in the product
 * (`traslado`, `conteo`, `venta`, `sede`) -- and named `lista` rather than
 * `segment` because S4's `/counter` already carries a `segment` of its own, and
 * one typed search union holding two vocabularies under one name is a union
 * neither route can read.
 */
export const SEGMENTS = [
  { value: "pendientes" as const, label: "Pendientes" },
  { value: "fallidos" as const, label: "Fallidos" },
  { value: "sin-enviar" as const, label: "Ventas sin enviar" },
];

export type Segment = (typeof SEGMENTS)[number]["value"];

/** `sale` and `credit_note`, as a person reads them on a row. */
export const DOCUMENT_TYPE: Record<string, string> = {
  sale: "Venta",
  credit_note: "Nota crédito",
};

/** The two environments a target runs in. `test` is the default, because a
 *  pilot connects a sandbox before it connects the system that files. */
export const ENVIRONMENTS = [
  { value: "test", label: "Pruebas" },
  { value: "production", label: "Producción" },
];

export const DELIVERY_MODES = [
  { value: "per_sale", label: "Una por venta" },
  { value: "batched", label: "Por lotes" },
];

export const FILE_FORMATS = [
  { value: "json", label: "JSON" },
  { value: "csv", label: "CSV" },
];
