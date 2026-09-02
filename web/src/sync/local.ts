import type { SyncDatabase } from "./store";
import type { BarcodeDoc, CustomerDoc, ItemDoc, PriceDoc } from "./registry";
import { businessDay } from "@/ui/format";
import { queue, uuidV7 } from "./outbox";

/**
 * The local reads and the one local write.
 *
 * **Every value on a till surface is either a replicated server row or a row in
 * this device's own outbox** (§5 rule 1). Nothing here computes a figure from a
 * local aggregate and presents it unqualified, and nothing writes a derived
 * value into a replicated collection.
 *
 * Reading is a local query at zero latency and renders **with no staleness
 * marker**, because it is tier 1 of §B.9.2 — the till's own sede, read from the
 * local store. The price list's own freshness is stated once, in the sync
 * panel, and never as a dot on every line.
 */

/** `Losartán` folded to `losartan`, exactly as the database's generated
 *  `search_name` is — so a local search finds what a server search would. */
export function fold(term: string): string {
  return term
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

export interface CatalogHit {
  id: string;
  name: string;
  presentation: string;
  price: string | null;
  invima_status: string;
}

/**
 * The counter's catalog search: **keystroke to filtered list under 30 ms p95,
 * with no network in the path** (§4).
 *
 * It is a prefix-and-substring match over the indexed `search_name`, then one
 * indexed price lookup per hit — never a scan of `item_prices`, and never a
 * network call.
 */
export async function searchCatalog(
  database: SyncDatabase,
  term: string,
  limit = 25,
): Promise<CatalogHit[]> {
  const folded = fold(term);
  if (!folded) return [];
  const items = (await database.collections
    .items!.find({
      selector: { search_name: { $regex: `.*${escapeRegex(folded)}.*` } },
      sort: [{ search_name: "asc" }],
      limit,
    })
    .exec()) as unknown as ItemDoc[];

  const prices = await currentPrices(
    database,
    items.map((one) => one.id),
  );
  return items.map((item) => ({
    id: item.id,
    name: item.name,
    presentation: item.presentation,
    price: prices.get(item.id) ?? null,
    invima_status: item.invima_status,
  }));
}

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * This sede's price for each item, **resolved the way the server resolves it**.
 *
 * Three rules, in order, and all three are load-bearing:
 *
 *  1. The window is `[effective_from, effective_to)` and the end is exclusive.
 *     A till legitimately holds more than one row per item per scope — a dated
 *     repricing is pulled the day it is written, not the day it starts — so a
 *     resolver that ignored the window would charge tomorrow's price today, or
 *     yesterday's tomorrow, depending on which uuid RxDB happened to return
 *     first.
 *  2. A row scoped to this sede beats the network-wide one.
 *  3. Between two rows of the same scope, the later `effective_from` wins.
 *
 * There is no location filter here and no way for another sede's price to be in
 * the running: the device only ever holds its own sede's rows and the
 * network's, because that is the registry's predicate.
 */
export async function currentPrices(
  database: SyncDatabase,
  itemIds: string[],
  on: string = businessDay(),
): Promise<Map<string, string>> {
  if (itemIds.length === 0) return new Map();
  const rows = (await database.collections
    .item_prices!.find({ selector: { item_id: { $in: itemIds } } })
    .exec()) as unknown as PriceDoc[];
  const best = new Map<string, PriceDoc>();
  for (const row of rows) {
    if (row.effective_from > on) continue;
    if (row.effective_to !== null && row.effective_to <= on) continue;
    const held = best.get(row.item_id);
    if (!held || wins(row, held)) best.set(row.item_id, row);
  }
  return new Map([...best].map(([id, row]) => [id, row.price]));
}

function wins(row: PriceDoc, held: PriceDoc): boolean {
  const scoped = row.location_id !== null;
  const heldScoped = held.location_id !== null;
  if (scoped !== heldScoped) return scoped;
  return row.effective_from > held.effective_from;
}

/** Criterion 8's second half: findable in the local store by name **and** by
 *  document number, with the network unplugged. */
export async function findCustomers(
  database: SyncDatabase,
  term: string,
  limit = 25,
): Promise<CustomerDoc[]> {
  const trimmed = term.trim();
  if (!trimmed) return [];
  const pattern = `.*${escapeRegex(trimmed)}.*`;
  const rows = (await database.collections
    .customers!.find({
      selector: {
        $or: [
          { document: { $regex: pattern } },
          { name: { $regex: pattern, $options: "i" } },
        ],
      },
      limit,
    })
    .exec()) as unknown as CustomerDoc[];
  return rows;
}

export interface NewCustomer {
  document_type: string;
  document: string;
  name: string;
  phone?: string;
  email?: string;
  address?: string;
  data_consent?: boolean;
}

/**
 * Register a customer at the counter — **the one client-originated write that
 * exists at S2**, and the one the whole push path is proven on.
 *
 * The row is written to the local store **and** to the outbox immediately, with
 * no loading state at all (§B.10.1, optimistic writes). The status line moves
 * to `Sin conexión · 1 por enviar`. On reconnection the outcome is `applied`,
 * `duplicate` or `merged` — all three successes, all three removing it from the
 * outbox.
 *
 * `customers` carries no `client_uuid`: it is S1's master-data table rather than
 * an event log, so it is pushed under the declared natural key
 * `(tenant_id, document_type, document)` (ledger rule 8, second paragraph).
 * The envelope still carries one, because the outbox needs a key of its own.
 */
export async function registerCustomer(
  database: SyncDatabase,
  values: NewCustomer,
): Promise<CustomerDoc> {
  const document: CustomerDoc = {
    id: uuidV7(),
    updated_at: new Date().toISOString(),
    document_type: values.document_type,
    document: values.document,
    name: values.name,
    phone: values.phone ?? "",
    email: values.email ?? "",
    address: values.address ?? "",
    data_consent: values.data_consent ?? false,
  };
  const local = await database.collections.customers!.insert(document);
  try {
    await queue(database, "customers", {
      id: document.id,
      document_type: document.document_type,
      document: document.document,
      name: document.name,
      phone: document.phone,
      email: document.email,
      address: document.address,
      data_consent: document.data_consent,
    });
  } catch (failure) {
    // **The two writes stand or fall together.** The realistic cause of a throw
    // here is a storage quota, which fails the second write after the first
    // succeeded — and a customer in the local store with nothing in the outbox
    // is a person the counter says is registered and the server will never
    // hear about. Better a refusal the cashier can retry than a row that
    // silently never leaves.
    await local.remove();
    throw failure;
  }
  return document;
}

/**
 * Queue one entry of `Cargar mercancía` from a till that is offline.
 *
 * **Merchandise arrives whether or not the internet is up**, and a box that
 * cannot be received is a box that gets sold from while being invisible. Each
 * line carries its own `client_uuid`, so the server dedupes on it and a push
 * that timed out after the server committed is a no-op on replay (A5).
 *
 * The lot travels as its **natural key** -- code and expiry -- rather than as a
 * row: `lots` is not a till-written table (ledger rule 8), and the server
 * creates or matches it inside the pinned push transaction.
 *
 * **Nothing is written to the local stock collections here.** They are a
 * snapshot of server state, and a till that added its own pending receipt to
 * them would be inventing a quantity -- which is exactly what §5 rule 1
 * forbids. The projection follows when the push lands.
 */
export async function queueReceiptLines(
  database: SyncDatabase,
  documentId: string,
  lines: {
    item_id: string;
    lot_code: string;
    expires_at: string | null;
    quantity: number;
    unit_cost: string | null;
  }[],
): Promise<number> {
  for (const line of lines) {
    // **The outbox row's own `client_uuid` is the line's key**, minted by
    // `queue` as a uuid v7 -- one key per line and not two, so the server
    // dedupes on the same value the outbox retries under. The document id is
    // the entry's, shared by every line of it, so the moves the push writes
    // hang off one `receipts` document exactly as the online path's do.
    await queue(database, "receipt_lines", {
      ...line,
      document_id: documentId,
      reason: "standalone_receipt",
    });
  }
  return lines.length;
}

/**
 * A scan, resolved from the local store — **the half of `Cargar mercancía` that
 * makes it offline-capable.**
 *
 * Acceptance 19 pulls the cable and expects the surface to keep accepting
 * scans. A resolution that goes to the server cannot: merchandise arrives
 * whether or not the internet is up, and a box that cannot be received is a box
 * that gets sold from while being invisible.
 *
 * `item_barcodes` is in the registry with an index on `code`, so this is one
 * indexed lookup and then one more by id — the same path §4 budgets at 50 ms
 * for a counter scan, and there is no second implementation of it.
 */
export async function scanBarcode(
  database: SyncDatabase,
  code: string,
): Promise<ItemDoc | null> {
  const scanned = code.trim();
  if (!scanned) return null;
  const barcode = (await database.collections
    .item_barcodes!.findOne({ selector: { code: scanned } })
    .exec()) as unknown as BarcodeDoc | null;
  if (!barcode) return null;
  const item = (await database.collections
    .items!.findOne(barcode.item_id)
    .exec()) as unknown as ItemDoc | null;
  return item ?? null;
}

/**
 * Queue one counted line from a till walked around a back room.
 *
 * **The count itself is created online** — the list view needs the network
 * anyway — and its lines queue against that document's id. `expected_quantity`
 * is stamped by the server when the batch lands, which is the same rule the
 * online path follows: the line is entered when it is entered, and the
 * arithmetic between the stamp and the close is the server's.
 */
export async function queueCountLine(
  database: SyncDatabase,
  line: {
    count_id: string;
    item_id: string;
    lot_id: string | null;
    counted_quantity: number;
  },
): Promise<void> {
  await queue(database, "stock_count_lines", { ...line });
}
