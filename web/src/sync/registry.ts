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
export const REGISTRY_VERSION = 3;

/**
 * **The stores opened on a till, which is not the same list as the streams.**
 *
 * RxDB's open-core build caps a database at thirteen collections and the whole
 * registry is committed to more streams than that: S2's six, S3's three, S4's
 * six, S5's one and S8's two. So a store is opened per *shape*, and a stream
 * that carries the same shape under a different predicate lands in the store it
 * shares -- which is also what S2's own amendment table declares S3 adds:
 * `stock_on_hand`, `lots` and `stock_policies`, with the other-location set
 * inside the first of them rather than beside it.
 */
export const COLLECTIONS = [
  "items",
  "item_barcodes",
  "manufacturers",
  "categories",
  "item_prices",
  "customers",
  // S3's amendment (ownership.md rule 9). **No S3 surface reads these in a
  // browser** -- Existencias is online-only and server-authoritative -- so they
  // are provisioned for S4's counter: its stock, its FEFO queue, its expiry
  // display and its low-stock signal, all at zero latency.
  "lots",
  "stock_on_hand",
  "stock_policies",
  // S4's amendment (ownership.md rule 9). **Six streams, three stores**, and
  // the arithmetic is the reason: RxDB's open-core build refuses the fifteenth
  // collection outright and stalls for 1,8 s on the fourteenth, which is most
  // of the 2,5 s cold-start budget spent on a limit rather than on the till.
  // Nine stores were open before this stage; these three take the database to
  // twelve, and the outbox is the thirteenth.
  //
  // The grouping is by shape and not by convenience: a sale and a return are
  // both documents with a number, a total and a tax; a line, a payment and a
  // returned line are all components of one, each carrying its parent's id.
  // **S5 and S8 share these stores rather than opening their own** — there is
  // no fourteenth.
  "shifts",
  "sales",
  "sale_lines",
] as const;

export type CollectionName = (typeof COLLECTIONS)[number];

/**
 * **The pull streams**, one per collection the server serves. Each has its own
 * cursor, its own epoch and its own digest, because each is a different
 * predicate over a different scan -- `stock_elsewhere` is every *other* sede's
 * rows for the references this one is short of, and a single cursor over both
 * sets would advance past one on the other's pages.
 */
export const STREAMS = [
  ...COLLECTIONS,
  "stock_elsewhere",
  "payments",
  "sale_returns",
  "sale_return_lines",
] as const;

export type StreamName = (typeof STREAMS)[number];

/** Which store a stream's documents land in. Identity except where two streams
 *  share a shape. */
const SHARED_STORE: Partial<Record<StreamName, CollectionName>> = {
  stock_elsewhere: "stock_on_hand",
  // A return is a sale document; a payment and a returned line are both
  // components of one. See the note on `COLLECTIONS`.
  sale_returns: "sales",
  payments: "sale_lines",
  sale_return_lines: "sale_lines",
};

export function storeOf(stream: StreamName): CollectionName {
  return SHARED_STORE[stream] ?? (stream as CollectionName);
}

/** Which stream each of S4's shared-store documents came from, as the `kind`
 *  the server stamps on the document itself. */
const KIND_OF: Partial<Record<StreamName, string>> = {
  sales: "sale",
  sale_returns: "return",
  sale_lines: "line",
  payments: "payment",
  sale_return_lines: "return_line",
};

/**
 * Which of a shared store's documents belong to which stream.
 *
 * For the two stock streams the split is the predicate itself: this sede's rows
 * are the till's own stock, every other sede's are the other-location set. For
 * S4's five it is the `kind` the server stamps. Either way it is what lets a
 * reset wipe one stream without taking the other's rows with it, and what lets
 * the daily digest compare each half against the server's own answer for it.
 */
export function belongsTo(
  stream: StreamName,
  document: { location_id?: string | null; kind?: string },
  deviceLocationId: string,
): boolean {
  if (stream === "stock_on_hand")
    return document.location_id === deviceLocationId;
  if (stream === "stock_elsewhere")
    return document.location_id !== deviceLocationId;
  const kind = KIND_OF[stream];
  // **A `kind` and not a heuristic over which fields are present.** A reset
  // wipes one stream's rows out of a shared store and leaves the others where
  // they are; a guess that got it wrong would delete rows whose cursor stayed
  // put, and the till would sit on a hole until the next daily digest.
  return kind === undefined || document.kind === kind;
}

/**
 * The streams whose predicate names a sede. When the device's `location_id`
 * changes — an office moved the till — these are wiped and re-pulled from a
 * zero cursor, and the tenant-wide ones are left alone.
 */
export const LOCATION_SCOPED: readonly StreamName[] = [
  "item_prices",
  // All six of S4's: a till moved to another sede holds the wrong sede's
  // tickets exactly as it holds the wrong sede's stock.
  "shifts",
  "sales",
  "sale_lines",
  "payments",
  "sale_returns",
  "sale_return_lines",
  // All four of S3's, and for the same reason: every one of them is selected by
  // where the till is. `lots` has no `location_id` of its own, but its
  // predicate joins through `stock_on_hand`, so a till that moved sede holds
  // the wrong lots exactly as it holds the wrong stock.
  "lots",
  "stock_on_hand",
  "stock_elsewhere",
  "stock_policies",
];

/** The collections a device may write. `customers` is S2's; the other two are
 *  S3's, and neither is ever pulled back -- a till sends an event and already
 *  knows what it sent. */
export const PUSHABLE: readonly string[] = [
  "customers",
  "receipt_lines",
  "stock_count_lines",
  // S4's six, and they are the first collections a till both reads and writes.
  "shifts",
  "sales",
  "sale_lines",
  "payments",
  "sale_returns",
  "sale_return_lines",
];

/**
 * §B.9.3 · the sync panel breaks the queue down by kind -- `Ventas 2 ·
 * Movimientos 9 · Conteos 1`. At S2 that is `Clientes 1`; S3 adds the next two
 * and S4 `Ventas`, each by adding a line here rather than a second queue.
 */
export const QUEUE_LABELS: Record<string, string> = {
  customers: "Clientes",
  receipt_lines: "Movimientos",
  stock_count_lines: "Conteos",
  shifts: "Turnos",
  sales: "Ventas",
  sale_returns: "Devoluciones",
};

/**
 * Which label a queued row is counted under.
 *
 * §B.9.3 draws `Ventas 2 · Movimientos 9 · Conteos 1`, and **a ticket's lines
 * and its payments are counted as the sale they belong to**: a cashier asks how
 * many *sales* are waiting, and eleven rows for one ticket is a number that
 * measures the protocol rather than the queue. S4 is the first stage whose one
 * document is several outbox rows, which is why the grouping exists at all.
 */
const QUEUE_GROUP: Record<string, string> = {
  sale_lines: "sales",
  payments: "sales",
  sale_return_lines: "sale_returns",
};

export function queueGroups(
  counts: Record<string, number>,
): [string, number][] {
  const grouped: Record<string, number> = {};
  for (const [kind, total] of Object.entries(counts)) {
    if (total <= 0) continue;
    const key = QUEUE_GROUP[kind] ?? kind;
    grouped[key] = (grouped[key] ?? 0) + total;
  }
  // The registry's own order, so the line reads the same on every till rather
  // than in whatever order the outbox happened to answer.
  const order = Object.keys(QUEUE_LABELS);
  return Object.entries(grouped).sort(
    (a, b) => order.indexOf(a[0]) - order.indexOf(b[0]),
  );
}

/** The Spanish name of each stream, for the first-sync card. The card counts a
 *  download, and a download is a stream. */
export const COLLECTION_LABELS: Record<StreamName, string> = {
  items: "Catálogo",
  item_barcodes: "Códigos de barras",
  manufacturers: "Laboratorios",
  categories: "Categorías",
  item_prices: "Precios",
  customers: "Clientes",
  lots: "Lotes",
  stock_on_hand: "Existencias de la sede",
  stock_elsewhere: "Existencias en otras sedes",
  stock_policies: "Umbrales",
  shifts: "Turnos",
  sales: "Ventas recientes",
  sale_lines: "Líneas de venta",
  payments: "Pagos",
  sale_returns: "Devoluciones",
  sale_return_lines: "Líneas de devolución",
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

export interface StockDoc {
  id: string;
  updated_at: string;
  location_id: string;
  item_id: string;
  lot_id: string | null;
  quantity: number;
  /** Carried only by the other-location set: a till holds no `locations`
   *  collection, and `hay 96 en Suba` is read offline or not at all. */
  location_name: string | null;
}

export interface LotDoc {
  id: string;
  updated_at: string;
  item_id: string;
  lot_code: string;
  expires_at: string | null;
  unit_cost: string | null;
}

/**
 * A turno, as the till stores it.
 *
 * `declared_total` and `variance` are null on an open shift and on a forced
 * close, and the two are different states: an open drawer has not been counted
 * *yet*, and a forced close is a drawer nobody counted at all (§6).
 */
export interface ShiftDoc {
  id: string;
  updated_at: string;
  location_id: string;
  /** The till the drawer belongs to. The collection is scoped by sede, so a
   *  device holds its neighbours' turnos too — and this is what tells them
   *  apart. */
  device_id: string | null;
  user_id: string | null;
  user_name: string;
  opened_at: string;
  closed_at: string | null;
  opening_float: string;
  declared_total: string | null;
  variance: string | null;
  status: "open" | "closed";
}

/**
 * A ticket **or** a devolución — one store, split by `kind`.
 *
 * Money is a string on the wire and in the store, never a number: `12345.67`
 * through IEEE 754 and back is not `12345.67`, and this is the one figure a
 * customer is about to pay.
 */
export interface SaleDoc {
  id: string;
  updated_at: string;
  kind: "sale" | "return";
  location_id: string;
  shift_id: string | null;
  number: string;
  status: "open" | "closed" | "voided";
  source: "counter" | "imported";
  customer_id: string | null;
  subtotal: string;
  discount: string;
  tax: string;
  total: string;
  sold_by_user_id: string | null;
  sold_by_name: string;
  occurred_at: string;
  /** The sale a devolución reverses. Null on a sale. */
  sale_id: string | null;
  reason: string;
  refund_method: string | null;
}

/**
 * A ticket line, a payment or a returned line — one store, split by `kind`.
 *
 * `parent_id` is the sale's id for a line and a payment and the return's for a
 * returned line, which is what lets one index serve all three.
 */
export interface SaleLineDoc {
  id: string;
  updated_at: string;
  kind: "line" | "payment" | "return_line";
  parent_id: string;
  location_id: string;
  sale_line_id: string | null;
  position: number | null;
  item_id: string | null;
  lot_id: string | null;
  quantity: number | null;
  unit_price: string | null;
  discount: string | null;
  vat_class: string | null;
  tax_amount: string | null;
  unit_cost: string | null;
  from_suggestion: boolean | null;
  method: string | null;
  amount: string | null;
  reference: string | null;
}

export interface PolicyDoc {
  id: string;
  updated_at: string;
  item_id: string;
  location_id: string | null;
  min_quantity: number | null;
  max_quantity: number | null;
  reorder_point: number | null;
  target_coverage_days: number | null;
  source: string;
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

const STOCK_FIELDS = {
  location_id: { type: "string", maxLength: 36 },
  item_id: { type: "string", maxLength: 36 },
  lot_id: { type: ["string", "null"], maxLength: 36 },
  quantity: { type: "number" },
  location_name: { type: ["string", "null"] },
} as const;

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
  lots: schema<LotDoc>(
    {
      item_id: { type: "string", maxLength: 36 },
      lot_code: { type: "string", maxLength: 64 },
      expires_at: { type: ["string", "null"], maxLength: 10 },
      unit_cost: { type: ["string", "null"] },
    },
    ["item_id", "lot_code"],
    // **`item_id` and not `expires_at`.** The FEFO queue is expiry ascending
    // within one item, but `expires_at` is nullable -- an item that tracks no
    // expiry has none -- and RxDB cannot index a nullable field. The lookup is
    // by item and the ordering is over the handful of lots that come back,
    // which is where a counter reads it anyway.
    [["item_id"]],
  ),
  // **One store, two streams.** The other-location set has the same shape and
  // the same key, and §B.9.2's tier-2 distinction -- another sede's stock,
  // rendered with the staleness marker and its own reading -- is
  // `location_id !== <this sede>`, which every row already carries. A second
  // store would spend one of the thirteen RxDB opens on a boolean that is
  // already in the document, and S4 has six streams still to place.
  stock_on_hand: schema<StockDoc>(
    STOCK_FIELDS,
    ["item_id", "quantity"],
    [["item_id"]],
  ),
  shifts: schema<ShiftDoc>(
    {
      location_id: { type: "string", maxLength: 36 },
      device_id: { type: ["string", "null"], maxLength: 36 },
      user_id: { type: ["string", "null"], maxLength: 36 },
      user_name: { type: "string" },
      opened_at: { type: "string", maxLength: 32 },
      closed_at: { type: ["string", "null"], maxLength: 32 },
      opening_float: { type: "string" },
      declared_total: { type: ["string", "null"] },
      variance: { type: ["string", "null"] },
      status: { type: "string", maxLength: 16 },
    },
    ["location_id", "opened_at", "status"],
    // The one query a till runs against this store on every route load: **the
    // open turno for this device**, which is what makes the counter sellable
    // (acceptance 4). The device is applied over the handful of rows the status
    // index returns, because it is nullable and RxDB will not index a nullable
    // field.
    [["status", "opened_at"]],
  ),
  sales: schema<SaleDoc>(
    {
      kind: { type: "string", maxLength: 8 },
      location_id: { type: "string", maxLength: 36 },
      shift_id: { type: ["string", "null"], maxLength: 36 },
      number: { type: "string", maxLength: 32 },
      status: { type: "string", maxLength: 16 },
      source: { type: "string", maxLength: 16 },
      customer_id: { type: ["string", "null"], maxLength: 36 },
      subtotal: { type: "string" },
      discount: { type: "string" },
      tax: { type: "string" },
      total: { type: "string" },
      sold_by_user_id: { type: ["string", "null"], maxLength: 36 },
      sold_by_name: { type: "string" },
      occurred_at: { type: "string", maxLength: 32 },
      sale_id: { type: ["string", "null"], maxLength: 36 },
      reason: { type: "string" },
      refund_method: { type: ["string", "null"], maxLength: 16 },
    },
    ["kind", "location_id", "number", "status", "occurred_at"],
    // Found by number, which is how a cashier reaches yesterday's sale for a
    // return; and listed by kind and date, which is the recent-sales list and
    // the average-ticket note's own window.
    [["number"], ["kind", "occurred_at"]],
  ),
  sale_lines: schema<SaleLineDoc>(
    {
      kind: { type: "string", maxLength: 16 },
      parent_id: { type: "string", maxLength: 36 },
      location_id: { type: "string", maxLength: 36 },
      sale_line_id: { type: ["string", "null"], maxLength: 36 },
      position: { type: ["number", "null"] },
      item_id: { type: ["string", "null"], maxLength: 36 },
      lot_id: { type: ["string", "null"], maxLength: 36 },
      quantity: { type: ["number", "null"] },
      unit_price: { type: ["string", "null"] },
      discount: { type: ["string", "null"] },
      vat_class: { type: ["string", "null"], maxLength: 16 },
      tax_amount: { type: ["string", "null"] },
      unit_cost: { type: ["string", "null"] },
      from_suggestion: { type: ["boolean", "null"] },
      method: { type: ["string", "null"], maxLength: 16 },
      amount: { type: ["string", "null"] },
      reference: { type: ["string", "null"] },
    },
    ["kind", "parent_id", "location_id"],
    // One index for all three kinds: a ticket's lines, a ticket's payments and
    // a devolución's lines are each `(kind, parent_id)`.
    [["kind", "parent_id"]],
  ),
  stock_policies: schema<PolicyDoc>(
    {
      item_id: { type: "string", maxLength: 36 },
      location_id: { type: ["string", "null"], maxLength: 36 },
      min_quantity: { type: ["number", "null"] },
      max_quantity: { type: ["number", "null"] },
      reorder_point: { type: ["number", "null"] },
      target_coverage_days: { type: ["number", "null"] },
      source: { type: "string", maxLength: 16 },
    },
    ["item_id"],
    [["item_id"]],
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
