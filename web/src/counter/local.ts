import { useEffect, useState } from "react";
import type { SyncDatabase } from "@/sync/store";
import type {
  ItemDoc,
  LotDoc,
  SaleDoc,
  SaleLineDoc,
  ShiftDoc,
  StockDoc,
} from "@/sync/registry";
import type { DeviceRecord } from "@/sync/device";
import { useSync as useSyncContext } from "@/sync/context";
import { queue, uuidV7 } from "@/sync/outbox";
import { currentPrices } from "@/sync/local";
import { businessDay } from "@/ui/format";
import { fromCents, lineTax, toCents, totals } from "./money";

/**
 * The till's own reads and writes, all of them local.
 *
 * **Nothing in this module touches the network.** A scan, a keystroke, a
 * quantity, a payment and a receipt are all local queries and local writes at
 * zero latency, which is the whole of §4's two-read-models boundary and the
 * reason none of §4's counter budgets contains a request. What leaves the till
 * leaves through the outbox, on the ordinary cadence, and nothing on any path
 * here waits for it.
 *
 * **Two events per sale, and that is the protocol.** A ticket's header is
 * written and queued the moment its first line lands, as `open`; its lines and
 * its payments are queued only in the batch that closes it. So an open sale on
 * the server has no lines — which is what makes `Mostrador 3` answerable to an
 * owner without a till having to publish a ticket that is still being rung
 * (§5, §B.8.2).
 *
 * **The local rows are a server snapshot plus this device's pending events**
 * (§5 rule 1). A ticket closed while offline reads as closed here and as open
 * on the server until the queue drains; the push runs before the pull in every
 * cycle, so the two are never seen disagreeing.
 */

/** The ticket a cashier is on, kept where two tabs and a relaunch can both find
 *  it. `localStorage` rather than the store, for the same reason the device
 *  record lives there: it has to be readable before anything is queried. */
const TICKET_KEY = "botica.counter.ticket";

/** The last number this till allocated, per kind. It survives the retention
 *  window the local store does not. */
const SEQUENCE_KEY = "botica.counter.sequence";

/**
 * An RxDB document as plain data.
 *
 * **Every reader in this module returns plain objects**, because an
 * `RxDocument` proxies field access but does not spread: `{...document}` yields
 * the wrapper's own properties and none of the row's, so a ticket copied that
 * way loses its id — and the receipt is rendered from the copy. The cost is one
 * call at the boundary; the alternative is a class of bug that only shows up in
 * whichever field somebody happened to spread.
 */
function plain<T>(rows: { toJSON: () => unknown }[]): T[] {
  return rows.map((row) => row.toJSON() as T);
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function heldTicketId(): string | null {
  return storage()?.getItem(TICKET_KEY) ?? null;
}

export function holdTicket(saleId: string | null) {
  if (saleId) storage()?.setItem(TICKET_KEY, saleId);
  else storage()?.removeItem(TICKET_KEY);
}

// ---------------------------------------------------------------------------
// The turno
// ---------------------------------------------------------------------------

/**
 * The open turno **on this device**, or null.
 *
 * The collection is scoped by sede, so a till holds its neighbours' turnos too:
 * a query that asked only for an open one would hand the second till at a sede
 * the first till's drawer, and its `Cerrar turno` would then count somebody
 * else's cash. **The drawer belongs to the till, not to the person.**
 *
 * **The till cannot sell without one**, and that is not a network block:
 * opening is a local write. A counter sale outside a cash session cannot be
 * reconciled, and the server's own `CHECK` makes it impossible anyway.
 */
export async function openShift(
  database: SyncDatabase,
  device: DeviceRecord,
): Promise<ShiftDoc | null> {
  const rows = plain<ShiftDoc>(
    await database.collections
      .shifts!.find({ selector: { status: "open" } })
      .exec(),
  );
  return rows.find((one) => one.device_id === device.id) ?? null;
}

export async function startShift(
  database: SyncDatabase,
  device: DeviceRecord,
  user: { id: string; name: string },
  openingFloat: number,
): Promise<ShiftDoc> {
  const now = new Date().toISOString();
  const document: ShiftDoc = {
    id: uuidV7(),
    updated_at: now,
    location_id: device.location_id,
    device_id: device.id,
    user_id: user.id,
    user_name: user.name,
    opened_at: now,
    closed_at: null,
    opening_float: fromCents(openingFloat),
    declared_total: null,
    variance: null,
    status: "open",
  };
  await database.collections.shifts!.insert(document);
  await queueShift(database, document);
  return document;
}

/**
 * Close the turno with the cashier's own count.
 *
 * **The variance is stored whatever it is** and nothing here offers to make it
 * zero. The figure the till computes is a display figure: the server recomputes
 * it from the sales it actually holds when the event lands, which is what makes
 * a sale that arrives after the close still count against the right turno.
 */
export async function closeShift(
  database: SyncDatabase,
  shift: ShiftDoc,
  declaredTotal: number,
): Promise<ShiftDoc> {
  const expected = await expectedCash(database, shift);
  const closed: ShiftDoc = {
    ...shift,
    status: "closed",
    closed_at: new Date().toISOString(),
    declared_total: fromCents(declaredTotal),
    variance: fromCents(declaredTotal - expected),
    updated_at: new Date().toISOString(),
  };
  const local = await database.collections.shifts!.findOne(shift.id).exec();
  await local?.incrementalPatch({
    status: closed.status,
    closed_at: closed.closed_at,
    declared_total: closed.declared_total,
    variance: closed.variance,
  });
  await queueShift(database, closed);
  return closed;
}

function queueShift(database: SyncDatabase, shift: ShiftDoc) {
  // **The envelope's key is the event and the payload's is the row.** A turno
  // is opened and later closed, so one row receives two events; each is
  // idempotent on its own key and both converge on the shift under
  // `UNIQUE (tenant_id, client_uuid)`.
  return queue(database, "shifts", {
    client_uuid: shift.id,
    id: shift.id,
    status: shift.status,
    user_id: shift.user_id,
    opened_at: shift.opened_at,
    closed_at: shift.closed_at,
    opening_float: shift.opening_float,
    declared_total: shift.declared_total,
  });
}

/**
 * `Efectivo esperado` — what the drawer should hold, computed from this
 * device's own rows.
 *
 * Voided sales are excluded: the money went back over the counter, so counting
 * it would manufacture a shortfall out of a correction. A refund leaves the
 * drawer that is open now, whichever turno the sale was rung in.
 */
export async function expectedCash(
  database: SyncDatabase,
  shift: ShiftDoc,
): Promise<number> {
  const sales = await salesOfShift(database, shift.id);
  const closed = sales.filter((one) => one.status === "closed");
  const payments = await componentsOf(
    database,
    "payment",
    closed.map((one) => one.id),
  );
  const cash = payments
    .filter((one) => one.method === "cash")
    .reduce((sum, one) => sum + toCents(one.amount), 0);
  const returns = plain<SaleDoc>(
    await database.collections
      .sales!.find({ selector: { kind: "return" } })
      .exec(),
  );
  const refunded = returns
    .filter((one) => one.shift_id === shift.id && one.refund_method === "cash")
    .reduce((sum, one) => sum + toCents(one.total), 0);
  return toCents(shift.opening_float) + cash - refunded;
}

export async function salesOfShift(
  database: SyncDatabase,
  shiftId: string,
): Promise<SaleDoc[]> {
  const rows = plain<SaleDoc>(
    await database.collections
      .sales!.find({ selector: { kind: "sale" } })
      .exec(),
  );
  return rows.filter((one) => one.shift_id === shiftId);
}

// ---------------------------------------------------------------------------
// The ticket
// ---------------------------------------------------------------------------

export interface Ticket {
  sale: SaleDoc;
  lines: SaleLineDoc[];
}

export async function readTicket(
  database: SyncDatabase,
  saleId: string,
): Promise<Ticket | null> {
  const found = await database.collections.sales!.findOne(saleId).exec();
  if (!found) return null;
  return {
    sale: found.toJSON() as SaleDoc,
    lines: await componentsOf(database, "line", [saleId]),
  };
}

/** One kind of a shared store's rows, for one or more parents. */
export async function componentsOf(
  database: SyncDatabase,
  kind: "line" | "payment" | "return_line",
  parentIds: string[],
): Promise<SaleLineDoc[]> {
  if (parentIds.length === 0) return [];
  const rows = plain<SaleLineDoc>(
    await database.collections
      .sale_lines!.find({ selector: { kind, parent_id: { $in: parentIds } } })
      .exec(),
  );
  return rows.sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
}

/**
 * Open a ticket. Called on the **first line**, never on route load: a sale row
 * written every time somebody opens the till is a queue of empty tickets, and
 * `Mostrador 3` would count them.
 */
export async function startTicket(
  database: SyncDatabase,
  device: DeviceRecord,
  shift: ShiftDoc,
  user: { id: string; name: string },
): Promise<SaleDoc> {
  const now = new Date().toISOString();
  const sale: SaleDoc = {
    id: uuidV7(),
    updated_at: now,
    kind: "sale",
    location_id: device.location_id,
    shift_id: shift.id,
    // `{código de caja}-{consecutivo}`. Composed, because the number has to be
    // allocatable on a till with no connection and must never collide across
    // two tills in one sede — and because a composed number can never be read
    // as one some other system issued (§8).
    number: await nextSaleNumber(database, device),
    status: "open",
    source: "counter",
    customer_id: null,
    subtotal: "0.00",
    discount: "0.00",
    tax: "0.00",
    total: "0.00",
    sold_by_user_id: user.id,
    sold_by_name: user.name,
    occurred_at: now,
    sale_id: null,
    reason: "",
    refund_method: null,
  };
  await database.collections.sales!.insert(sale);
  holdTicket(sale.id);
  await queueSale(database, sale);
  return sale;
}

function queueSale(database: SyncDatabase, sale: SaleDoc) {
  return queue(database, "sales", {
    client_uuid: sale.id,
    id: sale.id,
    status: sale.status,
    number: sale.number,
    shift_id: sale.shift_id,
    customer_id: sale.customer_id,
    sold_by_user_id: sale.sold_by_user_id,
    occurred_at: sale.occurred_at,
  });
}

/**
 * Allocate the next number for this till, and remember it.
 *
 * **Two sources, and the higher wins.** The local store is authoritative about
 * what this till has already issued — until the seven-day retention window
 * trims it, at which point a till idle for a fortnight would restart at 1 and
 * the server would refuse the row on `one_sale_number_per_location` at exactly
 * the moment a cashier is holding a customer's money. So the last allocated
 * value is also kept in `localStorage`, which no retention window touches, and
 * the store is what recovers the counter when a browser's site data is cleared.
 * Losing both at once costs a re-claim, which is the same answer S2 gives for
 * the device key.
 *
 * `kind` keeps the two sequences apart: `C1-4821` numbers a sale and `C1D-12` a
 * devolución, and each is unique within its location independently.
 */
async function allocate(
  database: SyncDatabase,
  device: DeviceRecord,
  kind: "sale" | "return",
): Promise<string> {
  const prefix =
    kind === "sale"
      ? `${device.code.toUpperCase()}-`
      : `${device.code.toUpperCase()}D-`;
  const rows = plain<SaleDoc>(
    await database.collections.sales!.find({ selector: { kind } }).exec(),
  );
  let highest = 0;
  for (const row of rows) {
    if (!row.number.startsWith(prefix)) continue;
    const value = Number(row.number.slice(prefix.length));
    if (Number.isFinite(value) && value > highest) highest = value;
  }
  const key = `${SEQUENCE_KEY}.${kind}.${device.id}`;
  const held = Number(storage()?.getItem(key) ?? 0);
  const next = Math.max(highest, Number.isFinite(held) ? held : 0) + 1;
  storage()?.setItem(key, String(next));
  return `${prefix}${next}`;
}

export function nextSaleNumber(database: SyncDatabase, device: DeviceRecord) {
  return allocate(database, device, "sale");
}

export function nextReturnNumber(database: SyncDatabase, device: DeviceRecord) {
  return allocate(database, device, "return");
}

export interface AddLine {
  item: ItemDoc;
  quantity: number;
  lot: LotDoc | null;
  unitPrice: number;
  unitCost: string | null;
  fefoOverride: boolean;
}

/**
 * Add one line to the ticket, or add to the line that is already there.
 *
 * **A second scan of the same product steps the quantity rather than adding a
 * second row.** A ticket with `Acetaminofén` on lines 1 and 4 is a ticket a
 * cashier has to read twice, and a customer buying three of something buys them
 * on one line.
 */
export async function addLine(
  database: SyncDatabase,
  ticket: Ticket,
  entry: AddLine,
): Promise<SaleLineDoc> {
  const held = ticket.lines.find(
    (line) =>
      line.item_id === entry.item.id && line.lot_id === (entry.lot?.id ?? null),
  );
  if (held) {
    return (await setQuantity(
      database,
      held,
      (held.quantity ?? 0) + entry.quantity,
    ))!;
  }
  const now = new Date().toISOString();
  const position =
    ticket.lines.reduce((top, line) => Math.max(top, line.position ?? 0), -1) +
    1;
  const line: SaleLineDoc = {
    id: uuidV7(),
    updated_at: now,
    kind: "line",
    parent_id: ticket.sale.id,
    location_id: ticket.sale.location_id,
    sale_line_id: null,
    position,
    item_id: entry.item.id,
    lot_id: entry.lot?.id ?? null,
    quantity: entry.quantity,
    unit_price: fromCents(entry.unitPrice),
    discount: "0.00",
    vat_class: entry.item.vat_class,
    tax_amount: "0.00",
    unit_cost: entry.unitCost,
    // Created empty and never written here (ledger, disputed columns). S8
    // writes it when `Agregar` is pressed on a suggestion card.
    from_suggestion: false,
    method: null,
    amount: null,
    reference: null,
  };
  line.tax_amount = fromCents(lineTax(asPriced(line)));
  await database.collections.sale_lines!.insert(line);
  return line;
}

function asPriced(line: SaleLineDoc) {
  return {
    quantity: line.quantity ?? 0,
    unit_price: line.unit_price,
    discount: line.discount,
    vat_class: line.vat_class,
  };
}

export async function setQuantity(
  database: SyncDatabase,
  line: SaleLineDoc,
  quantity: number,
): Promise<SaleLineDoc | null> {
  if (quantity <= 0) return removeLine(database, line);
  const next = { ...line, quantity };
  const document = await database.collections
    .sale_lines!.findOne(line.id)
    .exec();
  await document?.incrementalPatch({
    quantity,
    tax_amount: fromCents(lineTax(asPriced(next))),
  });
  return { ...next, tax_amount: fromCents(lineTax(asPriced(next))) };
}

/**
 * A price override or a line discount, both of which only an `owner` or an
 * `admin` ever reaches — §2 gives a `cashier` no pricing authority, and the
 * control is **not rendered** for one rather than rendered disabled (§B.8.3).
 */
export async function repriceLine(
  database: SyncDatabase,
  line: SaleLineDoc,
  values: { unitPrice?: number; discount?: number },
): Promise<SaleLineDoc> {
  const next: SaleLineDoc = {
    ...line,
    unit_price:
      values.unitPrice === undefined
        ? line.unit_price
        : fromCents(values.unitPrice),
    discount:
      values.discount === undefined
        ? line.discount
        : fromCents(values.discount),
  };
  next.tax_amount = fromCents(lineTax(asPriced(next)));
  const document = await database.collections
    .sale_lines!.findOne(line.id)
    .exec();
  await document?.incrementalPatch({
    unit_price: next.unit_price,
    discount: next.discount,
    tax_amount: next.tax_amount,
  });
  return next;
}

export async function setLot(
  database: SyncDatabase,
  line: SaleLineDoc,
  lot: LotDoc | null,
  fefoHeadId: string | null,
): Promise<SaleLineDoc> {
  const document = await database.collections
    .sale_lines!.findOne(line.id)
    .exec();
  await document?.incrementalPatch({ lot_id: lot?.id ?? null });
  // The deviation is the till's own observation and travels with the line: the
  // server applying this sale hours later would recompute the head against a
  // projection that has moved since (§6).
  overrides.set(line.id, Boolean(lot && fefoHeadId && lot.id !== fefoHeadId));
  return { ...line, lot_id: lot?.id ?? null };
}

/** Whether each line took a lot other than the one FEFO offered, held until the
 *  ticket closes and the lines are queued. */
const overrides = new Map<string, boolean>();

export function markOverride(lineId: string, override: boolean) {
  overrides.set(lineId, override);
}

export async function removeLine(
  database: SyncDatabase,
  line: SaleLineDoc,
): Promise<null> {
  const document = await database.collections
    .sale_lines!.findOne(line.id)
    .exec();
  await document?.remove();
  overrides.delete(line.id);
  return null;
}

export interface CommitPayment {
  method: string;
  amount: number;
  reference: string;
}

/**
 * Close the ticket: **one local write, one queued batch, and no network at
 * all.**
 *
 * The header, its lines and its payments are queued together as one document,
 * in that order, because `client_uuid` is uuid v7 and the server applies a
 * batch in that order — so a line never arrives before the sale it belongs to.
 * The stock moves are the server's to append when the lines land (rule 7).
 */
export async function commit(
  database: SyncDatabase,
  ticket: Ticket,
  payments: CommitPayment[],
  customer: { id: string; document_type: string; document: string } | null,
): Promise<Ticket> {
  const figures = totals(ticket.lines.map(asPriced));
  const now = new Date().toISOString();
  const closed: SaleDoc = {
    ...ticket.sale,
    status: "closed",
    customer_id: customer?.id ?? null,
    subtotal: fromCents(figures.subtotal),
    discount: fromCents(figures.discount),
    tax: fromCents(figures.tax),
    total: fromCents(figures.total),
    updated_at: now,
  };
  const document = await database.collections
    .sales!.findOne(ticket.sale.id)
    .exec();
  await document?.incrementalPatch({
    status: closed.status,
    customer_id: closed.customer_id,
    subtotal: closed.subtotal,
    discount: closed.discount,
    tax: closed.tax,
    total: closed.total,
  });

  for (const line of ticket.lines) {
    await queue(database, "sale_lines", {
      id: line.id,
      sale_id: closed.id,
      position: line.position,
      item_id: line.item_id,
      lot_id: line.lot_id,
      quantity: line.quantity,
      unit_price: line.unit_price,
      discount: line.discount,
      vat_class: line.vat_class,
      unit_cost: line.unit_cost,
      fefo_override: overrides.get(line.id) ?? false,
    });
    overrides.delete(line.id);
  }
  const rows: SaleLineDoc[] = [];
  for (const payment of payments) {
    const row: SaleLineDoc = {
      id: uuidV7(),
      updated_at: now,
      kind: "payment",
      parent_id: closed.id,
      location_id: closed.location_id,
      sale_line_id: null,
      position: null,
      item_id: null,
      lot_id: null,
      quantity: null,
      unit_price: null,
      discount: null,
      vat_class: null,
      tax_amount: null,
      unit_cost: null,
      from_suggestion: null,
      method: payment.method,
      amount: fromCents(payment.amount),
      reference: payment.reference,
    };
    rows.push(row);
    await database.collections.sale_lines!.insert(row);
    await queue(database, "payments", {
      id: row.id,
      sale_id: closed.id,
      method: row.method,
      amount: row.amount,
      reference: row.reference,
    });
  }
  // **Queued last, after every line and payment it closes over.** Envelope
  // keys are monotonic uuid v7 and the server applies a batch in that order, so
  // a close event minted first would flip the ticket to `closed` and restate
  // its totals over a sale that had no lines yet.
  await queue(database, "sales", {
    client_uuid: closed.id,
    id: closed.id,
    status: "closed",
    number: closed.number,
    shift_id: closed.shift_id,
    customer_id: closed.customer_id,
    // **The acquirer's natural key travels with the sale**, so a customer that
    // merged onto an existing row during the same blackout is still the person
    // this ticket names (rule 8, second paragraph).
    customer_document_type: customer?.document_type ?? "",
    customer_document: customer?.document ?? "",
    sold_by_user_id: closed.sold_by_user_id,
    occurred_at: closed.occurred_at,
    closed_at: now,
  });
  holdTicket(null);
  return { sale: closed, lines: ticket.lines };
}

/**
 * Void a ticket **inside the cashier's own open turno**.
 *
 * A mis-keyed ticket at 10:14 is corrected at 10:15 by the person who made it.
 * Once the turno closes only an `owner` or an `admin` may void, through
 * `POST /api/sales/{id}/void`: a permissive void is how a till is robbed, and a
 * strictly-office void means a mis-key at 20:00 waits for Monday. The
 * same-shift boundary is the narrowest rule that fixes the common case.
 *
 * **No row is deleted.** The event flips the status and the server appends the
 * reversing moves through S3's ledger service when it lands (rule 7).
 */
export async function voidSale(
  database: SyncDatabase,
  sale: SaleDoc,
  reason: string,
): Promise<SaleDoc> {
  const document = await database.collections.sales!.findOne(sale.id).exec();
  await document?.incrementalPatch({ status: "voided" });
  await queue(database, "sales", {
    client_uuid: sale.id,
    status: "voided",
    number: sale.number,
    shift_id: sale.shift_id,
    void_reason: reason,
    voided_at: new Date().toISOString(),
  });
  return { ...sale, status: "voided" };
}

/** Whether this cashier may still void the ticket themselves: their own open
 *  turno, and nothing returned against it. */
export async function voidableHere(
  database: SyncDatabase,
  ticket: Ticket,
  shift: ShiftDoc | null,
): Promise<boolean> {
  if (!shift || ticket.sale.shift_id !== shift.id) return false;
  if (ticket.sale.status !== "closed") return false;
  const remaining = await returnable(database, ticket);
  return Object.entries(remaining).every(
    ([id, left]) =>
      left === (ticket.lines.find((line) => line.id === id)?.quantity ?? 0),
  );
}

// ---------------------------------------------------------------------------
// What the counter reads back
// ---------------------------------------------------------------------------

const AVERAGE_WINDOW_DAYS = 7;

/** Below this the note reads `—` with its reason. **Never a zero**: a zero
 *  standing in for "we do not know" is the most expensive lie an inventory
 *  system tells (§B.9.2 tier 3). */
export const AVERAGE_FLOOR = 20;

/**
 * `Ticket promedio del punto`, computed from **this location's** closed sales
 * in the window the till actually holds.
 *
 * Null means the till cannot answer, and the surface renders `—` with the
 * reason rather than a figure.
 */
export async function averageTicket(
  database: SyncDatabase,
): Promise<number | null> {
  const since = new Date(
    Date.now() - AVERAGE_WINDOW_DAYS * 24 * 60 * 60 * 1000,
  ).toISOString();
  const rows = plain<SaleDoc>(
    await database.collections
      .sales!.find({ selector: { kind: "sale" } })
      .exec(),
  );
  const closed = rows.filter(
    (one) => one.status === "closed" && one.occurred_at >= since,
  );
  if (closed.length < AVERAGE_FLOOR) return null;
  const sum = closed.reduce((total, one) => total + toCents(one.total), 0);
  return Math.round(sum / closed.length);
}

/** §B.8.2 · the `Mostrador` counter a cashier reads, from the local store and
 *  never from the server. */
export async function openTicketCount(database: SyncDatabase): Promise<number> {
  return database.collections
    .sales!.count({ selector: { kind: "sale", status: "open" } })
    .exec();
}

export async function recentSales(
  database: SyncDatabase,
  limit = 40,
): Promise<SaleDoc[]> {
  const rows = plain<SaleDoc>(
    await database.collections
      .sales!.find({ selector: { kind: "sale", status: "closed" } })
      .exec(),
  );
  return rows
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at))
    .slice(0, limit);
}

export async function findSale(
  database: SyncDatabase,
  number: string,
): Promise<SaleDoc | null> {
  const rows = plain<SaleDoc>(
    await database.collections
      .sales!.find({ selector: { number: number.trim().toUpperCase() } })
      .exec(),
  );
  return rows.find((one) => one.kind === "sale") ?? null;
}

/**
 * `sale_line_id -> units still returnable`, after every earlier return.
 *
 * This is what the devolución's stepper is capped at, and what the empty state
 * `Esta venta no tiene unidades por devolver.` is derived from.
 */
export async function returnable(
  database: SyncDatabase,
  ticket: Ticket,
): Promise<Record<string, number>> {
  const lineIds = new Set(ticket.lines.map((line) => line.id));
  const rows = plain<SaleLineDoc>(
    await database.collections
      .sale_lines!.find({ selector: { kind: "return_line" } })
      .exec(),
  );
  const taken: Record<string, number> = {};
  for (const row of rows) {
    if (!row.sale_line_id || !lineIds.has(row.sale_line_id)) continue;
    taken[row.sale_line_id] =
      (taken[row.sale_line_id] ?? 0) + (row.quantity ?? 0);
  }
  const answer: Record<string, number> = {};
  for (const line of ticket.lines) {
    answer[line.id] = Math.max(0, (line.quantity ?? 0) - (taken[line.id] ?? 0));
  }
  return answer;
}

export interface ReturnEntry {
  lineId: string;
  quantity: number;
}

/**
 * Register a devolución against a closed sale.
 *
 * Each line's money is stamped **from the original line**, not from today's
 * price list: a credit note must reverse what was charged, and a price that
 * changed in between is exactly the case the sale's own record settles (§5).
 * The stock goes back to the lot the line sold, which is the server's to append.
 */
export async function registerReturn(
  database: SyncDatabase,
  device: DeviceRecord,
  shift: ShiftDoc,
  user: { id: string; name: string },
  ticket: Ticket,
  entries: ReturnEntry[],
  values: { reason: string; refundMethod: string },
): Promise<SaleDoc> {
  const now = new Date().toISOString();
  const lines = entries
    .map((entry) => {
      const line = ticket.lines.find((one) => one.id === entry.lineId);
      return line ? { entry, line } : null;
    })
    .filter((one): one is { entry: ReturnEntry; line: SaleLineDoc } => !!one);

  const priced = lines.map(({ entry, line }) => {
    // The discount is prorated, so a partial return of a discounted line
    // refunds its share rather than the whole discount or none of it.
    const share = entry.quantity / (line.quantity || 1);
    const discount = Math.round(toCents(line.discount) * share);
    return {
      quantity: entry.quantity,
      unit_price: line.unit_price,
      discount: fromCents(discount),
      vat_class: line.vat_class,
      line,
    };
  });
  const figures = totals(priced);

  const header: SaleDoc = {
    id: uuidV7(),
    updated_at: now,
    kind: "return",
    location_id: device.location_id,
    shift_id: shift.id,
    number: await nextReturnNumber(database, device),
    status: "closed",
    source: "counter",
    customer_id: ticket.sale.customer_id,
    subtotal: fromCents(figures.subtotal),
    discount: fromCents(figures.discount),
    tax: fromCents(figures.tax),
    total: fromCents(figures.total),
    sold_by_user_id: user.id,
    sold_by_name: user.name,
    occurred_at: now,
    sale_id: ticket.sale.id,
    reason: values.reason,
    refund_method: values.refundMethod,
  };
  await database.collections.sales!.insert(header);
  await queue(database, "sale_returns", {
    id: header.id,
    sale_id: ticket.sale.id,
    number: header.number,
    shift_id: shift.id,
    reason: values.reason,
    refund_method: values.refundMethod,
    returned_by_user_id: user.id,
  });

  for (const row of priced) {
    const document: SaleLineDoc = {
      id: uuidV7(),
      updated_at: now,
      kind: "return_line",
      parent_id: header.id,
      location_id: header.location_id,
      sale_line_id: row.line.id,
      position: row.line.position,
      item_id: row.line.item_id,
      lot_id: row.line.lot_id,
      quantity: row.quantity,
      unit_price: row.unit_price,
      discount: row.discount,
      vat_class: row.vat_class,
      tax_amount: fromCents(lineTax(row)),
      unit_cost: row.line.unit_cost,
      from_suggestion: null,
      method: null,
      amount: null,
      reference: null,
    };
    await database.collections.sale_lines!.insert(document);
    await queue(database, "sale_return_lines", {
      id: document.id,
      sale_return_id: header.id,
      sale_line_id: row.line.id,
      quantity: row.quantity,
    });
  }
  return header;
}

// ---------------------------------------------------------------------------
// Stock, prices and the FEFO queue — all local, all at zero latency
// ---------------------------------------------------------------------------

export interface LotOption {
  lot: LotDoc | null;
  quantity: number;
}

/**
 * This sede's lots of one item, **first expired first out**.
 *
 * An undated lot sorts last: an item that tracks no expiry has no place in an
 * ordering by expiry, and putting it first would consume the untracked stock
 * before the dated stock every time.
 *
 * **A lot with nothing on the shelf is offered when nothing else is.** The head
 * of a cashier's queue is no place for a zero, so lots with stock come first —
 * but if every lot is at zero the line takes the earliest-expiring one anyway
 * and S3's ledger raises the negative-stock exception to the office (§5 rule 2,
 * acceptance 10). Returning nothing here instead would leave the line with no
 * lot at all, and the units that left the shelf would never be recorded as a
 * movement: a sale is never refused for want of a lot, and it is never *lost*
 * for want of one either.
 */
export async function lotQueue(
  database: SyncDatabase,
  device: DeviceRecord,
  itemId: string,
): Promise<LotOption[]> {
  const stock = plain<StockDoc>(
    await database.collections
      .stock_on_hand!.find({ selector: { item_id: itemId } })
      .exec(),
  );
  const mine = stock.filter((row) => row.location_id === device.location_id);
  const here = mine.some((row) => row.quantity > 0)
    ? mine.filter((row) => row.quantity > 0)
    : mine;
  const lots = plain<LotDoc>(
    await database.collections
      .lots!.find({ selector: { item_id: itemId } })
      .exec(),
  );
  const byId = new Map(lots.map((lot) => [lot.id, lot]));
  const options = here.map((row) => ({
    lot: row.lot_id ? (byId.get(row.lot_id) ?? null) : null,
    quantity: row.quantity,
  }));
  options.sort((a, b) => {
    const left = a.lot?.expires_at ?? null;
    const right = b.lot?.expires_at ?? null;
    if (left === right)
      return (a.lot?.lot_code ?? "").localeCompare(b.lot?.lot_code ?? "");
    if (left === null) return 1;
    if (right === null) return -1;
    return left.localeCompare(right);
  });
  return options;
}

/**
 * The price this sede charges today, resolved the way the server resolves it.
 *
 * Null means the item has no price in force, which is not zero: a line cannot
 * be added for it, and the surface says so rather than selling something for
 * nothing.
 */
export async function priceOf(
  database: SyncDatabase,
  itemId: string,
): Promise<number | null> {
  const prices = await currentPrices(database, [itemId], businessDay());
  const value = prices.get(itemId);
  return value === undefined ? null : toCents(value);
}

/**
 * §B.8.2 · the `Mostrador` nav counter for a cashier, kept live from the store
 * itself.
 *
 * It subscribes rather than polls: RxDB already tells the tab when the sales
 * store changes, and a nav counter that re-queried on a timer would be a
 * question asked of a local database eight times a minute for a number that
 * moves twice an hour.
 */
export function useOpenTicketCount(enabled: boolean): number {
  const [count, setCount] = useState(0);
  const sync = useSyncContext();
  const database = enabled ? sync.database : null;

  useEffect(() => {
    if (!database) return;
    let stale = false;
    const read = () => {
      void openTicketCount(database).then((next) => {
        if (!stale) setCount(next);
      });
    };
    read();
    const watch = database.collections.sales!.$.subscribe(read);
    return () => {
      stale = true;
      watch.unsubscribe();
    };
  }, [database]);

  return count;
}
