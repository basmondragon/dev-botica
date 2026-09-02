import { replicateRxCollection } from "rxdb/plugins/replication";
import type { RxReplicationState } from "rxdb/plugins/replication";
import {
  COLLECTIONS,
  LOCATION_SCOPED,
  REGISTRY_VERSION,
  type CollectionName,
} from "./registry";
import type { DeviceRecord } from "./device";
import { writeDevice } from "./device";
import { localDigest } from "./digest";
import * as outbox from "./outbox";
import type { SyncDatabase } from "./store";
import {
  ServerRefused,
  Unreachable,
  fetchDigest,
  fetchPull,
  type PullResponse,
  type RegistryResponse,
} from "./transport";
import type { DegradedReason } from "./state";

/**
 * The replication engine: what runs, when, and what it does when it fails.
 *
 * **All client sync code is in this one module** (§5). No domain code calls the
 * replication API directly, so if §4's measurements ever justify a sync engine
 * — PowerSync, ElectricSQL, logical replication — it replaces this module and
 * nothing else in the product moves.
 */

export interface Checkpoint {
  updated_at: string;
  id: string;
}

export interface EngineSnapshot {
  ready: boolean;
  /** Per-collection `received of total`, which is what the first-sync card
   *  counts against — real server totals, never a percentage (§B.9.1). */
  progress: Record<string, { received: number; total: number }>;
  pending: number;
  queue: Record<string, number>;
  lastPullAt: string | null;
  lastPushAt: string | null;
  degraded: DegradedReason | null;
  networkFailures: number;
  online: boolean;
  clockSkewMs: number | null;
  clockSkewWarnSeconds: number;
  storagePersisted: boolean | null;
  device: DeviceRecord;
  /** The collection currently downloading, for the first-sync card's line. */
  syncing: CollectionName | null;
  lastError: string;
  /** §B.10.3 · the correlation id of the last refusal, so an error a cashier
   *  reports has something a support engineer can chase. */
  requestId: string;
}

type Listener = (snapshot: EngineSnapshot) => void;

const POLL_CEILING_MS = 60_000;
const HIDDEN_INTERVAL_MS = 60_000;
const COMPACTION_INTERVAL_MS = 6 * 60 * 60 * 1000;
const DIGEST_INTERVAL_MS = 24 * 60 * 60 * 1000;
/** How often the daily gate is *looked at*. The gate is what makes it daily. */
const DIGEST_CHECK_INTERVAL_MS = 60 * 60 * 1000;
/** How often a tab re-reads what the leader shares. */
const SHARED_REFRESH_MS = 5_000;
const DIGEST_KEY = "botica.sync.digest";
const EPOCH_KEY = "botica.sync.epoch";
/**
 * Which device has completed a first sync.
 *
 * **It is a property of the store and not of this tab.** Only the leader
 * replicates, so a second tab never runs a pull of its own — and a `ready` flag
 * derived from *this tab's* replication would leave every follower on the
 * first-sync screen forever, looking at a store that is already complete.
 */
const FIRST_SYNC_KEY = "botica.sync.first";
/**
 * When this **store** last completed a pull.
 *
 * Shared across tabs for the same reason `ready` is: only the leader pulls, so
 * a follower deriving `Sincronizado hace 4 s` from its own replication would
 * render a different line from the leader's — and §B.9.1's line is one state.
 */
const LAST_PULL_KEY = "botica.sync.last-pull";
/** The registry version this store was last built under. */
const VERSION_KEY = "botica.sync.registry-version";

function readLocal(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A browser that will not keep this re-syncs from its checkpoint on the
    // next load, which is correct and merely slower.
  }
}

/**
 * A collection's checkpoint is identified by its `replicationIdentifier`, so
 * **resetting one is bumping its epoch**: the old identifier's checkpoint is
 * abandoned, the local documents are wiped, and the collection re-pulls from
 * zero. That is what a registry-version change and a failed digest both do, and
 * it is deterministic rather than reliant on removing replication metadata by
 * hand.
 */
function readEpochs(): Record<string, number> {
  try {
    return JSON.parse(readLocal(EPOCH_KEY) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

function writeEpochs(epochs: Record<string, number>) {
  writeLocal(EPOCH_KEY, JSON.stringify(epochs));
}

export class SyncEngine {
  private listeners = new Set<Listener>();
  private replications = new Map<
    CollectionName,
    RxReplicationState<never, unknown>
  >();
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** The `location_id` the last pull answered. Applied **between** cycles: a
   *  reset from inside a pull handler would cancel and rebuild the very
   *  replication whose handler is running, and would mutate the map `pull()` is
   *  iterating. */
  private observedLocation: string | null = null;
  private outboxWatch: { unsubscribe: () => void } | null = null;
  /**
   * Who is waiting for a collection's next page to come back.
   *
   * A cycle has to know when its pull is done. **`RxReplicationState.awaitInSync`
   * is not that signal**: RxDB documents it as unsafe in a multi-tab
   * application, because it waits on database idleness that only the leading
   * instance ever reaches — and in practice it does not resolve here. The
   * handler below is the only code that knows a page came back, so it is the
   * thing that says so.
   */
  private waiters = new Map<
    CollectionName,
    { resolve: () => void; reject: (failure: unknown) => void }[]
  >();
  private compactionTimer: ReturnType<typeof setInterval> | null = null;
  private digestTimer: ReturnType<typeof setInterval> | null = null;
  private sharedTimer: ReturnType<typeof setInterval> | null = null;
  private backoffMs = 0;
  private stopped = false;
  /**
   * Whether this tab holds the store's leadership.
   *
   * **The gate has to be here.** RxDB's own `waitForLeadership` is honoured
   * only on the path it takes when `autoStart` is true, and this engine drives
   * every replication by hand precisely so the push runs before the pull — so
   * calling `start()` from a follower would replicate from a follower, and the
   * server would see two pull streams for one till (§5, criterion 15).
   */
  private leader = false;
  private draining = false;
  /** Set by a whole-batch refusal; cleared only by `Sincronizar ahora`. */
  private pushBlocked = false;
  /** Set by a per-row refusal; cleared by the next batch that applies
   *  something. A pull succeeding says nothing about a row the server refused. */
  private rowsRejected = false;
  private snapshot: EngineSnapshot;
  private options: RegistryResponse | null = null;

  constructor(
    private database: SyncDatabase,
    private device: DeviceRecord,
  ) {
    this.snapshot = {
      ready: readLocal(FIRST_SYNC_KEY) === device.id,
      progress: {},
      pending: 0,
      queue: {},
      lastPullAt: readLocal(LAST_PULL_KEY),
      lastPushAt: null,
      degraded: device.persisted === false ? "evictable" : null,
      networkFailures: 0,
      online: typeof navigator === "undefined" ? true : navigator.onLine,
      clockSkewMs: null,
      clockSkewWarnSeconds: 90,
      storagePersisted: device.persisted,
      device,
      syncing: null,
      lastError: "",
      requestId: "",
    };
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => this.listeners.delete(listener);
  }

  current() {
    return this.snapshot;
  }

  private emit(patch: Partial<EngineSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener(this.snapshot);
  }

  /**
   * Start replicating. **Only the leader tab does**: two replicators are two
   * answers to how current this till is, and §B.9.1's line is one state.
   */
  async start(registry: RegistryResponse) {
    this.options = registry;
    this.emit({
      clockSkewMs: registry.clock_skew_ms ?? null,
      clockSkewWarnSeconds: registry.clock_skew_warn_seconds,
      storagePersisted: registry.storage_persisted ?? this.device.persisted,
      progress: Object.fromEntries(
        registry.collections.map((one) => [
          one.name,
          { received: 0, total: one.rows },
        ]),
      ),
    });

    // The device record follows the server: a sede renamed from the office has
    // to reach the sync panel and the counter's empty state without a re-claim.
    if (
      registry.location_name &&
      (registry.location_name !== this.device.location_name ||
        registry.location_code !== this.device.location_code)
    ) {
      this.device = writeDevice({
        ...this.device,
        location_name: registry.location_name,
        location_code: registry.location_code,
      });
      this.emit({ device: this.device });
    }

    await this.applyLocationChange(registry.location_id);
    await this.applyVersionChange();
    await this.refreshCounts();
    this.watchOutbox();
    // A follower runs no cycle, so nothing else would ever re-read what the
    // leader shares. This is the only clock a follower has.
    this.sharedTimer = setInterval(
      () => void this.refreshCounts(),
      SHARED_REFRESH_MS,
    );

    for (const name of COLLECTIONS) {
      this.replications.set(name, this.replicate(name));
    }

    // **Not awaited.** A follower tab never becomes leader while the leader
    // lives, so awaiting leadership here would leave `start()` unresolved in
    // every tab but one. The follower still renders: it reads the same store
    // and the same outbox, and its `SyncStatus` is the same state (§5).
    void this.database.waitForLeadership().then(() => {
      if (this.stopped) return;
      this.leader = true;
      this.schedule(0);
      this.compactionTimer = setInterval(
        () => void this.compact(),
        COMPACTION_INTERVAL_MS,
      );
      // Checked hourly and **run daily**: the interval gate inside
      // `maybeDigest` is what makes it daily, and without a timer beside it the
      // check would get exactly one chance per browser session — which a till
      // left open across a long weekend never takes.
      this.digestTimer = setInterval(
        () => void this.maybeDigest(),
        DIGEST_CHECK_INTERVAL_MS,
      );
      this.leader = true;
      void this.compact();
      void this.maybeDigest();
    });
  }

  /**
   * Criterion 15 · **the queue count is identical in both tabs.**
   *
   * Only the leader replicates, but every tab shares the store — so a follower
   * keeps its count live by watching the outbox rather than by polling, and the
   * two tabs render one number from one place.
   */
  private watchOutbox() {
    this.outboxWatch = this.database.collections.outbox!.$.subscribe(() => {
      void this.refreshCounts();
    });
  }

  private replicate(name: CollectionName) {
    const epoch = readEpochs()[name] ?? 0;
    const collection = this.database.collections[name]!;
    return replicateRxCollection({
      collection,
      // The epoch is what makes a reset deterministic: a new identifier has no
      // checkpoint, so the collection starts from zero.
      replicationIdentifier: `botica-${name}-v${REGISTRY_VERSION}-e${epoch}`,
      /**
       * **`live: true` with no `pull.stream$`, which is not the same as
       * `live: false`.**
       *
       * A non-live replication in RxDB runs once and then cancels itself, so
       * the cadence below would have produced exactly one sync per till and
       * then silence — the failure that looks like a working product for the
       * first eight seconds. Live keeps the replication subscribed and idle;
       * with no stream to listen to, it does nothing at all until `reSync()`
       * asks it to. The schedule, the ordering and the backoff stay this
       * engine's, which is what *The poll schedule* requires.
       */
      live: true,
      // RxDB waits for leadership itself, which is the same rule the poll timer
      // below follows: **two replicators are two answers to how current this
      // till is** (§5), and §B.9.1's line is one state.
      waitForLeadership: true,
      // RxDB's own retry on a failed handler. Held at the ceiling so it never
      // races the backoff this engine runs.
      retryTime: POLL_CEILING_MS,
      // **The push runs before the pull in every cycle**, and auto-start would
      // put RxDB's own first pull ahead of the first push. `start()` is the one
      // primitive: the first call starts the replication, every later call is a
      // resync — so the cycle below drives both without a second code path.
      autoStart: false,
      pull: {
        batchSize: this.options?.pull_page_size ?? 500,
        handler: async (checkpoint: unknown, batchSize: number) => {
          if (this.stopped) {
            this.settle(name, null);
            return { documents: [] as never[], checkpoint };
          }
          let answer: PullResponse;
          try {
            answer = await fetchPull(
              this.device,
              name,
              (checkpoint as Checkpoint | undefined) ?? null,
              batchSize,
            );
          } catch (failure) {
            this.settle(name, failure);
            throw failure;
          }
          if (answer.registry_version > REGISTRY_VERSION) {
            // The server is ahead of this bundle. The application shell is
            // served by the service worker and updates itself on the next full
            // load; until then this client must not write documents it does
            // not understand.
            const refusal = new ServerRefused(409, "versión desactualizada");
            this.emit({ degraded: "outdated" });
            void this.reloadWhenDrained();
            this.settle(name, refusal);
            throw refusal;
          }
          this.observedLocation = answer.location_id;
          const received = answer.documents.length;
          if (received > 0) {
            const progress = { ...this.snapshot.progress };
            const current = progress[name] ?? { received: 0, total: 0 };
            progress[name] = {
              ...current,
              received: current.received + received,
            };
            this.emit({ progress, syncing: name });
          }
          // Caught up as far as the server is concerned. That is what a cycle
          // waits on: not RxDB's idleness, but the last page of this
          // collection's delta having been answered.
          if (!answer.has_more) this.settle(name, null);
          return {
            documents: answer.documents as never[],
            checkpoint: answer.checkpoint ?? checkpoint,
          };
        },
      },
    }) as unknown as RxReplicationState<never, unknown>;
  }

  /**
   * Criterion 31 · a registry version that changes a local schema resets and
   * re-pulls the affected collections.
   *
   * **It does not reset a device that is offline with unsent rows.** That
   * device stays `degraded`, keeps its store, and completes the reset only
   * after it has drained — the stored version is not advanced until every
   * collection has actually been reset, so a half-done reset is retried rather
   * than forgotten.
   */
  private async applyVersionChange() {
    const stored = Number(readLocal(VERSION_KEY) ?? REGISTRY_VERSION);
    if (stored === REGISTRY_VERSION) {
      writeLocal(VERSION_KEY, String(REGISTRY_VERSION));
      return;
    }
    for (const name of COLLECTIONS) {
      if (!(await this.reset(name))) {
        this.emit({ degraded: "outdated" });
        return;
      }
    }
    writeLocal(VERSION_KEY, String(REGISTRY_VERSION));
  }

  /**
   * Criterion 13 · an office moved this till to another sede.
   *
   * Its whole location-scoped predicate changed, so those collections are wiped
   * and re-pulled — **and the tenant-wide ones are left exactly as they are**,
   * because nothing about the catalog, the laboratorios or the clientes moved.
   * No re-claim, and the outbox is untouched.
   */
  private async applyLocationChange(locationId: string) {
    if (!locationId || locationId === this.device.location_id) return;
    // **The record moves only once the reset has actually happened.** Writing
    // the new sede first and then failing to drain would leave a till holding
    // the old sede's prices with nothing left to notice: the location matches
    // on every later cycle, so the reset is never attempted again and the
    // wrong prices stand until somebody re-claims the browser.
    for (const name of LOCATION_SCOPED) {
      if (!(await this.reset(name))) return;
    }
    this.device = writeDevice({ ...this.device, location_id: locationId });
    this.emit({ device: this.device });
  }

  /**
   * Wipe one collection and give it a new checkpoint identity.
   *
   * **A reset never discards an unpushed row.** The outbox is drained first; if
   * it cannot be drained because the device is offline, **the reset does not
   * happen** — the till keeps its old store, stays `degraded`, and retries when
   * the link returns. A forced wipe with a full outbox loses sales, which is
   * the single failure this whole stage exists to prevent, and it would arrive
   * as a deployment side effect rather than as anything a cashier did.
   */
  async reset(name: CollectionName): Promise<boolean> {
    if ((await outbox.depth(this.database)) > 0) {
      try {
        await this.push();
      } catch {
        this.emit({ degraded: this.snapshot.degraded ?? "outdated" });
        return false;
      }
      if ((await outbox.depth(this.database)) > 0) return false;
    }
    const running = this.replications.get(name);
    if (running) {
      await running.cancel();
      this.replications.delete(name);
    }
    const epochs = readEpochs();
    epochs[name] = (epochs[name] ?? 0) + 1;
    writeEpochs(epochs);
    const held = await this.database.collections[name]!.find().exec();
    await this.database.collections[name]!.bulkRemove(
      held.map((document) => document.primary),
    );
    const progress = { ...this.snapshot.progress };
    if (progress[name]) progress[name] = { ...progress[name], received: 0 };
    this.emit({ progress });
    if (!this.stopped) this.replications.set(name, this.replicate(name));
    return true;
  }

  /**
   * The server's registry is ahead of this bundle, so the application shell has
   * to be replaced. The shell is served by the service worker and updates
   * itself on a **full load**, never mid-session.
   *
   * **The reload waits for the outbox.** Swapping the running application under
   * an unsent row is the same failure a forced reset is, arriving by a
   * different door; a till with something to send stays `degraded` and reloads
   * on the cycle after it drains.
   */
  private async reloadWhenDrained() {
    if (this.stopped) return;
    if ((await outbox.depth(this.database)) > 0) return;
    if (typeof location !== "undefined") location.reload();
  }

  /**
   * Pull now: the browser's `online` event, a regained focus, and the tick after
   * a successful push.
   *
   * **It does not clear a refusal.** Alt-tabbing back into a till is not a
   * person deciding to retry a batch the server would not take; if it were, a
   * blocked queue would re-post itself every time a cashier switched windows,
   * which is the hammering `pushBlocked` exists to stop.
   */
  syncNow() {
    this.backoffMs = 0;
    this.schedule(0);
  }

  /**
   * `Sincronizar ahora`, from the sync panel — **a person deciding to try
   * again**, which is the one thing that clears a refusal. The conflict row it
   * produced stays in the office queue either way.
   */
  retryNow() {
    this.pushBlocked = false;
    this.rowsRejected = false;
    this.emit({ degraded: this.restingReason() });
    this.syncNow();
  }

  private schedule(delayMs: number) {
    if (this.stopped) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.cycle(), delayMs);
  }

  private interval() {
    if (typeof document !== "undefined" && document.hidden)
      return HIDDEN_INTERVAL_MS;
    return (this.options?.pull_interval_seconds ?? 8) * 1000;
  }

  /**
   * One cycle: **push, then pull.**
   *
   * A till that has something to say says it before it listens, so a customer
   * registered offline is on the server before the pull that follows it
   * returns. From S3 this also means the stock consequences of a till's own
   * sale arrive on the same round trip that sent it.
   */
  private async cycle() {
    // A follower reads the store and the outbox and renders the same line; what
    // it does not do is talk to the server.
    if (this.stopped || !this.leader) return;
    let failed = false;
    try {
      await this.push();
      await this.pull();
      // Between cycles, never inside a handler: an office moved this till, and
      // its whole location-scoped predicate changed with it.
      if (this.observedLocation) {
        await this.applyLocationChange(this.observedLocation);
      }
      this.backoffMs = 0;
      this.emit({ networkFailures: 0, degraded: this.restingReason() });
    } catch (failure) {
      failed = true;
      this.note(failure);
      // **One retry policy, and it is this engine's.** A handler that threw
      // leaves RxDB in a retry of its own, waiting out `retryTime` and ignoring
      // a resync in the meantime — so the next cycle would wait on a page that
      // never comes and the till would sit on the reason it failed with, long
      // after the reason stopped being true. Rebuilding is cheap and keeps the
      // checkpoint: the epoch is unchanged, so the replication identifier is
      // too.
      await this.rearm();
    }
    await this.refreshCounts();
    // Jitter, because twenty tills that lost the same link would otherwise
    // return in lockstep.
    const base = failed ? this.nextBackoff() : this.interval();
    this.schedule(base + Math.random() * base * 0.2);
  }

  private nextBackoff() {
    const start = this.interval();
    this.backoffMs = Math.min(
      POLL_CEILING_MS,
      this.backoffMs === 0 ? start * 2 : this.backoffMs * 2,
    );
    return this.backoffMs;
  }

  private async push() {
    // A stopped engine is a tab that has gone. It does not keep talking to the
    // server, and a cycle already in flight when `stop()` lands unwinds here.
    //
    // **A refused batch stops the queue rather than draining it.** A foreign
    // tenant or a foreign location means this till is wrong about which network
    // or sede it is at, which no retry changes and no cashier can fix; sending
    // the same rows every eight seconds would be a till hammering a refusal.
    // The rows stay — deleting writes the server never applied is the failure
    // this stage exists to prevent — and `Sincronizar ahora` is the one thing
    // that tries again, because it is a person deciding to.
    if (this.stopped || this.draining || this.pushBlocked) return;
    this.draining = true;
    try {
      const maxRows = this.options?.push_batch_max_rows ?? 200;
      let report = await outbox.drain(this.database, this.device, maxRows);
      while (
        report.applied + report.duplicate + report.merged + report.rejected >
        0
      ) {
        for (const merge of report.merges) await this.adoptServerId(merge);
        if (report.rejected > 0 || report.batchRejected) {
          this.rowsRejected = true;
          this.emit({ degraded: "rejected", lastError: report.reason });
        } else if (report.applied + report.duplicate + report.merged > 0) {
          // A batch that applied something is the condition being resolved.
          this.rowsRejected = false;
        }
        this.emit({ lastPushAt: new Date().toISOString() });
        if (report.batchRejected) {
          this.pushBlocked = true;
          break;
        }
        if ((await outbox.depth(this.database)) === 0) break;
        report = await outbox.drain(this.database, this.device, maxRows);
      }
    } finally {
      this.draining = false;
    }
  }

  private settle(name: CollectionName, failure: unknown | null) {
    const held = this.waiters.get(name);
    if (!held || held.length === 0) return;
    this.waiters.set(name, []);
    for (const one of held) {
      if (failure === null) one.resolve();
      else one.reject(failure);
    }
  }

  /**
   * Resolves when this collection's next page comes back, rejects with whatever
   * the call failed on, and **gives up rather than hanging**.
   *
   * A cycle that waited forever on a page that never comes is a till whose
   * status line freezes on whatever it last said, which is worse than a wrong
   * line because nothing about it looks wrong.
   */
  private nextPage(name: CollectionName) {
    const ceiling = Math.max(3 * this.interval(), POLL_CEILING_MS / 2);
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(
        () => this.settle(name, new Unreachable()),
        ceiling,
      );
      const done = () => clearTimeout(timer);
      const held = this.waiters.get(name) ?? [];
      held.push({
        resolve: () => {
          done();
          resolve();
        },
        reject: (failure) => {
          done();
          reject(failure);
        },
      });
      this.waiters.set(name, held);
    });
  }

  /** Cancel and recreate every replication, keeping each one's checkpoint. */
  private async rearm() {
    if (this.stopped) return;
    for (const [name, replication] of [...this.replications]) {
      await replication.cancel().catch(() => undefined);
      this.replications.set(name, this.replicate(name));
    }
  }

  /**
   * `merged` · **the local row goes; the server's own arrives.**
   *
   * The temporary row the till invented under its own id is removed, and
   * nothing is written under the server's id — because the row the natural key
   * merged onto is the server's, and copying a cashier's hurried entry over it
   * would overwrite a name, a phone and an address the office already has with
   * whatever was typed at a counter. §5 rule 1: the till's store is a **server
   * snapshot** plus its own pending events, and this row has stopped being one
   * of its pending events.
   *
   * It arrives on the very next pull: the server stamps `updated_at` on a
   * merge, so the person is inside the recency window by definition — they were
   * just seen at a counter.
   *
   * The cashier is told nothing. They registered a person who was already
   * known, which is not an event worth telling them about (§*Offline*).
   */
  private async adoptServerId(merge: {
    collection: string;
    from: string;
    to: string;
  }) {
    const local = await this.database.collections[merge.collection]
      ?.findOne(merge.from)
      .exec();
    await local?.remove();
  }

  private async pull() {
    if (this.stopped) return;
    for (const [name, replication] of [...this.replications]) {
      if (this.stopped) return;
      this.emit({ syncing: name });
      const answered = this.nextPage(name);
      void replication.start();
      await answered;
    }
    const at = new Date().toISOString();
    writeLocal(FIRST_SYNC_KEY, this.device.id);
    writeLocal(LAST_PULL_KEY, at);
    this.emit({ lastPullAt: at, syncing: null, ready: true });
  }

  /**
   * A named reason, or `offline`.
   *
   * **A transport failure with no server response is `offline`, not
   * `degraded`** — the difference is whether we heard back.
   */
  private note(failure: unknown) {
    if (failure instanceof Unreachable) {
      // Criterion 25 · **a call that got no answer is not a call the server
      // refused**, so the reason it refused with stops being true the moment
      // the link goes. The line moves from `Sincronización con problemas ·
      // sesión vencida` to `Sin conexión`, which is what a cashier can act on.
      // The persistence reason is not a transport failure and survives.
      this.emit({
        networkFailures: this.snapshot.networkFailures + 1,
        degraded: this.offlineReason(),
        lastError: failure.message,
        requestId: "",
      });
      return;
    }
    if (failure instanceof ServerRefused && failure.status >= 500) {
      // A 5xx answered, and answered nothing about the data. Naming it
      // `el servidor rechazó los datos` would tell a cashier their sale was
      // refused when it was not, and §B.9.1's rule is that every degraded
      // reason is **named** — not that every failure has to be squeezed into
      // one of the names. It counts as a transport failure and reads
      // `Sin conexión` once two in a row have failed.
      this.emit({
        networkFailures: this.snapshot.networkFailures + 1,
        degraded: this.offlineReason(),
        lastError: failure.detail,
        requestId: failure.requestId,
      });
      return;
    }
    if (failure instanceof ServerRefused) {
      const reason: DegradedReason =
        failure.status === 401 && failure.detail.includes("dado de baja")
          ? "revoked"
          : failure.status === 401 || failure.status === 403
            ? "session_expired"
            : failure.status === 409 && failure.detail.includes("versión")
              ? "outdated"
              : "rejected";
      this.emit({
        degraded: reason,
        lastError: failure.detail,
        requestId: failure.requestId,
      });
      return;
    }
    // A local write that threw. On a till this is a quota error far more often
    // than anything else, and compaction is the one thing that answers it.
    this.emit({ degraded: "storage_full", lastError: String(failure) });
    void this.compact();
  }

  /**
   * What a till is still `degraded` about once a cycle has *succeeded*.
   *
   * Three things survive a good call. The persistence reason, because nothing
   * about a successful push makes the storage durable (§B.9.4). A refused
   * batch, and a refused row — because §B.9.1 leaves `degraded` when *the named
   * condition is resolved*, and a pull succeeding resolves nothing about a row
   * the server would not take. What resolves it is a later batch that applied
   * something, or a person pressing `Sincronizar ahora`.
   */
  private restingReason(): DegradedReason | null {
    if (this.pushBlocked || this.rowsRejected) return "rejected";
    return this.offlineReason();
  }

  /**
   * What survives a call that **got no answer**.
   *
   * Only the persistence chip, which is not a transport failure at all. A
   * refusal the server made stops being true the moment the link goes:
   * criterion 25 turns `Sincronización con problemas · sesión vencida` into
   * `Sin conexión` on exactly that transition, and carrying `el servidor
   * rechazó los datos` through a blackout would tell a cashier the server did
   * something it is in no position to do.
   */
  private offlineReason(): DegradedReason | null {
    return this.snapshot.storagePersisted === false ? "evictable" : null;
  }

  private async refreshCounts() {
    const [pending, queue] = await Promise.all([
      outbox.depth(this.database),
      outbox.byKind(this.database),
    ]);
    this.emit({
      pending,
      queue,
      // Re-read rather than remembered, so a follower's resting line ticks off
      // the leader's last pull and the two tabs read the same thing — and so a
      // follower opened *during* a first sync leaves the first-sync screen when
      // the leader finishes, rather than sitting on it until it is reloaded.
      lastPullAt: readLocal(LAST_PULL_KEY) ?? this.snapshot.lastPullAt,
      ready:
        this.snapshot.ready || readLocal(FIRST_SYNC_KEY) === this.device.id,
      online: typeof navigator === "undefined" ? true : navigator.onLine,
    });
  }

  /**
   * Retention and compaction, in the leader tab, on start and every six hours.
   *
   * **Nothing unpushed is compacted at any age, ever.** The outbox is not
   * swept: at S2 it is the only event collection, and a row in it by definition
   * has not been confirmed applied. S3's moves and S4's sales inherit this rule
   * rather than inventing one.
   */
  private async compact() {
    if (typeof document !== "undefined") {
      const focused = document.activeElement;
      // Never while a capture field has focus (§B.13.3): a scan is a burst of
      // characters and a compaction that stole a frame mid-burst drops it.
      if (
        focused instanceof HTMLElement &&
        (focused.tagName === "INPUT" || focused.tagName === "TEXTAREA")
      ) {
        return;
      }
    }
    // `local_retention_days` is what a till keeps of what it has already
    // settled: RxDB's cleanup drops the tombstones of documents deleted
    // locally, and holding them for the retention window is what makes the
    // setting load-bearing rather than decorative. **The outbox is not swept**
    // — a row in it has by definition not been confirmed applied, and nothing
    // unpushed is compacted at any age.
    const keepMs =
      (this.options?.local_retention_days ?? 30) * 24 * 60 * 60 * 1000;
    for (const name of COLLECTIONS) {
      await this.database.collections[name]?.cleanup(keepMs).catch(() => false);
    }
  }

  /**
   * The daily divergence check (`GET /api/sync/digest`).
   *
   * It is the backstop that makes the safety horizon an engineering choice
   * rather than a bet: a mismatch resets that collection's checkpoint and
   * re-pulls it, which turns a silent permanent loss into a one-day-late
   * repair.
   */
  async maybeDigest(now = Date.now()) {
    const last = Number(readLocal(DIGEST_KEY) ?? 0);
    if (now - last < DIGEST_INTERVAL_MS) return;
    try {
      const answer = await fetchDigest(this.device);
      for (const name of COLLECTIONS) {
        const remote = answer.collections[name];
        if (!remote) continue;
        const local = await localDigest(this.database, name);
        // **Both halves.** The count catches a row that arrived or left without
        // a departure being serveable — a hard delete, a window that aged out.
        // The checksum catches the one the count cannot: a row rewritten inside
        // a transaction that outlived the safety horizon, where nothing was
        // added or removed and the till is simply holding an older version of a
        // row it already has. That second case is the whole reason the server
        // computes a checksum at all.
        if (
          local.count !== remote.count ||
          local.checksum !== remote.checksum
        ) {
          await this.reset(name);
        }
      }
      writeLocal(DIGEST_KEY, String(now));
    } catch {
      // A digest we could not fetch is a check that runs tomorrow, not a
      // failure a cashier is shown.
    }
  }

  async stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.compactionTimer) clearInterval(this.compactionTimer);
    if (this.digestTimer) clearInterval(this.digestTimer);
    if (this.sharedTimer) clearInterval(this.sharedTimer);
    this.outboxWatch?.unsubscribe();
    this.outboxWatch = null;
    for (const replication of this.replications.values()) {
      await replication.cancel();
    }
    this.replications.clear();
    // A cycle waiting on a page it will never get would hang the caller.
    for (const name of [...this.waiters.keys()]) {
      this.settle(name, new Unreachable());
    }
    this.waiters.clear();
    this.listeners.clear();
  }
}
