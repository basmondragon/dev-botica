import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useMe } from "@/api/queries";
import {
  readDevice,
  requestPersistence,
  writeDevice,
  type DeviceRecord,
} from "./device";
import { SyncEngine, type EngineSnapshot } from "./engine";
import { openStore, type SyncDatabase } from "./store";
import { fetchRegistry, type RegistryResponse } from "./transport";
import {
  dwell,
  render,
  stateOf,
  type Dwelt,
  type Rendered,
  type SyncFacts,
} from "./state";

/**
 * The one place the application touches the local store.
 *
 * **A4's boundary made operational.** The provider is mounted only for a role
 * that is a till, so an office browser never replicates, never opens a store
 * and never issues a `/api/sync/pull`. An `owner` who deliberately claims the
 * browser they are sitting at gets the same engine; what never happens is being
 * *offered* it.
 */

export interface SyncContextValue {
  database: SyncDatabase | null;
  device: DeviceRecord | null;
  snapshot: EngineSnapshot | null;
  /** The line §B.9.1 draws, after the two-second dwell. */
  line: Rendered | null;
  registry: RegistryResponse | null;
  needsClaim: boolean;
  /** The four events *The poll schedule* names: `online`, focus, a successful
   *  push, and a regained visibility. It resets the backoff and nothing else. */
  syncNow: () => void;
  /** `Sincronizar ahora` from the panel — a person deciding to try again, which
   *  is the one thing that clears a refusal the server made. */
  retryNow: () => void;
  adopt: (device: DeviceRecord) => void;
  panelOpen: boolean;
  setPanelOpen: (open: boolean) => void;
}

/**
 * Exported so a test can render a surface against a stated sync state without
 * standing up a store and a server. Nothing in the application reads it
 * directly — `useSync` is the accessor, and it refuses outside a provider.
 */
export const SyncContext = createContext<SyncContextValue | null>(null);
const Sync = SyncContext;

export function useSync() {
  const value = useContext(Sync);
  if (!value) {
    throw new Error(
      "useSync outside SyncProvider. Every surface that reads the local store " +
        "renders SyncStatus, and SyncStatus reads this.",
    );
  }
  return value;
}

/** Whether this identity's browser is a till at all (A4). */
export function isTillRole(role: string | undefined) {
  return role === "cashier";
}

export function facts(snapshot: EngineSnapshot): SyncFacts {
  return {
    // **At v1 nothing produces `blocked`** (A6, §8). The state and its banner
    // geometry exist; no condition in the product raises them.
    blocking: false,
    degraded: snapshot.degraded,
    online: snapshot.online,
    networkFailures: snapshot.networkFailures,
    pending: snapshot.pending,
    lastPullAt: snapshot.lastPullAt,
  };
}

/**
 * The line for the state that is currently *shown*, which during a dwell is not
 * the state that is currently *true*.
 *
 * **A held line is kept, not recomputed.** Rendering the live facts under the
 * held state's name would defeat the dwell for every state but `synced`: a link
 * that returns 250 ms after it dropped would put `Sincronizado` on screen while
 * `held.shown` still said `offline`, and §B.9.1's two-second hold exists
 * precisely so a flapping link does not make the line flicker. The figures a
 * cashier acts on stay live where they are read — the pending count in the sync
 * panel, and the resting clock in the component.
 */
function shown(
  snapshot: EngineSnapshot,
  held: Dwelt,
  previous: Rendered | null,
): Rendered {
  const live = facts(snapshot);
  if (stateOf(live) === held.shown) return render(live);
  return previous ?? render(live);
}

/** How often a queued transition is checked for having served its dwell. */
const DWELL_TICK_MS = 500;

export function SyncProvider({ children }: { children: ReactNode }) {
  const me = useMe();
  const [device, setDevice] = useState<DeviceRecord | null>(() => readDevice());
  const [database, setDatabase] = useState<SyncDatabase | null>(null);
  const [snapshot, setSnapshot] = useState<EngineSnapshot | null>(null);
  const [line, setLine] = useState<Rendered | null>(null);
  const [registry, setRegistry] = useState<RegistryResponse | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const engine = useRef<SyncEngine | null>(null);
  // `shownSince: 0` rather than `Date.now()`: the first state the engine
  // reports has nothing to dwell behind, so it should land immediately.
  const held = useRef<Dwelt>({ shown: "synced", shownSince: 0, queued: null });
  /** The line for `held.shown`, kept so a dwell holds a line and not a name. */
  const lastLine = useRef<Rendered | null>(null);

  /**
   * **A4, and it is the whole boundary in one expression.**
   *
   * The provider is mounted for every role, because `/counter` is a route every
   * role can open and a surface that threw for an owner would be a crash rather
   * than a boundary. What is gated is the *engine*: an office identity opens no
   * store, starts no replication and issues no `/api/sync/pull` — **even on a
   * browser that is already a device**, which is the case an owner creates when
   * they claim the till they are sitting at from the device list.
   *
   * A device record with no session behind it is a browser somebody signed out
   * of. It keeps its store and its outbox; what it does not do is replicate.
   */
  const ready =
    device !== null && me.data !== undefined && isTillRole(me.data?.role);

  useEffect(() => {
    if (!ready || !device) return;
    let stopped = false;
    void (async () => {
      const store = await openStore(device.id);
      if (stopped) return;
      setDatabase(store);
      const running = new SyncEngine(store, device);
      engine.current = running;
      // §B.9.1's dwell is computed here rather than in an effect, because the
      // engine is the external system the state comes from and the dwell is a
      // property of that state, not of the view rendering it.
      running.subscribe((next) => {
        if (stopped) return;
        held.current = dwell(held.current, stateOf(facts(next)), Date.now());
        const line = shown(next, held.current, lastLine.current);
        lastLine.current = line;
        setSnapshot(next);
        setLine(line);
      });
      try {
        const answer = await fetchRegistry(device);
        if (stopped) return;
        setRegistry(answer);
        await running.start(answer);
      } catch {
        // The first sync needs the network and the first-sync screen says so.
        // A till that was already synced comes up on what it already has, which
        // is criterion 5 — a 2,5 s cold start with the plug out.
        if (!stopped) await running.start(offlineRegistry(device));
      }
    })();
    return () => {
      stopped = true;
      void engine.current?.stop();
      engine.current = null;
    };
  }, [ready, device]);

  // A transition held back by the dwell has to land on its own when the two
  // seconds are up, even if nothing else changes.
  useEffect(() => {
    const timer = setInterval(() => {
      const current = engine.current?.current();
      if (!current || !held.current.queued) return;
      const before = held.current.shown;
      held.current = dwell(held.current, stateOf(facts(current)), Date.now());
      if (held.current.shown === before) return;
      const line = shown(current, held.current, lastLine.current);
      lastLine.current = line;
      setLine(line);
    }, DWELL_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  // The four events that make a till pull immediately (*The poll schedule*):
  // the browser's `online` event, the tab regaining focus, a successful push
  // (the engine's own), and `Sincronizar ahora`.
  useEffect(() => {
    const now = () => engine.current?.syncNow();
    const onVisible = () => {
      if (!document.hidden) now();
    };
    window.addEventListener("online", now);
    window.addEventListener("focus", now);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("online", now);
      window.removeEventListener("focus", now);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const adopt = useCallback((record: DeviceRecord) => {
    setDevice(writeDevice(record));
  }, []);

  const value = useMemo<SyncContextValue>(
    () => ({
      database,
      device,
      snapshot,
      line,
      registry,
      needsClaim: device === null && isTillRole(me.data?.role),
      syncNow: () => engine.current?.syncNow(),
      retryNow: () => engine.current?.retryNow(),
      adopt,
      panelOpen,
      setPanelOpen,
    }),
    [
      database,
      device,
      snapshot,
      line,
      registry,
      me.data?.role,
      adopt,
      panelOpen,
    ],
  );

  return <Sync value={value}>{children}</Sync>;
}

/**
 * What the engine starts on when the registry call itself could not be made.
 *
 * A till that was already synced must come up and be usable with no network at
 * all — criterion 5, the 2,5 s cold start with the plug out. It starts on the
 * device record it already has and the settings' own defaults, and the first
 * successful call replaces them.
 */
function offlineRegistry(device: DeviceRecord): RegistryResponse {
  return {
    version: 1,
    device_id: device.id,
    device_label: device.label,
    device_code: "",
    location_id: device.location_id,
    location_name: device.location_name,
    location_code: device.location_code,
    collections: [],
    server_time: new Date().toISOString(),
    clock_skew_ms: null,
    storage_persisted: device.persisted,
    storage_persistence_policy: "warn",
    pull_interval_seconds: 8,
    pull_page_size: 500,
    push_batch_max_rows: 200,
    push_batch_max_bytes: 1048576,
    local_retention_days: 30,
    clock_skew_warn_seconds: 90,
  };
}

export { requestPersistence };
