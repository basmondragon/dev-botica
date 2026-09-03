import { getRxStorageMemory } from "rxdb/plugins/storage-memory";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { DeviceRecord } from "@/sync/device";
import { byKind, depth } from "@/sync/outbox";
import type { SyncDatabase } from "@/sync/store";
import { closeStore, openStore } from "@/sync/store";
import { fromCents, toCents, totals } from "./money";
import * as till from "./local";

/**
 * The till's own store, with the network gone.
 *
 * **Every check here runs with no server at all**, which is the point: a
 * keystroke, a scan, a quantity, a payment and a receipt are local reads and
 * local writes, and the outbox is the only durable record of what has not been
 * sent. The memory storage rather than Dexie, for the reason S2's own store
 * test gives: what is under test is this stage's rules, and the adapter is
 * exercised by the production-build gate.
 */

const DEVICE: DeviceRecord = {
  id: "11111111-1111-1111-1111-111111111111",
  key: "bkd_test",
  label: "Caja 1",
  code: "C1",
  location_id: "22222222-2222-2222-2222-222222222222",
  location_name: "Chapinero",
  location_code: "CHA",
  persisted: true,
  persistence_dialog_seen: true,
};

const USER = {
  id: "33333333-3333-3333-3333-333333333333",
  name: "Andrés Peña",
};

const ITEM = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  updated_at: "2026-09-01T10:00:00.000000Z",
  name: "Acetaminofén 500 mg × 100",
  search_name: "acetaminofen 500 mg x 100",
  type: "product",
  presentation: "Caja × 100",
  active_ingredient: "Acetaminofén",
  strength: "500 mg",
  unit: "tableta",
  units_per_pack: 100,
  splittable: true,
  vat_class: "excluded",
  manufacturer_id: null,
  category_id: null,
  requires_prescription: false,
  controlled: false,
  cold_chain: false,
  tracks_stock: true,
  tracks_lots: true,
  tracks_expiry: true,
  invima_status: "valid",
};

const LOTS = [
  {
    id: "bbbbbbbb-0000-0000-0000-000000000001",
    updated_at: "2026-09-01T10:00:00.000000Z",
    item_id: ITEM.id,
    lot_code: "A-2291",
    expires_at: "2027-03-01",
    unit_cost: "1200.00",
  },
  {
    id: "bbbbbbbb-0000-0000-0000-000000000002",
    updated_at: "2026-09-01T10:00:00.000000Z",
    item_id: ITEM.id,
    lot_code: "A-2292",
    expires_at: "2028-01-01",
    unit_cost: "1250.00",
  },
];

let open: SyncDatabase | null = null;

async function store() {
  open = await openStore(
    `${DEVICE.id}-${Math.random().toString(36).slice(2)}`,
    getRxStorageMemory(),
  );
  await open.collections.items!.insert(ITEM);
  await open.collections.lots!.bulkInsert(LOTS);
  await open.collections.item_prices!.insert({
    id: "cccccccc-0000-0000-0000-000000000001",
    updated_at: "2026-09-01T10:00:00.000000Z",
    item_id: ITEM.id,
    location_id: null,
    price: "3900.00",
    effective_from: "2026-01-01",
    effective_to: null,
  });
  await open.collections.stock_on_hand!.bulkInsert([
    {
      id: "dddddddd-0000-0000-0000-000000000001",
      updated_at: "2026-09-01T10:00:00.000000Z",
      location_id: DEVICE.location_id,
      item_id: ITEM.id,
      lot_id: LOTS[1]!.id,
      quantity: 40,
    },
    {
      id: "dddddddd-0000-0000-0000-000000000002",
      updated_at: "2026-09-01T10:00:00.000000Z",
      location_id: DEVICE.location_id,
      item_id: ITEM.id,
      lot_id: LOTS[0]!.id,
      quantity: 25,
    },
  ]);
  return open;
}

async function sell(database: SyncDatabase, quantity = 4) {
  const shift = await till.startShift(database, DEVICE, USER, 15_000_000);
  const sale = await till.startTicket(database, DEVICE, shift, USER);
  const queue = await till.lotQueue(database, DEVICE, ITEM.id);
  const price = await till.priceOf(database, ITEM.id);
  await till.addLine(
    database,
    { sale, lines: [] },
    {
      item: ITEM,
      quantity,
      lot: queue[0]?.lot ?? null,
      unitPrice: price!,
      unitCost: queue[0]?.lot?.unit_cost ?? null,
      fefoOverride: false,
    },
  );
  return { shift, ticket: (await till.readTicket(database, sale.id))! };
}

beforeEach(() => {
  // A fresh browser per check: the till keeps its ticket pointer and its number
  // sequence in `localStorage`, and a counter carried between checks is a
  // second till's history leaking into this one's.
  window.localStorage.clear();
});

afterEach(async () => {
  await closeStore();
  open = null;
  window.localStorage.clear();
});

describe("the turno", () => {
  it("opens locally and queues itself, with no network in the path", async () => {
    const database = await store();
    const shift = await till.startShift(database, DEVICE, USER, 15_000_000);
    expect(shift.status).toBe("open");
    expect(await till.openShift(database, DEVICE)).not.toBeNull();
    expect(await byKind(database)).toEqual({ shifts: 1 });
  });

  it("is this till's drawer and never the one next to it", async () => {
    // The collection is scoped by sede, so a till holds its neighbours' turnos
    // too. A query that asked only for an open one would hand the second till
    // the first till's drawer, and its `Cerrar turno` would count somebody
    // else's cash.
    const database = await store();
    await database.collections.shifts!.insert({
      id: "ffffffff-0000-0000-0000-000000000001",
      updated_at: "2026-09-01T10:00:00.000000Z",
      location_id: DEVICE.location_id,
      device_id: "99999999-9999-9999-9999-999999999999",
      user_id: null,
      user_name: "Otra cajera",
      opened_at: "2026-09-01T08:30:00.000000Z",
      closed_at: null,
      opening_float: "150000.00",
      declared_total: null,
      variance: null,
      status: "open",
    });
    expect(await till.openShift(database, DEVICE)).toBeNull();

    const mine = await till.startShift(database, DEVICE, USER, 15_000_000);
    expect((await till.openShift(database, DEVICE))?.id).toBe(mine.id);
  });

  it("closes with the difference it actually found and never a zero", async () => {
    const database = await store();
    const { shift } = await sell(database);
    const ticket = (await till.readTicket(database, till.heldTicketId()!))!;
    await till.commit(
      database,
      ticket,
      [{ method: "cash", amount: 1_560_000, reference: "" }],
      null,
    );

    // $150.000 float + $15.600 taken = $165.600 expected; the drawer holds
    // $162.600, so the variance is −$3.000 and it is **stored as it stands**.
    const closed = await till.closeShift(database, shift, 16_260_000);
    expect(closed.variance).toBe("-3000.00");
    expect(closed.status).toBe("closed");
  });
});

describe("the ticket", () => {
  it("takes the earliest-expiring lot without the cashier choosing", async () => {
    const database = await store();
    const queue = await till.lotQueue(database, DEVICE, ITEM.id);
    // FEFO: `A-2291` expires in March 2027 and `A-2292` in January 2028, and
    // the March lot is what the line takes even though the store returned the
    // January one first.
    expect(queue.map((one) => one.lot?.lot_code)).toEqual(["A-2291", "A-2292"]);
  });

  it("steps the quantity rather than adding a second row for one product", async () => {
    const database = await store();
    const { ticket } = await sell(database, 1);
    const price = await till.priceOf(database, ITEM.id);
    const queue = await till.lotQueue(database, DEVICE, ITEM.id);
    await till.addLine(database, ticket, {
      item: ITEM,
      quantity: 1,
      lot: queue[0]?.lot ?? null,
      unitPrice: price!,
      unitCost: null,
      fefoOverride: false,
    });
    const again = (await till.readTicket(database, ticket.sale.id))!;
    expect(again.lines).toHaveLength(1);
    expect(again.lines[0]!.quantity).toBe(2);
  });

  it("survives a relaunch, because the pointer and the rows both persist", async () => {
    const database = await store();
    const { ticket } = await sell(database, 2);
    // What a relaunch does: nothing is in memory, and the ticket is found from
    // the pointer alone.
    const found = await till.readTicket(database, till.heldTicketId()!);
    expect(found?.sale.id).toBe(ticket.sale.id);
    expect(found?.lines).toHaveLength(1);
  });

  it("closes as one queued document: the header, its lines and its payments", async () => {
    const database = await store();
    const { ticket } = await sell(database, 4);
    const closed = await till.commit(
      database,
      ticket,
      [
        { method: "cash", amount: 1_000_000, reference: "" },
        { method: "debit_card", amount: 560_000, reference: "REF1" },
      ],
      null,
    );
    expect(closed.sale.status).toBe("closed");
    expect(closed.sale.total).toBe("15600.00");
    expect(till.heldTicketId()).toBeNull();

    // The turno's open event, the sale's open event, the sale's close event,
    // its one line and its two payments.
    expect(await depth(database)).toBe(6);
    expect(await byKind(database)).toEqual({
      shifts: 1,
      sales: 2,
      sale_lines: 1,
      payments: 2,
    });
  });

  it("names the acquirer by document as well as by id", async () => {
    // **The natural key is what makes an offline registration safe**: a customer
    // that merged onto an existing row during the same blackout keeps a
    // different id, and the sale still names the person (rule 8).
    const database = await store();
    const { ticket } = await sell(database, 1);
    await till.commit(
      database,
      ticket,
      [{ method: "cash", amount: 390_000, reference: "" }],
      {
        id: "eeeeeeee-0000-0000-0000-000000000001",
        document_type: "CC",
        document: "1020304050",
      },
    );
    const rows = await database.collections.outbox!.find().exec();
    const close = rows
      .map(
        (one) =>
          one.toJSON() as { kind: string; payload: Record<string, unknown> },
      )
      .find((one) => one.kind === "sales" && one.payload.status === "closed");
    expect(close?.payload.customer_document).toBe("1020304050");
    expect(close?.payload.customer_document_type).toBe("CC");
  });
});

describe("what the counter reads back", () => {
  it("withholds the average-ticket note below twenty tickets", async () => {
    // §B.9.2 tier 3 · **never a zero**: a zero standing in for "we do not know"
    // is the most expensive lie an inventory system tells.
    const database = await store();
    const { ticket } = await sell(database, 1);
    await till.commit(
      database,
      ticket,
      [{ method: "cash", amount: 390_000, reference: "" }],
      null,
    );
    expect(await till.averageTicket(database)).toBeNull();
  });

  it("counts the tickets in progress for the nav", async () => {
    const database = await store();
    await sell(database, 1);
    expect(await till.openTicketCount(database)).toBe(1);
  });

  it("caps a return at what remains on the line", async () => {
    const database = await store();
    const { shift, ticket } = await sell(database, 4);
    const closed = await till.commit(
      database,
      ticket,
      [{ method: "cash", amount: 1_560_000, reference: "" }],
      null,
    );
    expect(await till.returnable(database, closed)).toEqual({
      [closed.lines[0]!.id]: 4,
    });

    await till.registerReturn(
      database,
      DEVICE,
      shift,
      USER,
      closed,
      [{ lineId: closed.lines[0]!.id, quantity: 2 }],
      { reason: "Caja sin abrir", refundMethod: "cash" },
    );
    expect(await till.returnable(database, closed)).toEqual({
      [closed.lines[0]!.id]: 2,
    });
  });

  it("refunds the money that was charged, not today's price", async () => {
    const database = await store();
    const { shift, ticket } = await sell(database, 4);
    const closed = await till.commit(
      database,
      ticket,
      [{ method: "cash", amount: 1_560_000, reference: "" }],
      null,
    );
    // The price list moves between the sale and the return.
    const price = await database.collections.item_prices!.findOne().exec();
    await price?.incrementalPatch({ price: "9900.00" });

    const header = await till.registerReturn(
      database,
      DEVICE,
      shift,
      USER,
      closed,
      [{ lineId: closed.lines[0]!.id, quantity: 2 }],
      { reason: "Caja sin abrir", refundMethod: "cash" },
    );
    expect(header.total).toBe("7800.00");
    expect(header.number).toBe("C1D-1");
  });
});

describe("the number a till allocates with no connection", () => {
  it("is composed from the till's own code and never collides", async () => {
    const database = await store();
    const shift = await till.startShift(database, DEVICE, USER, 0);
    const first = await till.startTicket(database, DEVICE, shift, USER);
    expect(first.number).toBe("C1-1");
    // **It survives the retention window the local store does not.** A till
    // idle longer than seven days finds no sales left to derive from, and a
    // counter that restarted at 1 there would collide with its own history the
    // moment the push landed.
    const rows = await database.collections
      .sales!.find({ selector: { kind: "sale" } })
      .exec();
    await database.collections.sales!.bulkRemove(
      rows.map((one) => one.primary),
    );
    expect(await till.nextSaleNumber(database, DEVICE)).toBe("C1-2");
    // The two sequences are independent: `C1-4821` numbers a sale and `C1D-12`
    // a devolución, and each is unique within its location on its own.
    expect(await till.nextReturnNumber(database, DEVICE)).toBe("C1D-1");
    expect(await till.nextReturnNumber(database, DEVICE)).toBe("C1D-2");
  });
});

describe("the arithmetic the panel renders", () => {
  it("agrees with the money module on a real ticket", async () => {
    const database = await store();
    const { ticket } = await sell(database, 4);
    const figures = totals(
      ticket.lines.map((line) => ({
        quantity: line.quantity ?? 0,
        unit_price: line.unit_price,
        discount: line.discount,
        vat_class: line.vat_class,
      })),
    );
    expect(fromCents(figures.total)).toBe("15600.00");
    expect(toCents(ticket.lines[0]!.unit_price)).toBe(390_000);
  });
});

describe("the cashier's own void", () => {
  it("is available inside their own turno and gone once something is returned", async () => {
    const database = await store();
    const { shift, ticket } = await sell(database, 4);
    const closed = await till.commit(
      database,
      ticket,
      [{ method: "cash", amount: 1_560_000, reference: "" }],
      null,
    );
    expect(await till.voidableHere(database, closed, shift)).toBe(true);

    await till.registerReturn(
      database,
      DEVICE,
      shift,
      USER,
      closed,
      [{ lineId: closed.lines[0]!.id, quantity: 1 }],
      { reason: "Caja sin abrir", refundMethod: "cash" },
    );
    // A mis-key is corrected by a void; a ticket somebody has already returned
    // against is corrected by another return.
    expect(await till.voidableHere(database, closed, shift)).toBe(false);
  });

  it("queues the void as an event and deletes nothing", async () => {
    const database = await store();
    const { ticket } = await sell(database, 2);
    const closed = await till.commit(
      database,
      ticket,
      [{ method: "cash", amount: 780_000, reference: "" }],
      null,
    );
    await till.voidSale(database, closed.sale, "Tiquete mal digitado");

    const again = await till.readTicket(database, closed.sale.id);
    expect(again?.sale.status).toBe("voided");
    expect(again?.lines).toHaveLength(1);
    const rows = await database.collections.outbox!.find().exec();
    const events = rows
      .map(
        (one) =>
          one.toJSON() as { kind: string; payload: Record<string, unknown> },
      )
      .filter((one) => one.kind === "sales")
      .map((one) => one.payload.status);
    expect(events).toEqual(["open", "closed", "voided"]);
  });
});

describe("a shelf at zero", () => {
  it("still names the lot the units left on", async () => {
    // §5 rule 2, acceptance 10 · a sale is never refused for want of a lot, and
    // it is never *lost* for want of one either: the line takes the
    // earliest-expiring lot anyway and S3's ledger raises the negative-stock
    // exception when the push lands. Returning nothing here would leave the
    // line with `lot_id = null`, and the units that left the shelf would never
    // be recorded as a movement at all.
    const database = await store();
    const rows = await database.collections.stock_on_hand!.find().exec();
    for (const row of rows) await row.incrementalPatch({ quantity: 0 });

    const queue = await till.lotQueue(database, DEVICE, ITEM.id);
    expect(queue).toHaveLength(2);
    expect(queue[0]!.lot?.lot_code).toBe("A-2291");
    expect(queue[0]!.quantity).toBe(0);
  });

  it("still prefers a lot that has stock when one does", async () => {
    const database = await store();
    const rows = await database.collections.stock_on_hand!.find().exec();
    // Empty the March lot; the January one still holds forty.
    for (const row of rows) {
      if ((row.toJSON() as { lot_id: string }).lot_id === LOTS[0]!.id)
        await row.incrementalPatch({ quantity: 0 });
    }
    const queue = await till.lotQueue(database, DEVICE, ITEM.id);
    expect(queue.map((one) => one.lot?.lot_code)).toEqual(["A-2292"]);
  });
});
