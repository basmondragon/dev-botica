import type { RxJsonSchema } from "rxdb";

/**
 * The client half of the sync registry (ownership.md rule 9, A4).
 *
 * **The server's registry is the authority and this is its local shape.** What
 * a device may pull is decided by `core/sync/registry.py` and by nothing here;
 * these schemas say how the same documents are stored, indexed and queried once
 * they arrive. A collection the server does not serve cannot be filled by
 * adding one here, and a collection here that the server does not serve stays
 * permanently empty — which `GET /api/sync/registry` makes visible, because the
 * first-sync card counts against the server's own totals.
 */

/** Bumped with `REGISTRY_VERSION` on the server. A client behind the server
 *  enters `degraded · versión desactualizada` and reloads the shell. */
export const REGISTRY_VERSION = 1;

export const COLLECTIONS = [
  "items",
  "item_barcodes",
  "manufacturers",
  "categories",
  "item_prices",
  "customers",
] as const;

export type CollectionName = (typeof COLLECTIONS)[number];

/**
 * The collections whose predicate names a sede. When the device's
 * `location_id` changes — an office moved the till — these are wiped and
 * re-pulled from a zero cursor, and the tenant-wide ones are left alone.
 */
export const LOCATION_SCOPED: readonly CollectionName[] = ["item_prices"];

/** The one collection a device may write at S2 (registry, `Push` column). */
export const PUSHABLE: readonly CollectionName[] = ["customers"];

/**
 * §B.9.3 · the sync panel breaks the queue down by kind. At S2 that is
 * `Clientes 1`; S3 adds `Movimientos` and S4 `Ventas`, each by adding a line
 * here rather than a second queue.
 */
export const QUEUE_LABELS: Record<string, string> = {
  customers: "Clientes",
};

/** The Spanish name of each collection, for the first-sync card. */
export const COLLECTION_LABELS: Record<CollectionName, string> = {
  items: "Catálogo",
  item_barcodes: "Códigos de barras",
  manufacturers: "Laboratorios",
  categories: "Categorías",
  item_prices: "Precios",
  customers: "Clientes",
};

/** Every document carries these two, and the cursor and the digest are both
 *  computed over them. */
const HEAD = {
  id: { type: "string", maxLength: 36 },
  updated_at: { type: "string", maxLength: 32 },
} as const;

function schema<T>(
  properties: Record<string, unknown>,
  required: string[],
  indexes: string[][] = [],
): RxJsonSchema<T> {
  return {
    version: 0,
    primaryKey: "id",
    type: "object",
    properties: { ...HEAD, ...properties },
    required: ["id", "updated_at", ...required],
    indexes,
  } as unknown as RxJsonSchema<T>;
}

export interface ItemDoc {
  id: string;
  updated_at: string;
  name: string;
  search_name: string;
  type: string;
  presentation: string;
  active_ingredient: string;
  strength: string;
  unit: string;
  units_per_pack: number;
  splittable: boolean;
  vat_class: string;
  manufacturer_id: string | null;
  category_id: string | null;
  requires_prescription: boolean;
  controlled: boolean;
  cold_chain: boolean;
  tracks_stock: boolean;
  tracks_lots: boolean;
  tracks_expiry: boolean;
  invima_status: string;
}

export interface BarcodeDoc {
  id: string;
  updated_at: string;
  item_id: string;
  code: string;
  is_primary: boolean;
}

export interface NamedDoc {
  id: string;
  updated_at: string;
  name: string;
  search_name?: string;
  parent_id?: string | null;
}

export interface PriceDoc {
  id: string;
  updated_at: string;
  item_id: string;
  location_id: string | null;
  price: string;
  effective_from: string;
  effective_to: string | null;
}

export interface CustomerDoc {
  id: string;
  updated_at: string;
  document_type: string;
  document: string;
  name: string;
  phone: string;
  email: string;
  address: string;
  data_consent: boolean;
}

/**
 * A row a till wrote and the server has not confirmed.
 *
 * **Local-only, with no server counterpart.** It is never replicated, never
 * compacted while it has contents, and is the only durable record of a write
 * the server has not seen. Putting it on the server would make the server a
 * second record of what a till has done, which is the shape §5 rule 1 forbids:
 * the till's store is a server snapshot plus its own pending events, and the
 * pending events are the half the server does not have yet.
 */
export interface OutboxDoc {
  client_uuid: string;
  /** The registry collection this row is destined for. It is **`kind` and not
   *  `collection`** because `collection` is a getter on every `RxDocument` and
   *  a schema field of that name cannot be written — and it is also the word
   *  §B.9.3 uses for the panel's own breakdown. */
  kind: string;
  /** The device's own clock, sent exactly as stamped and never corrected. */
  occurred_at: string;
  payload: Record<string, unknown>;
  attempts: number;
  last_error: string;
  /** For the sync panel's `Clientes 1` and for compaction's age rule. */
  queued_at: string;
}

/**
 * `search_name` carries the index because the counter's catalog search is the
 * one path with a 30 ms p95 budget over four thousand items (§4), and it is the
 * accent-folded, lowercased column the database generates — so `losartan` finds
 * `Losartán 50 mg × 30` locally exactly as it does on the server.
 */
export const SCHEMAS = {
  items: schema<ItemDoc>(
    {
      name: { type: "string", maxLength: 200 },
      search_name: { type: "string", maxLength: 200 },
      type: { type: "string", maxLength: 16 },
      presentation: { type: "string" },
      active_ingredient: { type: "string" },
      strength: { type: "string" },
      unit: { type: "string" },
      units_per_pack: { type: "number" },
      splittable: { type: "boolean" },
      vat_class: { type: "string", maxLength: 16 },
      manufacturer_id: { type: ["string", "null"], maxLength: 36 },
      category_id: { type: ["string", "null"], maxLength: 36 },
      requires_prescription: { type: "boolean" },
      controlled: { type: "boolean" },
      cold_chain: { type: "boolean" },
      tracks_stock: { type: "boolean" },
      tracks_lots: { type: "boolean" },
      tracks_expiry: { type: "boolean" },
      invima_status: { type: "string", maxLength: 20 },
    },
    ["name", "search_name"],
    [["search_name"]],
  ),
  item_barcodes: schema<BarcodeDoc>(
    {
      item_id: { type: "string", maxLength: 36 },
      code: { type: "string", maxLength: 64 },
      is_primary: { type: "boolean" },
    },
    ["item_id", "code"],
    // A scan resolves to one item in under 50 ms (§4), and that is a lookup by
    // code on every keystroke of a barcode burst.
    [["code"], ["item_id"]],
  ),
  manufacturers: schema<NamedDoc>(
    {
      name: { type: "string", maxLength: 200 },
      search_name: { type: "string", maxLength: 200 },
    },
    ["name"],
    [["search_name"]],
  ),
  categories: schema<NamedDoc>(
    {
      name: { type: "string", maxLength: 120 },
      parent_id: { type: ["string", "null"], maxLength: 36 },
    },
    ["name"],
  ),
  item_prices: schema<PriceDoc>(
    {
      item_id: { type: "string", maxLength: 36 },
      location_id: { type: ["string", "null"], maxLength: 36 },
      price: { type: "string" },
      effective_from: { type: "string", maxLength: 10 },
      effective_to: { type: ["string", "null"], maxLength: 10 },
    },
    ["item_id", "price"],
    [["item_id"]],
  ),
  customers: schema<CustomerDoc>(
    {
      document_type: { type: "string", maxLength: 8 },
      document: { type: "string", maxLength: 32 },
      name: { type: "string", maxLength: 200 },
      phone: { type: "string" },
      email: { type: "string" },
      address: { type: "string" },
      data_consent: { type: "boolean" },
    },
    ["name"],
    // Found offline by document number, which is criterion 8's second half.
    [["document"], ["name"]],
  ),
} satisfies Record<CollectionName, RxJsonSchema<never>>;

export const OUTBOX_SCHEMA: RxJsonSchema<OutboxDoc> = {
  version: 0,
  primaryKey: "client_uuid",
  type: "object",
  properties: {
    client_uuid: { type: "string", maxLength: 36 },
    kind: { type: "string", maxLength: 64 },
    occurred_at: { type: "string", maxLength: 32 },
    payload: { type: "object" },
    attempts: { type: "number" },
    last_error: { type: "string" },
    queued_at: { type: "string", maxLength: 32 },
  },
  required: ["client_uuid", "kind", "occurred_at", "payload", "queued_at"],
  indexes: [["kind"]],
} as unknown as RxJsonSchema<OutboxDoc>;
