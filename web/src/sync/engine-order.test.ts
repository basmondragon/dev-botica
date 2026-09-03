import { getRxStorageMemory } from "rxdb/plugins/storage-memory";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

/**
 * **Its own file, deliberately.** The ordering this asserts is the order of the
 * first two calls a fresh till makes, and a second engine still unwinding from
 * a previous test in the same module would put its own pull in front of them —
 * a green-then-red test that says nothing about the product. Vitest isolates
 * files, so this one starts from nothing.
 */
describe("§ the poll schedule · push before pull", () => {
  it("pushes before it pulls in every cycle", async () => {
    // A till that has something to say says it before it listens, so a customer
    // registered offline is on the server before the pull that follows it.
    // Only the two calls a cycle makes are recorded. The daily digest runs on
    // leadership and is neither, and counting it as a pull would fail this
    // check for a reason that has nothing to do with the ordering.
    const order: string[] = [];
    globalThis.fetch = (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/sync/push")) {
        order.push("push");
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
      if (url.includes("/api/sync/pull")) order.push("pull");
      if (url.includes("/api/sync/digest")) {
        return Promise.resolve(
          Response.json({
            collections: {},
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
    await queue(database, "customers", {
      id: "cccccccc-0000-0000-0000-000000000003",
      document_type: "CC",
      document: "3030304050",
      name: "Camila Rojas",
    });
    engine = new SyncEngine(database, DEVICE);
    await engine.start({ ...REGISTRY, pull_interval_seconds: 1 });

    await vi.waitFor(() => expect(order).toContain("pull"), { timeout: 8000 });
    expect(order[0]).toBe("push");
    expect(order.indexOf("push")).toBeLessThan(order.indexOf("pull"));
  }, 20000);
});

describe("uuid v7 inside one millisecond", () => {
  it("keeps minting in order, which is what the push applies a batch in", async () => {
    // **S4 is what makes this load-bearing**: a sale's close event, its
    // payments and its lines are minted in the same millisecond, and a key that
    // fell back on randomness inside one would let a payment arrive before the
    // sale it pays for.
    const { uuidV7 } = await import("./outbox");
    const keys = Array.from({ length: 500 }, () => uuidV7());
    expect(keys).toEqual([...keys].sort());
    expect(new Set(keys).size).toBe(keys.length);
  });
});
