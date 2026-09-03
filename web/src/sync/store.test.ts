import { getRxStorageMemory } from "rxdb/plugins/storage-memory";
import { afterEach, describe, expect, it } from "vitest";
import { openStore, closeStore } from "./store";
import { drain, depth, byKind, queue } from "./outbox";
import {
  currentPrices,
  fold,
  findCustomers,
  queueCountLine,
  queueReceiptLines,
  registerCustomer,
  searchCatalog,
} from "./local";
import type { DeviceRecord } from "./device";
import type { SyncDatabase } from "./store";

/**
 * The local store, its outbox, and the one write a till makes at S2.
 *
 * The memory storage rather than Dexie: what is under test is this stage's own
 * rules — what the outbox keeps, what removes a row from it, and that a
 * customer registered with the network gone is findable immediately. The
 * storage adapter is RxDB's and is exercised by the production build gate.
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

let open: SyncDatabase | null = null;

async function store() {
  open = await openStore(
    `${DEVICE.id}-${Math.random().toString(36).slice(2)}`,
    getRxStorageMemory(),
  );
  return open;
}

afterEach(async () => {
  await closeStore();
  open = null;
});

async function seedCatalog(database: SyncDatabase) {
  await database.collections.items!.bulkInsert([
    {
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      updated_at: "2026-09-01T10:00:00.000000Z",
      name: "Losartán 50 mg × 30",
      search_name: "losartan 50 mg x 30",
      type: "product",
      presentation: "Caja × 30 tabletas",
      active_ingredient: "Losartán potásico",
      strength: "50 mg",
      unit: "tableta",
      units_per_pack: 30,
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
    },
    {
      id: "aaaaaaaa-0000-0000-0000-000000000002",
      updated_at: "2026-09-01T10:00:00.000000Z",
      name: "Acetaminofén 500 mg",
      search_name: "acetaminofen 500 mg",
      type: "product",
      presentation: "Caja × 20 tabletas",
      active_ingredient: "Acetaminofén",
      strength: "500 mg",
      unit: "tableta",
      units_per_pack: 20,
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
    },
  ]);
  await database.collections.item_prices!.bulkInsert([
    {
      id: "bbbbbbbb-0000-0000-0000-000000000001",
      updated_at: "2026-09-01T10:00:00.000000Z",
      item_id: "aaaaaaaa-0000-0000-0000-000000000001",
      location_id: null,
      price: "4200.00",
      effective_from: "2026-01-01",
      effective_to: null,
    },
    {
      id: "bbbbbbbb-0000-0000-0000-000000000002",
      updated_at: "2026-09-01T10:00:00.000000Z",
      item_id: "aaaaaaaa-0000-0000-0000-000000000001",
      location_id: DEVICE.location_id,
      price: "3900.00",
      effective_from: "2026-02-01",
      effective_to: null,
    },
  ]);
}

describe("the catalog, offline", () => {
  it("folds accents the way the database's generated column does", () => {
    // `losartan` finds `Losartán 50 mg × 30`, which is what a cashier types.
    expect(fold("Losartán")).toBe("losartan");
    expect(fold("  ACETAMINOFÉN ")).toBe("acetaminofen");
  });

  it("finds a product by an accent-free prefix with no network in the path", async () => {
    const database = await store();
    await seedCatalog(database);
    const hits = await searchCatalog(database, "losartan");
    expect(hits.map((one) => one.name)).toEqual(["Losartán 50 mg × 30"]);
  });

  it("resolves this sede's price over the network-wide one", async () => {
    // The device only ever holds its own sede's rows and the network's, because
    // that is the registry's predicate — so no other sede's price can be in the
    // running at all.
    const database = await store();
    await seedCatalog(database);
    const [hit] = await searchCatalog(database, "losartan");
    expect(hit!.price).toBe("3900.00");
  });

  it("resolves a dated repricing by its window and not by uuid order", async () => {
    // A till legitimately holds both rows the day a repricing is written. A
    // resolver that ignored the window would charge tomorrow's price today, or
    // yesterday's tomorrow, depending on which uuid RxDB returned first.
    const database = await store();
    await seedCatalog(database);
    await database.collections.item_prices!.insert({
      id: "bbbbbbbb-0000-0000-0000-000000000000",
      updated_at: "2026-09-01T10:00:00.000000Z",
      item_id: "aaaaaaaa-0000-0000-0000-000000000001",
      location_id: DEVICE.location_id,
      price: "4500.00",
      effective_from: "2026-10-01",
      effective_to: null,
    });

    const before = await currentPrices(
      database,
      ["aaaaaaaa-0000-0000-0000-000000000001"],
      "2026-09-15",
    );
    expect(before.get("aaaaaaaa-0000-0000-0000-000000000001")).toBe("3900.00");

    const after = await currentPrices(
      database,
      ["aaaaaaaa-0000-0000-0000-000000000001"],
      "2026-10-02",
    );
    expect(after.get("aaaaaaaa-0000-0000-0000-000000000001")).toBe("4500.00");
  });

  it("renders a product with no price as unknown rather than as zero", async () => {
    // §B.9.2 tier 3 · a zero standing in for "we don't know" is the single most
    // expensive lie an inventory system can tell.
    const database = await store();
    await seedCatalog(database);
    const [hit] = await searchCatalog(database, "acetaminofen");
    expect(hit!.price).toBeNull();
  });
});

describe("registering a customer offline", () => {
  it("writes the local row and the outbox row in the same breath", async () => {
    // Criterion 8 · the status line reads `Sin conexión · 1 por enviar` and the
    // customer is immediately findable by name and by document.
    const database = await store();
    await registerCustomer(database, {
      document_type: "CC",
      document: "1020304050",
      name: "Ana Gómez",
    });

    expect(await depth(database)).toBe(1);
    expect(await byKind(database)).toEqual({ customers: 1 });
    expect(
      (await findCustomers(database, "1020304050")).map((one) => one.name),
    ).toEqual(["Ana Gómez"]);
    expect(
      (await findCustomers(database, "ana")).map((one) => one.document),
    ).toEqual(["1020304050"]);
  });

  it("keeps the row when the push never reaches the server", async () => {
    // **Nothing unsent is ever dropped for failing to send.**
    const database = await store();
    await registerCustomer(database, {
      document_type: "CC",
      document: "1020304050",
      name: "Ana Gómez",
    });
    const original = globalThis.fetch;
    globalThis.fetch = () => Promise.reject(new Error("offline"));
    await expect(drain(database, DEVICE, 200)).rejects.toThrow();
    globalThis.fetch = original;

    expect(await depth(database)).toBe(1);
    const [row] = await database.collections.outbox!.find().exec();
    expect(row!.get("attempts")).toBe(1);
  });
});

describe("the outbox drains on every outcome that is not a transport failure", () => {
  async function pushReturning(
    outcome: "applied" | "duplicate" | "merged" | "rejected",
    id: string | null,
  ) {
    const database = await store();
    const row = await queue(database, "customers", {
      id: "cccccccc-0000-0000-0000-000000000001",
      document_type: "CC",
      document: "1020304050",
      name: "Ana Gómez",
    });
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            batch_id: row.client_uuid,
            batch_outcome: "applied",
            batch_reason: "",
            server_time: new Date().toISOString(),
            results: [
              { client_uuid: row.client_uuid, outcome, id, reason: "" },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    const report = await drain(database, DEVICE, 200);
    return { database, report };
  }

  const original = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = original;
  });

  it("removes the row on `applied`", async () => {
    const { database, report } = await pushReturning("applied", "server-id");
    expect(report.applied).toBe(1);
    expect(await depth(database)).toBe(0);
  });

  it("removes the row on `duplicate`, because duplicate is a success", async () => {
    // A client that treats `duplicate` as an error can never drain a queue
    // whose push timed out after the server committed — the exact failure A5
    // exists to make safe.
    const { database, report } = await pushReturning("duplicate", "server-id");
    expect(report.duplicate).toBe(1);
    expect(await depth(database)).toBe(0);
  });

  it("reports the id a `merged` row must adopt", async () => {
    const { database, report } = await pushReturning("merged", "server-id");
    expect(report.merges).toEqual([
      {
        collection: "customers",
        from: "cccccccc-0000-0000-0000-000000000001",
        to: "server-id",
      },
    ]);
    expect(await depth(database)).toBe(0);
  });

  it("keeps every row when the whole batch was refused", async () => {
    // A foreign-tenant or foreign-location batch applies **no** row, so
    // removing them would delete writes that were never made. The till goes
    // `degraded`, the office sees the conflict and re-claims or revokes the
    // device, and a re-claim drains the outbox first — so nothing is lost.
    const database = await store();
    const row = await queue(database, "customers", {
      id: "cccccccc-0000-0000-0000-000000000009",
      document_type: "CC",
      document: "1020304050",
      name: "Ana Gómez",
    });
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            batch_id: row.client_uuid,
            batch_outcome: "rejected",
            batch_reason: "Este envío trae filas de otra sede.",
            server_time: new Date().toISOString(),
            results: [
              {
                client_uuid: row.client_uuid,
                outcome: "rejected",
                id: null,
                reason: "Este envío trae filas de otra sede.",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    const report = await drain(database, DEVICE, 200);
    expect(report.batchRejected).toBe(true);
    expect(await depth(database)).toBe(1);
    const [held] = await database.collections.outbox!.find().exec();
    expect(held!.get("attempts")).toBe(1);
  });

  it("removes the row on `rejected` rather than retrying it forever", async () => {
    // A row retried forever is a `degraded` state that never clears.
    const { database, report } = await pushReturning("rejected", null);
    expect(report.rejected).toBe(1);
    expect(await depth(database)).toBe(0);
  });
});

describe("S3's two pushable documents, queued offline", () => {
  /**
   * The two halves the surfaces owe the outbox, and neither is visible from
   * reading the component: an entry's lines have to share **one** document so
   * the moves the push writes hang off one `receipts` document, and a count
   * line has to reach the outbox at all — a collection nothing ever queues is
   * dead weight and a `Conteos` label that can never show a number.
   */
  it("gives every line of one entry the same document and its own key", async () => {
    const database = await store();
    const documentId = "dddddddd-0000-0000-0000-000000000001";
    await queueReceiptLines(database, documentId, [
      {
        item_id: "aaaaaaaa-0000-0000-0000-000000000001",
        lot_code: "A-2291",
        expires_at: "2027-03-31",
        quantity: 360,
        unit_cost: "1200.00",
      },
      {
        item_id: "aaaaaaaa-0000-0000-0000-000000000002",
        lot_code: "B-1180",
        expires_at: "2027-06-30",
        quantity: 40,
        unit_cost: "900.00",
      },
    ]);

    const rows = await database.collections.outbox!.find().exec();
    expect(rows).toHaveLength(2);
    const payloads = rows.map(
      (one) => one.get("payload") as { document_id: string },
    );
    expect(new Set(payloads.map((one) => one.document_id)).size).toBe(1);
    expect(payloads[0]!.document_id).toBe(documentId);
    // A5 · one key per line, and it is the outbox row's own — the value the
    // server dedupes on is the value the outbox retries under.
    expect(new Set(rows.map((one) => one.get("client_uuid"))).size).toBe(2);
    expect(await byKind(database)).toMatchObject({ receipt_lines: 2 });
  });

  it("queues a counted line under the collection the registry declares", async () => {
    const database = await store();
    await queueCountLine(database, {
      count_id: "cccccccc-0000-0000-0000-000000000001",
      item_id: "aaaaaaaa-0000-0000-0000-000000000001",
      lot_id: null,
      counted_quantity: 48,
    });
    expect(await byKind(database)).toMatchObject({ stock_count_lines: 1 });
  });
});
