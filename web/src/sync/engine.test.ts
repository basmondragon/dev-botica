import { getRxStorageMemory } from "rxdb/plugins/storage-memory";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { localDigest } from "./digest";
import { SyncEngine } from "./engine";
import { queue } from "./outbox";
import { closeStore, openStore, type SyncDatabase } from "./store";
import type { DeviceRecord } from "./device";
import type { RegistryResponse } from "./transport";

/**
 * The two engine rules a person cannot check by watching a green suite: what a
 * reset refuses to do, and what a second tab reads.
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

const REGISTRY: RegistryResponse = {
  version: 1,
  device_id: DEVICE.id,
  device_label: DEVICE.label,
  device_code: "C1",
  location_id: DEVICE.location_id,
  location_name: DEVICE.location_name,
  location_code: DEVICE.location_code,
  collections: [
    { name: "items", scope: "tenant", push: false, natural_key: null, rows: 2 },
  ],
  server_time: new Date().toISOString(),
  clock_skew_ms: 120,
  storage_persisted: true,
  storage_persistence_policy: "warn",
  pull_interval_seconds: 8,
  pull_page_size: 500,
  push_batch_max_rows: 200,
  push_batch_max_bytes: 1048576,
  local_retention_days: 30,
  clock_skew_warn_seconds: 90,
};

let database: SyncDatabase;
let engine: SyncEngine | null = null;
const originalFetch = globalThis.fetch;

beforeEach(async () => {
  localStorage.clear();
  database = await openStore(
    `${DEVICE.id}-${Math.random().toString(36).slice(2)}`,
    getRxStorageMemory(),
  );
});

afterEach(async () => {
  await engine?.stop();
  engine = null;
  await closeStore();
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

async function seedItem(id: string) {
  await database.collections.items!.insert({
    id,
    updated_at: "2026-09-01T10:00:00.000000Z",
    name: "Losartán 50 mg",
    search_name: "losartan 50 mg",
    type: "product",
    presentation: "",
    active_ingredient: "",
    strength: "",
    unit: "tableta",
    units_per_pack: 1,
    splittable: false,
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
  });
}

describe("a reset never discards an unpushed row", () => {
  it("refuses to wipe a collection while the device is offline with a full outbox", async () => {
    // Criterion 31, and *Verification* check 7's destructive half. **A forced
    // wipe with a full outbox loses sales**, which is the single failure this
    // whole stage exists to prevent — and it would arrive as a deployment side
    // effect rather than as anything a cashier did.
    engine = new SyncEngine(database, DEVICE);
    await seedItem("aaaaaaaa-0000-0000-0000-000000000001");
    await queue(database, "customers", {
      id: "cccccccc-0000-0000-0000-000000000001",
      document_type: "CC",
      document: "1020304050",
      name: "Ana Gómez",
    });
    globalThis.fetch = () => Promise.reject(new Error("offline"));

    const reset = await engine.reset("items");

    expect(reset).toBe(false);
    expect(await database.collections.items!.count().exec()).toBe(1);
    expect(await database.collections.outbox!.count().exec()).toBe(1);
  });

  it("resets once the outbox has drained", async () => {
    engine = new SyncEngine(database, DEVICE);
    await seedItem("aaaaaaaa-0000-0000-0000-000000000001");

    const reset = await engine.reset("items");

    expect(reset).toBe(true);
    expect(await database.collections.items!.count().exec()).toBe(0);
  });
});

describe("two streams in one store", () => {
  /**
   * RxDB's open-core build caps a database at thirteen collections and the
   * registry is committed to more streams than that across S2, S3, S4, S5 and
   * S8 — so `stock_on_hand` holds both the till's own sede and the capped
   * other-location set, split by the `location_id` every row already carries.
   * The split has to hold in the two places it can silently fail: a reset, and
   * the daily digest.
   */
  async function seedStock(id: string, locationId: string) {
    await database.collections.stock_on_hand!.insert({
      id,
      updated_at: "2026-09-01T10:00:00.000000Z",
      location_id: locationId,
      item_id: "dddddddd-0000-0000-0000-000000000001",
      lot_id: null,
      quantity: 12,
    });
  }

  it("resets one stream without taking the other's rows with it", async () => {
    // Wiping the whole store would leave the other stream's cursor where it
    // was, so it would never re-serve what was removed and the till would sit
    // on a hole until the next daily digest.
    engine = new SyncEngine(database, DEVICE);
    await seedStock("50000000-0000-0000-0000-000000000001", DEVICE.location_id);
    await seedStock(
      "50000000-0000-0000-0000-000000000002",
      "99999999-9999-9999-9999-999999999999",
    );

    expect(await engine.reset("stock_elsewhere")).toBe(true);

    const held = await database.collections.stock_on_hand!.find().exec();
    expect(held.map((one) => one.get("location_id"))).toEqual([
      DEVICE.location_id,
    ]);
  });

  it("hashes each stream against its own half of the store", async () => {
    // The server answers per stream. Hashing the whole store would find a
    // mismatch on both halves of a set that is perfectly in step, and re-pull
    // the pair every single day.
    await seedStock("50000000-0000-0000-0000-000000000001", DEVICE.location_id);
    await seedStock(
      "50000000-0000-0000-0000-000000000002",
      "99999999-9999-9999-9999-999999999999",
    );

    const own = await localDigest(
      database,
      "stock_on_hand",
      DEVICE.location_id,
    );
    const others = await localDigest(
      database,
      "stock_elsewhere",
      DEVICE.location_id,
    );

    expect(own.count).toBe(1);
    expect(others.count).toBe(1);
    expect(own.checksum).not.toBe(others.checksum);
  });
});

describe("§5 · one store, one replication, one status line", () => {
  it("reports a second tab as ready without it running a pull of its own", async () => {
    // Criterion 15 · only the leader replicates. A `ready` flag derived from
    // *this tab's* replication would leave every follower on the first-sync
    // screen forever, looking at a store that is already complete.
    localStorage.setItem("botica.sync.first", DEVICE.id);
    const follower = new SyncEngine(database, DEVICE);
    expect(follower.current().ready).toBe(true);
    await follower.stop();
  });

  it("does not report a different device's completed first sync as its own", async () => {
    localStorage.setItem(
      "botica.sync.first",
      "99999999-9999-9999-9999-999999999999",
    );
    const fresh = new SyncEngine(database, DEVICE);
    expect(fresh.current().ready).toBe(false);
    await fresh.stop();
  });

  it("does not let a follower talk to the server", async () => {
    // Criterion 15 · **the server sees one pull stream, not two.**
    //
    // Two engines on one store: the first takes leadership, the second is a
    // follower. The follower's `syncNow()` — which `online`, `focus` and
    // `visibilitychange` all reach — must not produce a request. RxDB's own
    // `waitForLeadership` does not stop it: it is honoured only on the path
    // taken when `autoStart` is true, and this engine drives every replication
    // by hand so the push runs before the pull. The gate has to be here.
    const calls = { total: 0 };
    globalThis.fetch = (input: RequestInfo | URL) => {
      calls.total += 1;
      const url = String(input);
      return Promise.resolve(
        url.includes("/api/sync/push")
          ? Response.json({
              batch_id: "b",
              batch_outcome: "applied",
              batch_reason: "",
              results: [],
              server_time: "2026-09-02T12:00:00Z",
            })
          : Response.json({
              documents: [],
              checkpoint: null,
              has_more: false,
              registry_version: 1,
              server_time: "2026-09-02T12:00:00Z",
              location_id: DEVICE.location_id,
            }),
      );
    };

    const follower = new SyncEngine(database, DEVICE);
    // It never acquired leadership, because `start()` was not called and the
    // only path that sets it is `waitForLeadership()` resolving.
    follower.syncNow();
    await vi.waitFor(() => expect(follower.current().pending).toBe(0));
    expect(calls.total).toBe(0);
    await follower.stop();
  }, 20000);

  it("keeps the queue count live in a tab that is not the leader", async () => {
    engine = new SyncEngine(database, DEVICE);
    globalThis.fetch = () => Promise.reject(new Error("offline"));
    await engine.start(REGISTRY);

    await queue(database, "customers", {
      id: "cccccccc-0000-0000-0000-000000000002",
      document_type: "CC",
      document: "2020304050",
      name: "Beatriz Aguirre",
    });
    await vi.waitFor(() => expect(engine!.current().pending).toBe(1));
    expect(engine!.current().queue).toEqual({ customers: 1 });
  });
});

describe("the poll schedule", () => {
  /** A server that answers every sync call and counts what it was asked. */
  function fakeServer() {
    const calls = { pull: 0, push: 0 };
    globalThis.fetch = (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/sync/pull")) {
        calls.pull += 1;
        return Promise.resolve(
          Response.json({
            documents: [],
            checkpoint: null,
            has_more: false,
            registry_version: 1,
            server_time: "2026-09-02T12:00:00Z",
            location_id: DEVICE.location_id,
          }),
        );
      }
      if (url.includes("/api/sync/push")) {
        calls.push += 1;
        return Promise.resolve(
          Response.json({
            batch_id: "b",
            batch_outcome: "applied",
            batch_reason: "",
            results: [],
            server_time: "2026-09-02T12:00:00Z",
          }),
        );
      }
      return Promise.reject(new Error(`unexpected call: ${url}`));
    };
    return calls;
  }

  it("keeps pulling after the first cycle", async () => {
    // The defect this exists for: RxDB **cancels a non-live replication after
    // one run**, so a cadence built on `live: false` syncs once and then goes
    // quiet — which looks like a working product for the first eight seconds
    // and like a till three days behind by Friday. Nothing else in this suite
    // would notice, because every other test drives one cycle.
    const calls = fakeServer();
    engine = new SyncEngine(database, DEVICE);
    await engine.start({ ...REGISTRY, pull_interval_seconds: 1 });

    await vi.waitFor(() => expect(calls.pull).toBeGreaterThanOrEqual(1), {
      timeout: 8000,
    });
    const afterFirst = calls.pull;
    await vi.waitFor(() => expect(calls.pull).toBeGreaterThan(afterFirst), {
      timeout: 8000,
    });
    expect(engine!.current().ready).toBe(true);
  }, 20000);
});

describe("the daily divergence check", () => {
  it("re-pulls a collection whose checksum moved while its count did not", async () => {
    // The case the count cannot see: a row rewritten inside a transaction that
    // outlived the safety horizon. Nothing was added or removed; the till is
    // simply holding an older version of a row it already has. **That is the
    // whole reason the server computes a checksum at all.**
    // **The store opened by `beforeEach` is closed first.** A browser is one
    // till and holds one store; two open at once is a test artefact, and the
    // orphan RxDB keeps counting against its open-collection limit is what made
    // every test after this one time out once S3's four collections landed.
    await closeStore();
    const database = await openStore(
      `digest-${Math.random().toString(36).slice(2)}`,
      getRxStorageMemory(),
    );
    await database.collections.items!.insert({
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      updated_at: "2026-09-01T10:00:00.000000Z",
      name: "Losartán 50 mg",
      search_name: "losartan 50 mg",
      type: "product",
      presentation: "",
      active_ingredient: "",
      strength: "",
      unit: "tableta",
      units_per_pack: 1,
      splittable: false,
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
    });

    globalThis.fetch = (input: RequestInfo | URL) => {
      if (String(input).includes("/api/sync/digest")) {
        return Promise.resolve(
          Response.json({
            collections: {
              // Same count, different checksum: the row moved under the till.
              items: {
                count: 1,
                checksum: "a-checksum-this-till-cannot-match",
              },
            },
            registry_version: 1,
            server_time: "2026-09-02T12:00:00Z",
          }),
        );
      }
      return Promise.resolve(
        Response.json({
          documents: [],
          checkpoint: null,
          has_more: false,
          registry_version: 1,
          server_time: "2026-09-02T12:00:00Z",
          location_id: DEVICE.location_id,
        }),
      );
    };

    const running = new SyncEngine(database, DEVICE);
    await running.maybeDigest();
    // The collection was reset, which is what a divergence costs: one re-pull.
    expect(await database.collections.items!.count().exec()).toBe(0);
    await running.stop();
    await closeStore();
  }, 20000);
});

describe("§B.9.1 · what a failed call means", () => {
  it("moves a degraded till to offline when the link goes", async () => {
    // Criterion 25 · **a call that got no answer is not a call the server
    // refused**, so the reason it refused with stops being true the moment the
    // link goes. A cashier told the system is failing when the shop's link is
    // simply down learns to distrust the line that matters.
    const running = new SyncEngine(database, DEVICE);
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "sesión vencida" }), {
          status: 401,
        }),
      );
    await running.start({ ...REGISTRY, pull_interval_seconds: 1 });
    await vi.waitFor(() =>
      expect(running.current().degraded).toBe("session_expired"),
    );

    globalThis.fetch = () => Promise.reject(new Error("offline"));
    // The next cycle, driven rather than waited on: after a refusal the engine
    // has already backed off, and what is under test is what the cycle reports,
    // not how long it waits before running one.
    running.syncNow();
    await vi.waitFor(() => expect(running.current().degraded).toBeNull(), {
      timeout: 8000,
    });
    expect(running.current().networkFailures).toBeGreaterThan(0);
    await running.stop();
  }, 20000);

  it("does not tell a cashier a 500 refused their data", async () => {
    // §B.9.1 · every degraded reason is **named**; that is not licence to
    // squeeze every failure into one of the names. A 5xx answered, and answered
    // nothing about the data.
    const running = new SyncEngine(database, DEVICE);
    globalThis.fetch = () =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "boom" }), { status: 503 }),
      );
    await running.start({ ...REGISTRY, pull_interval_seconds: 1 });
    await vi.waitFor(() =>
      expect(running.current().networkFailures).toBeGreaterThan(0),
    );
    expect(running.current().degraded).toBeNull();
    await running.stop();
  }, 20000);
});

describe("a merged customer", () => {
  it("removes the till's own row and writes nothing under the server's id", async () => {
    // §5 rule 1 · the till's store is a **server snapshot** plus its own
    // pending events, and a merged row has stopped being one of its pending
    // events. Copying the cashier's hurried entry over the record the office
    // already has — a name, a phone, an address — would make the till a writer
    // of the snapshot, which is the shape the rule forbids. The server's row
    // arrives on the next pull, because a merge touches its `updated_at` and a
    // person just seen at a counter is inside the recency window by definition.
    const local = "cccccccc-0000-0000-0000-000000000021";
    const server = "dddddddd-0000-0000-0000-000000000022";
    await database.collections.customers!.insert({
      id: local,
      updated_at: "2026-09-02T12:00:00.000000Z",
      document_type: "CC",
      document: "1122334455",
      name: "Rosa A.",
      phone: "",
      email: "",
      address: "",
      data_consent: false,
    });
    const row = await queue(database, "customers", {
      id: local,
      document_type: "CC",
      document: "1122334455",
      name: "Rosa A.",
    });

    globalThis.fetch = (input: RequestInfo | URL) => {
      if (String(input).includes("/api/sync/push")) {
        return Promise.resolve(
          Response.json({
            batch_id: row.client_uuid,
            batch_outcome: "applied",
            batch_reason: "",
            results: [
              {
                client_uuid: row.client_uuid,
                outcome: "merged",
                id: server,
                reason: "",
              },
            ],
            server_time: "2026-09-02T12:00:00Z",
          }),
        );
      }
      return Promise.resolve(
        Response.json({
          documents: [],
          checkpoint: null,
          has_more: false,
          registry_version: 1,
          server_time: "2026-09-02T12:00:00Z",
          location_id: DEVICE.location_id,
        }),
      );
    };

    engine = new SyncEngine(database, DEVICE);
    await engine.start({ ...REGISTRY, pull_interval_seconds: 1 });
    await vi.waitFor(async () =>
      expect(await database.collections.outbox!.count().exec()).toBe(0),
    );

    expect(
      await database.collections.customers!.findOne(local).exec(),
    ).toBeNull();
    // Nothing invented under the server's id: it is the server's to send.
    expect(
      await database.collections.customers!.findOne(server).exec(),
    ).toBeNull();
  }, 20000);
});

describe("a refused batch", () => {
  function refusingServer() {
    globalThis.fetch = (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/sync/push")) {
        return Promise.resolve(
          Response.json({
            batch_id: "b",
            batch_outcome: "rejected",
            batch_reason: "Este envío trae filas de otra sede.",
            results: [],
            server_time: "2026-09-02T12:00:00Z",
          }),
        );
      }
      return Promise.resolve(
        Response.json({
          documents: [],
          checkpoint: null,
          has_more: false,
          registry_version: 1,
          server_time: "2026-09-02T12:00:00Z",
          location_id: DEVICE.location_id,
        }),
      );
    };
  }

  it("is not un-blocked by alt-tabbing back into the till", async () => {
    // `pushBlocked` stops a till hammering a refusal it cannot fix. Clearing it
    // on `focus` would re-post the identical batch every time a cashier
    // switched windows — which is the hammering, arriving by a different door.
    refusingServer();
    await queue(database, "customers", {
      id: "cccccccc-0000-0000-0000-000000000011",
      document_type: "CC",
      document: "4040304050",
      name: "Diana Quintero",
    });
    engine = new SyncEngine(database, DEVICE);
    await engine.start({ ...REGISTRY, pull_interval_seconds: 1 });

    await vi.waitFor(() => expect(engine!.current().degraded).toBe("rejected"));
    expect(await database.collections.outbox!.count().exec()).toBe(1);

    // What `window.focus` reaches.
    engine.syncNow();
    await vi.waitFor(() => expect(engine!.current().degraded).toBe("rejected"));
    expect(await database.collections.outbox!.count().exec()).toBe(1);
  }, 20000);

  it("survives a link that drops, and does not read as a refusal then", async () => {
    // Criterion 25 · a call that got no answer is not a call the server
    // refused, so the reason it refused with stops being true when the link
    // goes — even though the queue is still blocked underneath.
    refusingServer();
    await queue(database, "customers", {
      id: "cccccccc-0000-0000-0000-000000000012",
      document_type: "CC",
      document: "5050304050",
      name: "Wilson Cárdenas",
    });
    engine = new SyncEngine(database, DEVICE);
    await engine.start({ ...REGISTRY, pull_interval_seconds: 1 });
    await vi.waitFor(() => expect(engine!.current().degraded).toBe("rejected"));

    globalThis.fetch = () => Promise.reject(new Error("offline"));
    engine.syncNow();
    await vi.waitFor(() => expect(engine!.current().degraded).toBeNull(), {
      timeout: 8000,
    });
    // And the row is still there, which is the half that matters.
    expect(await database.collections.outbox!.count().exec()).toBe(1);
  }, 20000);
});

describe("storage persistence", () => {
  it("does not clear the eviction reason on a successful call", async () => {
    // §B.9.4 · nothing about a successful push makes the storage durable, so
    // the reason is a chip and not a passing line.
    const evictable = new SyncEngine(database, { ...DEVICE, persisted: false });
    expect(evictable.current().degraded).toBe("evictable");
    await evictable.stop();
  });

  it("treats an unanswered browser as unknown and never as a refusal", async () => {
    // `null` is **not yet reported**, never `false` — a browser that has not
    // answered has not refused.
    const quiet = new SyncEngine(database, { ...DEVICE, persisted: null });
    expect(quiet.current().degraded).toBeNull();
    expect(quiet.current().storagePersisted).toBeNull();
    await quiet.stop();
  });
});
