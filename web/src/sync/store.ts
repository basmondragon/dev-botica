import {
  addRxPlugin,
  createRxDatabase,
  type RxDatabase,
  type RxStorage,
} from "rxdb";
import { RxDBCleanupPlugin } from "rxdb/plugins/cleanup";
import { RxDBLeaderElectionPlugin } from "rxdb/plugins/leader-election";
import { getRxStorageDexie } from "rxdb/plugins/storage-dexie";
import {
  COLLECTIONS,
  OUTBOX_SCHEMA,
  SCHEMAS,
  type CollectionName,
} from "./registry";

/**
 * The local store: RxDB over IndexedDB (architecture §5).
 *
 * **Ten collections: the registry's nine stores, plus the outbox.** The nine are
 * a snapshot of server state and nothing else writes them; the outbox is
 * local-only, has no server counterpart, and is the only durable record of a
 * write the server has not seen. That split *is* §5 rule 1 — the till's store
 * is a server snapshot plus its own pending events.
 *
 * **Stores, not streams.** RxDB's open-core build caps a database at thirteen
 * collections and the registry is committed to more streams than that across
 * S2, S3, S4, S5 and S8, so two streams that carry the same shape share a store
 * — `stock_on_hand` holds both the till's own sede and the capped
 * other-location set, split by the `location_id` every row already carries
 * (`registry.belongsTo`). Opening one store per stream would spend the budget
 * on a distinction that is already in the document.
 *
 * **`multiInstance` over `BroadcastChannel`, with leader election.** Two tabs
 * both replicating would be *safe* — the push is idempotent — and would double
 * the pull load on every till in the network while giving two tabs two
 * independent replication states to render one status line from. §B.9.1's line
 * is one state, and two replicators are two answers to how current this till is.
 */

addRxPlugin(RxDBLeaderElectionPlugin);
// Compaction is a client job and this is the plugin that performs it. It is
// registered rather than assumed: `collection.cleanup` is undefined without
// it, and an engine calling an absent method would report a compaction that
// never ran.
addRxPlugin(RxDBCleanupPlugin);

export type SyncDatabase = RxDatabase;

/**
 * The store is named for the device, so a browser re-claimed as a different
 * till does not inherit the previous one's catalog — and a developer switching
 * between two seeded tenants does not silently mix two networks' items.
 */
export function databaseName(deviceId: string) {
  return `botica_${deviceId.replace(/-/g, "")}`;
}

let opening: Promise<SyncDatabase> | null = null;
let openFor = "";

export async function openStore(
  deviceId: string,
  // Dexie on a till; the memory storage in a test, where what is under test is
  // this stage's own rules rather than RxDB's adapter — and the adapter is
  // exercised by the production-build gate instead.
  storage: RxStorage<unknown, unknown> = getRxStorageDexie(),
): Promise<SyncDatabase> {
  if (opening && openFor === deviceId) return opening;
  openFor = deviceId;
  opening = build(deviceId, storage);
  return opening;
}

async function build(
  deviceId: string,
  storage: RxStorage<unknown, unknown>,
): Promise<SyncDatabase> {
  const database = await createRxDatabase({
    name: databaseName(deviceId),
    storage,
    multiInstance: true,
    eventReduce: true,
    // A till is not a place to lose an unsent row to a background sweep nobody
    // asked for. **Nothing here reaches the outbox**: RxDB's cleanup removes
    // the tombstones of documents already deleted locally, and an outbox row is
    // deleted only when the server has confirmed it. Compaction is run
    // explicitly by the engine, in the leader tab, on start and every six
    // hours — never while a capture field has focus (§B.13.3).
    cleanupPolicy: { minimumDeletedTime: 0, minimumCollectionAge: 0 },
  });
  await database.addCollections({
    ...Object.fromEntries(
      COLLECTIONS.map((name: CollectionName) => [
        name,
        { schema: SCHEMAS[name] },
      ]),
    ),
    outbox: { schema: OUTBOX_SCHEMA },
  });
  return database;
}

/** For tests and for a device that was re-claimed: close and forget. */
export async function closeStore() {
  const current = opening;
  opening = null;
  openFor = "";
  if (current) await (await current).close();
}
