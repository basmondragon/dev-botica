import { DOT, since } from "@/ui/format";
import type { Family, Dot } from "@/ui/status";

/**
 * §B.9.1 · one state machine, rendered by every surface that reads the local
 * store and re-implemented by none.
 *
 * **Stated as transitions rather than as descriptions**, because five states
 * with prose definitions is how two surfaces end up disagreeing about what
 * `pending` means. The state is a pure function of four local facts — a
 * blocking condition, a named degraded reason, whether the browser reports
 * itself online and whether we heard back from the server, and the outbox depth
 * — evaluated in that order of precedence, top wins.
 */
export type SyncState =
  "blocked" | "degraded" | "offline" | "pending" | "synced";

/**
 * §B.9.1 forbids `Error de sincronización`: **every degraded reason is named.**
 *
 * Five come from a failed transport call. The sixth is listed apart because it
 * is not a call at all — a browser that refused persistent storage — and it
 * does not clear on the next successful call, because nothing about a
 * successful push makes the storage durable.
 */
export const DEGRADED_REASONS = {
  session_expired: "sesión vencida",
  rejected: "el servidor rechazó los datos",
  storage_full: "almacenamiento lleno",
  outdated: "versión desactualizada",
  /** Coined by S2 · §B.9.1 lists four and revocation is not among them, and it
   *  is the reason a till will most often see after an office action. */
  revoked: "este equipo fue dado de baja",
  /** Not a transport failure (§5, §B.9.4). */
  evictable: "el navegador puede borrar los datos sin enviar",
} as const;

export type DegradedReason = keyof typeof DEGRADED_REASONS;

/** The four local facts the state is computed from. Nothing else. */
export interface SyncFacts {
  /** **At v1 there is none, and no stage produces one.** The exhausted
   *  numbering lease was the only producer, and leases are not built (A6, §8).
   *  The state stays defined and unreachable, because the day a till genuinely
   *  must stop is not the day to design the product's only interruption from
   *  scratch. */
  blocking: boolean;
  /** The last push or pull failed for a **named** reason, or the browser
   *  refused persistence. */
  degraded: DegradedReason | null;
  online: boolean;
  /** Two consecutive transport calls that got **no server response**. A call
   *  that got no answer is not a call the server refused, and telling a cashier
   *  the system is failing when the shop's link is down teaches them to
   *  distrust the line that matters. */
  networkFailures: number;
  pending: number;
  lastPullAt: string | null;
}

export function stateOf(facts: SyncFacts): SyncState {
  if (facts.blocking) return "blocked";
  if (facts.degraded) return "degraded";
  if (!facts.online || facts.networkFailures >= 2) return "offline";
  if (facts.pending > 0) return "pending";
  return "synced";
}

export interface Rendered {
  state: SyncState;
  label: string;
  /** `synced` has none: **at rest it is text and nothing else.** A green dot on
   *  every screen all day is decoration, and a decoration that is always there
   *  is one nobody reads when it changes. */
  family: Family | null;
  dot: Dot;
}

const PLURAL = (count: number, one: string, many: string) =>
  count === 1 ? one : many;

/**
 * The line, in Spanish, from §B.9.1's table verbatim.
 *
 * **`degraded` never suppresses the pending count**: a till with nine unsent
 * rows and an expired session shows the reason, and the nine are in the sync
 * panel where a cashier can see they still exist.
 */
export function render(facts: SyncFacts, now: Date = new Date()): Rendered {
  const state = stateOf(facts);
  switch (state) {
    case "blocked":
      // Its words are deliberately unwritten, here and in the design system:
      // §B.9.1 binds the copy to the first stage that ever raises the state, and
      // live copy specified for a condition that cannot occur is how a stale
      // string ships. Nothing at v1 reaches this branch.
      return { state, label: "", family: "critical", dot: "solid" };
    case "degraded":
      return {
        state,
        label: `Sincronización con problemas ${DOT} ${
          DEGRADED_REASONS[facts.degraded!]
        }`,
        family: "warning",
        dot: "solid",
      };
    case "offline":
      return {
        state,
        label:
          facts.pending > 0
            ? `Sin conexión ${DOT} ${facts.pending} ${PLURAL(
                facts.pending,
                "por enviar",
                "por enviar",
              )}`
            : "Sin conexión",
        // Warning, **hollow**: the system is waiting on something outside
        // itself, which is exactly §B.7.2's ring.
        family: "warning",
        dot: "hollow",
      };
    case "pending":
      return {
        state,
        label: `Sincronizando ${DOT} ${facts.pending} ${PLURAL(
          facts.pending,
          "pendiente",
          "pendientes",
        )}`,
        // Informative, because the system is working. It is not a problem and
        // must never be dressed as one on a network that drops several times a
        // week.
        family: "info",
        dot: "solid",
      };
    default:
      return {
        state,
        label: facts.lastPullAt
          ? `Sincronizado ${since(facts.lastPullAt, now)}`
          : "Sincronizado",
        family: null,
        dot: "solid",
      };
  }
}

/**
 * §B.9.1 · **a state must dwell 2 seconds before it is replaced.** A connection
 * that flickers must not make the line flicker; a cashier who sees
 * `Sin conexión` blink four times in a second stops believing any of it.
 *
 * The clock is not a state change and is not held back by this — only the state
 * itself is, which is why `render` and `dwell` are separate.
 */
export const DWELL_MS = 2000;

export interface Dwelt {
  shown: SyncState;
  shownSince: number;
  queued: SyncState | null;
}

export function dwell(current: Dwelt, next: SyncState, at: number): Dwelt {
  if (next === current.shown) return { ...current, queued: null };
  if (at - current.shownSince < DWELL_MS) return { ...current, queued: next };
  return { shown: next, shownSince: at, queued: null };
}
