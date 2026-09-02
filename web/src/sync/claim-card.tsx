import { useState } from "react";
import { api, toApiError, ApiError } from "@/api/client";
import { useLocations, useMe } from "@/api/queries";
import { BrandSquare } from "@/ui/brand";
import { Button } from "@/ui/button";
import { count } from "@/ui/format";
import { Field, Input } from "@/ui/field";
import { Select } from "@/ui/select";
import { RegionError } from "@/ui/states";
import { COLLECTION_LABELS, type CollectionName } from "./registry";
import {
  recordFromClaim,
  requestPersistence,
  type DeviceRecord,
} from "./device";
import { useSync } from "./context";

/**
 * §B.8.4·5 · **Reclamar equipo**, on the sign-in card's geometry: a 380px L2
 * card on the canvas, no shell.
 *
 * Shown when a `cashier` signs in on a browser with no device record; **never
 * shown to an `owner` or `admin`**, who claim explicitly from the device list
 * if they mean to (A4). That is the boundary made operational: the counter is
 * local-first over its own sede, the office is server-authoritative over the
 * network and is never synced into a browser.
 */

const CARD =
  "w-[380px] max-w-full rounded-panel border border-edge-soft bg-surface p-8 shadow-plane";

export function ClaimGate({ children }: { children: React.ReactNode }) {
  const sync = useSync();
  if (sync.needsClaim) return <ClaimScreen />;
  // §B.9.4's one-time dialog, driven by the **stored** record rather than by
  // component state. The device is adopted the instant the server issues its
  // key — that key is returned once and this browser holds the only copy — so a
  // reload, a closed tab or a killed browser between the claim and the dialog
  // costs a dismissal, not the till's identity.
  if (
    sync.device &&
    sync.device.persisted === false &&
    !sync.device.persistence_dialog_seen
  ) {
    return <PersistenceDialog device={sync.device} onDismiss={sync.adopt} />;
  }
  // A device whose first sync has not finished is **not presented as ready**: a
  // catalog missing 1.400 products is worse than no catalog, because the
  // cashier searches, finds nothing, and concludes the product is broken.
  if (sync.device && sync.snapshot && !sync.snapshot.ready) {
    return <FirstSyncScreen />;
  }
  return <>{children}</>;
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas p-8">
      <div className={CARD}>
        <BrandSquare />
        <p className="mt-4 text-14 font-medium text-ink">Botica</p>
        {children}
      </div>
    </div>
  );
}

function ClaimScreen() {
  const me = useMe();
  const sync = useSync();
  // Only an office identity is ever offered a choice of sede, so the list is
  // only fetched for one — a `cashier`'s field is not a control (§2).
  const locations = useLocations(me.data?.role !== "cashier");
  const [label, setLabel] = useState("");
  // The sede is **derived** from the identity until an office identity picks
  // another one: a `cashier`'s home sede arrives with `/api/me`, and copying it
  // into state in an effect would render one frame with an empty field.
  const [chosen, setChosen] = useState<string | null>(null);
  const locationId = chosen ?? me.data?.location_id ?? "";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // A `cashier`'s sede is pre-filled and read-only (§2), so choosing another
  // one never happens — the field is not a control for them.
  const office = me.data?.role !== "cashier";

  async function claim() {
    setBusy(true);
    setError(null);
    // Requested **at claim**, and its answer travels with the claim so the
    // server records it on the device from the first moment (§B.9.4).
    const persisted = await requestPersistence();
    const {
      data,
      error: refusal,
      response,
    } = await api.POST("/api/devices/claim", {
      body: { label: label.trim(), location_id: locationId },
      headers:
        persisted === null
          ? undefined
          : { "X-Botica-Storage-Persisted": String(persisted) },
    });
    setBusy(false);
    if (refusal || !data) {
      setError(
        toApiError(response, refusal, "No pudimos registrar este equipo."),
      );
      return;
    }
    const record: DeviceRecord = {
      ...recordFromClaim(data),
      persisted,
      // A browser that granted or did not answer has nothing to be shown.
      persistence_dialog_seen: persisted !== false,
    };
    // **Adopted immediately, always.** The key the server just issued exists
    // nowhere else, and `ClaimGate` shows the persistence dialog off the stored
    // record's own flag on the very next render.
    sync.adopt(record);
  }

  return (
    <Frame>
      <h1 className="mt-5 text-20 text-ink">Registrar este equipo</h1>
      <p className="mt-2 text-12 text-ink-label">
        Este navegador se convierte en una caja de {me.data?.location_name}. El
        catálogo se descarga una vez y después funciona sin conexión.
      </p>

      <form
        className="mt-6 flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void claim();
        }}
      >
        <Field label="Nombre del equipo" htmlFor="device-label">
          <Input
            id="device-label"
            value={label}
            placeholder="Caja 1"
            autoFocus
            onChange={(event) => setLabel(event.target.value)}
          />
        </Field>

        <Field label="Sede" htmlFor="device-sede">
          {office ? (
            <Select
              id="device-sede"
              value={locationId}
              placeholder="Elija una sede"
              options={(locations.data ?? []).map((one) => ({
                value: one.id,
                label: one.name,
              }))}
              onValueChange={setChosen}
            />
          ) : (
            <Input
              id="device-sede"
              value={me.data?.location_name ?? ""}
              readOnly
              disabled
            />
          )}
        </Field>

        {error ? (
          <RegionError
            title="No pudimos registrar este equipo."
            detail={
              error.status === 0
                ? "Este equipo todavía no tiene el catálogo. Conéctelo a una red para prepararlo."
                : error.message
            }
            requestId={error.requestId}
            onRetry={() => void claim()}
          />
        ) : null}

        <Button
          type="submit"
          variant="primary"
          size="md"
          className="w-full"
          busy={busy}
          busyLabel="Registrando…"
          disabled={!label.trim() || !locationId}
        >
          Registrar equipo
        </Button>
      </form>
    </Frame>
  );
}

/**
 * §B.9.4 · the one-time dialog a browser that refused persistence gets, at
 * claim, explaining what to change.
 *
 * **Once.** The till keeps selling throughout, and the condition stays visible
 * as a chip in the sync panel and as a badge in the office list — a dialog an
 * operator dismisses every morning is a dialog they click through without
 * reading.
 */
function PersistenceDialog({
  device,
  onDismiss,
}: {
  device: DeviceRecord;
  onDismiss: (device: DeviceRecord) => void;
}) {
  return (
    <Frame>
      <h1 className="mt-5 text-20 text-ink">
        Este navegador puede borrar los datos
      </h1>
      <p className="mt-2 text-14 text-ink-body">
        El navegador no concedió almacenamiento protegido, así que puede borrar
        lo que este equipo todavía no ha enviado. El mostrador sigue vendiendo
        con normalidad.
      </p>
      <p className="mt-3 text-12 text-ink-label">
        Para protegerlo: abra Botica en Chrome, permita el almacenamiento del
        sitio y no use una ventana de incógnito. La oficina ve este equipo como
        «Almacenamiento sin proteger» hasta que cambie.
      </p>
      <Button
        variant="primary"
        size="md"
        className="mt-6 w-full"
        onClick={() => onDismiss({ ...device, persistence_dialog_seen: true })}
      >
        Entendido
      </Button>
    </Frame>
  );
}

/**
 * **Primera sincronización** — the same card, with the form replaced once the
 * device exists.
 *
 * Per-collection progress against the registry's **real** totals. Progress is
 * counts, never a percentage and never a spinner (§B.9.1, §B.10.1). An
 * interrupted first sync resumes from its checkpoint on the next attempt and
 * does not restart, and this screen never offers to start over — because
 * starting over is never the right answer.
 */
function FirstSyncScreen() {
  const sync = useSync();
  const progress = sync.snapshot?.progress ?? {};
  const rows = Object.entries(progress) as [
    CollectionName,
    { received: number; total: number },
  ][];
  const stalled = sync.snapshot?.degraded ?? null;
  // **`navigator.onLine` tracks the interface, not reachability.** A shop LAN
  // whose upstream is down reports itself online and answers nothing, which is
  // the ordinary case — so a call that got no answer counts here too, or this
  // card sits on a progress list that never moves and says nothing at all.
  const unreachable = (sync.snapshot?.networkFailures ?? 0) > 0;
  const offline = sync.snapshot ? !sync.snapshot.online : false;

  return (
    <Frame>
      <h1 className="mt-5 text-20 text-ink">Preparando este equipo</h1>
      <p className="mt-2 text-12 text-ink-label">
        {sync.device?.label} · {sync.device?.location_name}
      </p>

      <dl className="mt-6 flex flex-col gap-2.5">
        {rows.length === 0 ? (
          <p className="text-12 text-ink-soft">
            Consultando qué debe descargar este equipo…
          </p>
        ) : null}
        {rows.map(([name, one]) => (
          <div key={name} className="flex items-baseline justify-between gap-4">
            <dt className="text-12 text-ink-body">
              {COLLECTION_LABELS[name] ?? name}
            </dt>
            <dd className="text-12 tabular-nums text-ink">
              {count(Math.min(one.received, one.total))} de {count(one.total)}
            </dd>
          </div>
        ))}
      </dl>

      {stalled || offline || unreachable ? (
        <div className="mt-5">
          <RegionError
            title="No pudimos descargar el catálogo."
            detail={
              offline || unreachable
                ? "Necesita conexión para preparar este equipo. La descarga continúa donde quedó."
                : `${sync.snapshot?.lastError || "El servidor no respondió."} La descarga continúa donde quedó.`
            }
            requestId={sync.snapshot?.requestId || undefined}
            onRetry={sync.retryNow}
          />
        </div>
      ) : null}
    </Frame>
  );
}
