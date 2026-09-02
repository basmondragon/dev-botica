import type { InvimaStatus, ItemType, VatClass } from "@/api/catalog";

/**
 * The interface strings for S1's four enums, in one place.
 *
 * Every identifier in this system is English and every interface string is
 * Spanish (§3), and the two never mix. This module is the single seam between
 * them on the client: a component renders `VAT_CLASS[item.vat_class]` and never
 * a literal, so a label changes in one file.
 */

export const ITEM_TYPE: Record<ItemType, string> = {
  product: "Producto",
  service: "Servicio",
};

/**
 * Four values, not three rates plus a spare. `excluded` is not a taxable
 * operation at all; `exempt` is taxable at 0% and carries a right to credit.
 * The distinction matters to an accountant and to the documento equivalente.
 */
export const VAT_CLASS: Record<VatClass, string> = {
  excluded: "Excluido de IVA",
  exempt: "Exento de IVA",
  rate_5: "IVA 5%",
  rate_19: "IVA 19%",
};

export const INVIMA_LABEL: Record<InvimaStatus, string> = {
  valid: "Registro vigente",
  in_process: "En trámite",
  expired: "Registro vencido",
  not_applicable: "No aplica",
};

/**
 * The domestic vocabulary a Colombian counter actually uses. S5's per-target
 * mapping translates these for whatever system the client invoices with, so a
 * system that spells them differently is a mapping and never a migration.
 */
export const DOCUMENT_TYPES = [
  { value: "CC", label: "CC · Cédula de ciudadanía" },
  { value: "CE", label: "CE · Cédula de extranjería" },
  { value: "NIT", label: "NIT" },
  { value: "TI", label: "TI · Tarjeta de identidad" },
  { value: "PA", label: "PA · Pasaporte" },
  { value: "PEP", label: "PEP · Permiso especial de permanencia" },
  { value: "PPT", label: "PPT · Permiso por protección temporal" },
] as const;

/** The base units a droguería actually counts in. The field stays free text --
 *  a network that sells by `ampolla` should not need a migration. */
export const UNITS = [
  "caja",
  "tableta",
  "cápsula",
  "sobre",
  "frasco",
  "ampolla",
  "unidad",
  "bolsa",
  "botella",
  "inhalador",
  "servicio",
  "sesión",
  "domicilio",
];

export const enumOptions = <T extends string>(map: Record<T, string>) =>
  (Object.keys(map) as T[]).map((value) => ({ value, label: map[value] }));
