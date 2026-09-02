import type { SyncDatabase } from "./store";
import type { CustomerDoc, ItemDoc, PriceDoc } from "./registry";
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
