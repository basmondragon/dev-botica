import { useEffect, useState } from "react";
import { cn } from "@/ui/cn";
import { StatusDot } from "@/ui/status";
import { useSync } from "./context";
import { render } from "./state";

/**
 * §B.9.1 · `SyncStatus`. **One component, one state machine, rendered by every
 * surface that reads the local store and re-implemented by none.**
 *
 * At rest it is `Sincronizado hace 4 s` — bare text, no dot. **The dot appears
 * only when the state leaves `synced`**, which is what makes it noticeable; a
 * green dot on every screen all day is decoration, and a decoration that is
 * always there is one nobody reads when it changes.
 *
 * It never animates, never shows a percentage, and only `blocked` interrupts —
 * and at v1 nothing raises `blocked` (A6, §8).
 */

/** How often the relative time is recomputed. **The clock does not announce**
 *  (§B.9.1): only a state change reaches the live region. */
function useTick(intervalMs: number) {
  const [, bump] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => bump((count) => count + 1), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
}

export function SyncStatus({
  placement = "office",
  className,
}: {
  /** Office surfaces: the filter bar's right slot at 11px. Counter surfaces:
   *  `t-12` inside a 44px hit target that opens the panel. */
  placement?: "office" | "counter";
  className?: string;
}) {
  const sync = useSync();
  // Five seconds under a minute and thirty above it (§B.9.1). One interval is
  // used because the difference costs a re-render of one string.
  useTick(5000);

  if (!sync.line || !sync.snapshot) return null;
  // The dwelt state decides *which* line is shown; the clock inside it is
  // recomputed on every tick. `synced` is the only state whose string carries
  // one, so it is the only one re-rendered here.
  const label =
    sync.line.state === "synced"
      ? render(
          {
            blocking: false,
            degraded: null,
            online: true,
            networkFailures: 0,
            pending: 0,
            lastPullAt: sync.snapshot.lastPullAt,
          },
          new Date(),
        ).label
      : sync.line.label;

  /**
   * §B.9.1 · **a state change announces; the ticking clock does not.**
   *
   * The visible line carries the relative time and is recomputed every five
   * seconds; the live region carries the *state* and nothing that ticks, so a
   * screen reader is told when the till goes offline and is not told, twelve
   * times a minute, that it is still synced.
   */
  const announcement =
    sync.line.state === "synced" ? "Sincronizado" : sync.line.label;

  const body = (
    <>
      {sync.line.family ? (
        <StatusDot family={sync.line.family} dot={sync.line.dot} />
      ) : null}
      <span>{label}</span>
      <span role="status" aria-live="polite" className="sr-only">
        {announcement}
      </span>
    </>
  );

  if (placement === "counter") {
    return (
      <button
        type="button"
        // §B.13.3 · no single-letter shortcut on a till surface; the panel is
        // `F8`, bound where the layer lives.
        title="Estado de sincronización — F8"
        aria-label={`Estado de sincronización: ${label}`}
        aria-expanded={sync.panelOpen}
        onClick={() => sync.setPanelOpen(!sync.panelOpen)}
        className={cn(
          "inline-flex h-11 items-center gap-[7px] rounded-control px-3 text-12",
          "text-ink-label transition-[background-color] duration-140 ease-out",
          "hover:bg-hover-nav",
          className,
        )}
      >
        {body}
      </button>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-[7px] text-11 text-ink-label",
        className,
      )}
    >
      {body}
    </span>
  );
}

/**
 * §B.9.1 · the `blocked` banner's geometry, built here and **produced by
 * nothing**.
 *
 * The words are deliberately not written, here or in the design system: the
 * copy is bound to the first stage that ever raises the state, because live
 * copy specified for a condition that cannot occur is how a stale string ships.
 * The exhausted numbering lease was its only producer and Botica allocates no
 * fiscal numbers (A6, §8), so nothing at v1 renders this with real words.
 *
 * It is built anyway because a state machine a cashier has already learned is
 * not a place to retrofit the only interruption in the product.
 */
export function BlockedBanner({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div
      role="alert"
      className="rounded-card border border-edge-critical bg-tint-critical p-4"
    >
      <p className="text-14 font-medium text-ink">{title}</p>
      <p className="mt-1.5 text-12 text-ink-body">{body}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
