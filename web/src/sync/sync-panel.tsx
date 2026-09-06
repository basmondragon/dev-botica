import { useEffect, useRef, useState } from "react";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { DOT, count, decimal, since } from "@/ui/format";
import { StatusDot } from "@/ui/status";
import { QUEUE_LABELS, queueGroups, type PolicyDoc } from "./registry";
import type { SyncDatabase } from "./store";
import { storageUsedBytes } from "./device";
import { useSync } from "./context";

/**
 * §B.9.3 · the sync panel. An L3 popover, 320px, opened from the status line or
 * `F8` (§B.13.3).
 *
 * **It is a read-out, not a control panel**: nothing in it changes what syncs.
 * Every figure is local state, so it renders identically with the network gone
 * — which is the only condition under which anyone urgently wants to read it.
 *
 * **The numbering line is absent, not zeroed, and nothing at v1 adds it.** No
 * stage allocates a fiscal range (A6, §8), and §B.9.2 tier 3 is explicit that a
 * zero never stands in for a thing that does not exist.
 */

const MEGABYTE = 1024 * 1024;

function Line({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-11 text-ink-label">{label}</span>
      <span className="text-12 text-ink">{children}</span>
    </div>
  );
}

export function SyncPanel({ className }: { className?: string }) {
  const sync = useSync();
  const [used, setUsed] = useState<number | null>(null);
  const [rulesAt, setRulesAt] = useState<string | null>(null);
  const frame = useRef<HTMLDivElement | null>(null);
  const { panelOpen, setPanelOpen } = sync;
  const database = sync.database;

  useEffect(() => {
    if (!panelOpen) return;
    void storageUsedBytes().then(setUsed);
  }, [panelOpen]);

  // S8 · **the mined rules' freshness, stated once and here.** The percentage
  // inside a `Se lleva junto` reason is a figure from that run and can be a week
  // old; it carries no per-card marker, because forty dots on a counter screen
  // is the alarm fatigue §B.9.2's convention exists to prevent.
  useEffect(() => {
    if (!panelOpen || !database) return;
    let stale = false;
    void newestRuleAt(database).then((at) => {
      if (!stale) setRulesAt(at);
    });
    return () => {
      stale = true;
    };
  }, [panelOpen, database]);

  // A popover closes on `Escape` and on a click outside it (§B.13.1). It does
  // **not** trap focus: it is a read-out over a counter surface, and a till
  // whose focus is somewhere else is a till where the next scan goes into the
  // void (§B.13.3).
  useEffect(() => {
    if (!panelOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        setPanelOpen(false);
      }
    }
    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (frame.current?.contains(target)) return;
      // The status line is the toggle; letting its own click through here would
      // close and reopen the panel in one gesture.
      if ((target as HTMLElement).closest?.("[aria-expanded]")) return;
      setPanelOpen(false);
    }
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("mousedown", onPointerDown, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("mousedown", onPointerDown, true);
    };
  }, [panelOpen, setPanelOpen]);

  if (!sync.panelOpen || !sync.snapshot || !sync.device) return null;
  const snapshot = sync.snapshot;
  const queue = queueGroups(snapshot.queue);
  const skewSeconds =
    snapshot.clockSkewMs === null
      ? null
      : Math.round(Math.abs(snapshot.clockSkewMs) / 1000);
  const skewShown =
    skewSeconds !== null && skewSeconds > snapshot.clockSkewWarnSeconds;

  return (
    <div
      ref={frame}
      role="dialog"
      aria-label="Estado de sincronización"
      className={cn(
        "z-40 flex w-80 flex-col gap-3 rounded-panel border border-edge-soft",
        "bg-surface p-4 shadow-overlay",
        className,
      )}
    >
      <Line label="Última descarga">
        {snapshot.lastPullAt ? since(snapshot.lastPullAt) : "—"}
      </Line>
      <Line label="Último envío">
        {snapshot.lastPushAt ? since(snapshot.lastPushAt) : "—"}
      </Line>
      {rulesAt ? (
        <Line label="Reglas del asistente">{since(rulesAt)}</Line>
      ) : null}

      {/* §B.9.3 · the pending queue broken down by kind. At S2 that is
          `Clientes 1`; S3 adds `Movimientos` and S4 `Ventas` by adding a label,
          not a second queue — and S4's own lines and payments are counted under
          the sale they belong to rather than as rows of their own. */}
      <Line label="Pendientes por enviar">
        {queue.length === 0
          ? "Nada pendiente"
          : queue
              .map(
                ([collection, total]) =>
                  `${QUEUE_LABELS[collection] ?? collection} ${count(total)}`,
              )
              .join(` ${DOT} `)}
      </Line>

      <div className="border-t border-hairline pt-3">
        <Line label="Equipo">{sync.device.label}</Line>
        <div className="mt-3">
          <Line label="Sede">{sync.device.location_name}</Line>
        </div>
      </div>

      <div className="border-t border-hairline pt-3">
        {/* §B.9.4 · granted is one positive line and nothing else; denied is
            the persistent chip that stays for as long as the state does. */}
        <div className="flex items-center gap-[7px]">
          <StatusDot
            family={
              snapshot.storagePersisted === false ? "warning" : "positive"
            }
            dot={snapshot.storagePersisted === null ? "hollow" : "solid"}
          />
          <span className="text-12 text-ink">
            {snapshot.storagePersisted === false
              ? "Almacenamiento sin proteger"
              : snapshot.storagePersisted === null
                ? "Almacenamiento sin confirmar"
                : "Almacenamiento protegido"}
          </span>
        </div>
        {snapshot.storagePersisted === false ? (
          <p className="mt-1.5 text-11 text-ink-soft">
            El navegador puede borrar los datos sin enviar. Este equipo sigue
            vendiendo.
          </p>
        ) : null}
        {used !== null ? (
          <p className="mt-2 text-11 text-ink-soft">
            Espacio usado {DOT} {decimal(used / MEGABYTE)} MB
          </p>
        ) : null}
      </div>

      {/* Skew beyond the threshold renders a line here and on the office list,
          and is **never silently corrected** (§5 rule 4). It does not change
          the sync state: none of §B.9.1's five means "this machine's clock is
          wrong", and inventing a sixth would put a clock problem in the
          vocabulary a cashier uses to judge whether their sales have been
          sent. */}
      {skewShown ? (
        <p className="border-t border-hairline pt-3 text-12 text-ink-body">
          El reloj de este equipo está {clockPhrase(snapshot.clockSkewMs!)} del
          servidor.
        </p>
      ) : null}

      <div className="flex justify-end border-t border-hairline pt-3">
        {/* §B.9.3 · the panel's one control, and it is not a control over what
            syncs — it is a person saying *try again now*, which is what clears
            a refusal the server made. */}
        <Button size="sm" variant="secondary" onClick={sync.retryNow}>
          Sincronizar ahora
        </Button>
      </div>
    </div>
  );
}

function clockPhrase(skewMs: number) {
  const minutes = Math.round(Math.abs(skewMs) / 60000);
  const magnitude =
    minutes >= 60
      ? `${count(Math.round(minutes / 60))} h`
      : `${count(Math.max(1, minutes))} min`;
  return `${magnitude} ${skewMs > 0 ? "adelantado" : "atrasado"}`;
}

/** The freshest `computed_at` on any rule this till holds. Null where the
 *  tenant has none, which is a new network's normal first state and not a
 *  staleness anybody needs told about. */
async function newestRuleAt(database: SyncDatabase): Promise<string | null> {
  const rows = (await database.collections
    .stock_policies!.find({ selector: { kind: "rule" } })
    .exec()) as unknown as PolicyDoc[];
  let newest: string | null = null;
  for (const row of rows) {
    const at = row.computed_at ?? null;
    if (at && (!newest || at > newest)) newest = at;
  }
  return newest;
}
