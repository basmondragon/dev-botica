import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import type { ItemRow } from "@/api/catalog";
import { useCreateReceipt, useScan } from "@/api/inventory";
import { useLocations, type Me } from "@/api/queries";
import { Content, TopBar, TopBarButton } from "@/shell/shell";
import { useSync } from "@/sync/context";
import { queueReceiptLines, scanBarcode } from "@/sync/local";
import { SyncStatus } from "@/sync/sync-status";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { DOT, TIMES, count, money } from "@/ui/format";
import { Select } from "@/ui/select";
import { EmptyState, RegionError } from "@/ui/states";
import { Table, TableFrame, TableScroll, Td, Th, Tr } from "@/ui/table";
import { useToast } from "@/ui/toast";
import { InventoryBreadcrumb } from "./breadcrumb";

export interface ReceiveSearch {
  sede?: string;
  settings?: string;
}

interface Line {
  key: string;
  /** A5 · **the line's own idempotency key, minted at the scan and kept across
   *  every retry.** A `Confirmar entrada` that times out after the server
   *  committed is retried by the person holding the box, and without this the
   *  server derives a fresh key and books the whole entry twice. */
  client_uuid: string;
  item: ItemRow;
  lot_code: string;
  expires: string;
  packs: string;
  unit_cost: string;
}

/**
 * §B.11 · **Inventario · Cargar mercancía**, at Counter density.
 *
 * A capture field at the top holding focus permanently, a line list below it,
 * and a footer with the line count and the primary `Confirmar entrada`. A scan
 * resolves through `item_barcodes`.
 *
 * **No single-letter shortcuts anywhere on this surface** (§B.13.3): a scanner
 * is a keyboard, a scan is a burst of characters followed by `Enter`, and a
 * bound `j` here fires an action off a barcode. Focus returns to the capture
 * field after every action.
 *
 * **Receiving asks in packs and converts on the way in**, showing both figures
 * on the line before it is confirmed -- `12 cajas · 360 unidades`. The
 * alternative is the classic inventory bug that survives a year: merchandise
 * received in packs, sold in units, and a projection that reads a thirtieth of
 * the truth.
 *
 * **The surface is offline-capable**, because merchandise arrives whether or
 * not the internet is up and a box that cannot be received is a box that gets
 * sold from while being invisible.
 */
export function ReceivePage({ me, search }: { me: Me; search: ReceiveSearch }) {
  const navigate = useNavigate();
  const elevated = me.role !== "cashier";
  const locations = useLocations();
  const sync = useSync();
  const receipt = useCreateReceipt();
  const toast = useToast();

  const captureRef = useRef<HTMLInputElement | null>(null);
  const [code, setCode] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  // A5 · minted with the entry rather than with the request, so a retry of a
  // request that already committed collides with itself instead of appending a
  // second copy. A fresh one is taken only after an entry has landed.
  const [documentId, setDocumentId] = useState(() => crypto.randomUUID());
  const [notFound, setNotFound] = useState<string | null>(null);
  const [location, setLocation] = useState(
    search.sede ?? sync.device?.location_id ?? me.location_id ?? "",
  );

  const scan = useScan();
  const [scanning, setScanning] = useState(false);
  const [scanFailed, setScanFailed] = useState<string | null>(null);

  useEffect(() => {
    captureRef.current?.focus();
  }, []);

  /**
   * **The local store first, the server only when there is no local store.**
   *
   * Acceptance 19 pulls the cable and expects this surface to keep accepting
   * scans -- so a till resolves out of `item_barcodes`, which the registry
   * already replicates, and never waits on a round trip it may not get. An
   * office browser is not a device and has no store, so it asks the server;
   * that surface is online anyway, because a receipt it cannot queue is a
   * receipt it must not accept.
   */
  async function resolve(scanned: string) {
    setScanning(true);
    setScanFailed(null);
    try {
      const found = sync.database
        ? await scanBarcode(sync.database, scanned)
        : await scan.mutateAsync(scanned);
      if (found) {
        setLines((current) => [...current, blank(found as ItemRow)]);
        setNotFound(null);
      } else {
        setNotFound(scanned);
      }
    } catch (failure) {
      // **A refusal is not "no such code".** Telling somebody holding a box
      // that their product does not exist, when the call simply failed, is the
      // wrong answer and the one they cannot act on.
      setScanFailed(
        failure instanceof Error
          ? failure.message
          : "No pudimos resolver este código.",
      );
    } finally {
      setScanning(false);
      captureRef.current?.focus();
    }
  }

  const units = useMemo(
    () => lines.reduce((total, line) => total + baseUnits(line), 0),
    [lines],
  );

  const offline = !!sync.snapshot && !sync.snapshot.online;
  // §B.10.3 · a refusal names where it happened. `POST /api/receipts` answers a
  // refused line with its index and the control on it, so the message goes on
  // that line and the box is marked invalid; a refusal with neither -- an
  // outage, a 403 -- stays region scope, which is the right scope for it.
  const refused =
    receipt.error instanceof ApiError &&
    receipt.error.line !== undefined &&
    receipt.error.line < lines.length
      ? {
          line: receipt.error.line,
          field: receipt.error.field,
          message: receipt.error.message,
        }
      : null;
  const ready =
    !!location && lines.length > 0 && lines.every((line) => valid(line));

  async function confirm() {
    const payload = lines.map((line) => ({
      client_uuid: line.client_uuid,
      item_id: line.item.id,
      lot_code: line.lot_code.trim(),
      expires_at: toIsoMonth(line.expires),
      quantity: baseUnits(line),
      unit_cost: line.unit_cost.trim() || null,
    }));
    if (offline && sync.database) {
      // A queued line carries no sede: the push writer books it at the device's
      // own, which is the only sede a till can be at. Offering another one and
      // then ignoring it would be the worst kind of quiet wrong answer, so the
      // selector is locked to the device's sede whenever there is a store.
      // Queued with `client_uuid`, `device_id` and `occurred_at`, and applied
      // through the ledger service when it lands. **No loading state at all**
      // on an optimistic write (§B.10.1).
      await queueReceiptLines(sync.database, documentId, payload);
      setLines([]);
      setDocumentId(crypto.randomUUID());
      toast(
        `Entrada guardada en este equipo · ${count(payload.length)} ` +
          `${payload.length === 1 ? "línea" : "líneas"} por enviar.`,
      );
      captureRef.current?.focus();
      return;
    }
    receipt.mutate(
      {
        location_id: location,
        document_id: documentId,
        reason: "standalone_receipt",
        note: "",
        lines: payload,
      },
      {
        onSuccess: (result) => {
          setLines([]);
          setDocumentId(crypto.randomUUID());
          toast(
            `Entrada confirmada · ${count(result.lines_written)} ` +
              `${result.lines_written === 1 ? "línea" : "líneas"}, ` +
              `${count(units)} unidades.`,
          );
          captureRef.current?.focus();
        },
      },
    );
  }

  return (
    <>
      <TopBar
        breadcrumb={<InventoryBreadcrumb />}
        title="Cargar mercancía"
        actions={
          <>
            {/* §B.9.1 · Counter placement: `t-12` inside a 44px hit target that
                opens the sync panel. Receiving is a Counter-density surface, so
                the line is the till's own and not the office's 11px chrome. */}
            <SyncStatus placement="counter" />
            <TopBarButton
              variant="secondary"
              onClick={() => void navigate({ to: "/inventory" })}
            >
              Ver existencias
            </TopBarButton>
          </>
        }
      />
      <Content className="flex flex-col gap-4">
        {offline ? (
          // The one real risk of receiving offline -- the same box booked in
          // twice, once as a receipt and once as a transfer -- and naming it is
          // cheaper than a check that needs the server anyway.
          <p className="text-12 text-ink-body">
            Sin conexión {DOT} no podemos comprobar si hay traslados en camino a
            esta sede.
          </p>
        ) : null}

        <div className="flex flex-wrap items-end gap-4">
          <Field
            label="Sede"
            className="w-64"
            help={
              sync.device
                ? "Este equipo carga mercancía en su propia sede."
                : undefined
            }
          >
            <Select
              value={location}
              onValueChange={setLocation}
              // A till is at one sede and its queued lines are booked there.
              disabled={me.role === "cashier" || !!sync.device}
              options={[
                { value: "", label: "Elija una sede" },
                ...(locations.data ?? []).map((one) => ({
                  value: one.id,
                  label: one.name,
                })),
              ]}
            />
          </Field>
          <Field
            label="Escanee o escriba el código"
            className="min-w-[280px] flex-1"
            error={
              scanFailed ??
              (notFound
                ? `Ningún producto tiene el código ${notFound}.`
                : undefined)
            }
          >
            <Input
              ref={captureRef}
              value={code}
              autoComplete="off"
              placeholder="Código de barras"
              onChange={(event) => setCode(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                const scanned = code.trim();
                // **The field is never disabled while a scan resolves** -- a
                // scanner does not wait, and a disabled capture field is where
                // the next burst goes into the void (§B.11). The guard is on
                // the lookup, not on the input.
                if (!scanned || scanning) return;
                setCode("");
                void resolve(scanned);
              }}
            />
          </Field>
        </div>

        {notFound && elevated ? (
          <Button
            size="sm"
            variant="secondary"
            className="self-start"
            onClick={() =>
              void navigate({
                to: "/inventory/catalog",
                search: { item: "nuevo" },
              })
            }
          >
            Crear producto
          </Button>
        ) : notFound ? (
          <p className="text-12 text-ink-label">
            Pida a la administradora que cree esta referencia en el catálogo.
          </p>
        ) : null}

        {lines.length === 0 ? (
          <EmptyState
            kind="deliberate"
            title="Escanee el primer producto"
            body="El cursor ya está en el campo de captura."
          />
        ) : (
          <TableFrame>
            <TableScroll>
              <Table minWidth={1000}>
                <thead>
                  <tr>
                    <Th width="28%">Producto</Th>
                    <Th width="13%">Lote</Th>
                    <Th width="11%">Vence</Th>
                    <Th width="11%" align="right">
                      Empaques
                    </Th>
                    <Th width="15%" align="right">
                      Unidades base
                    </Th>
                    <Th width="12%" align="right">
                      Costo
                    </Th>
                    {/* Reserved at rest, so removing a line reflows nothing
                        (§B.4.4's actions column). */}
                    <Th width="10%">
                      <span className="sr-only">Quitar</span>
                    </Th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((line, index) => (
                    <Tr key={line.key} density="counter">
                      <Td density="counter" truncate>
                        <span className="block truncate text-ink">
                          {line.item.name}
                        </span>
                        {refused?.line === index ? (
                          <span className="block truncate text-11 text-ink-critical">
                            {refused.message}
                          </span>
                        ) : null}
                      </Td>
                      <Td density="counter">
                        <Input
                          size="sm"
                          aria-label={`Lote de ${line.item.name}`}
                          value={line.lot_code}
                          invalid={
                            (line.item.tracks_lots && !line.lot_code.trim()) ||
                            (refused?.line === index &&
                              refused.field === "lot_code")
                          }
                          disabled={!line.item.tracks_lots}
                          placeholder={line.item.tracks_lots ? "" : "sin lote"}
                          onChange={(event) =>
                            update(setLines, index, {
                              lot_code: event.currentTarget.value,
                            })
                          }
                        />
                      </Td>
                      <Td density="counter">
                        <Input
                          size="sm"
                          aria-label={`Vencimiento de ${line.item.name}`}
                          placeholder="MM/AAAA"
                          value={line.expires}
                          invalid={
                            (line.item.tracks_expiry &&
                              !toIsoMonth(line.expires)) ||
                            (refused?.line === index &&
                              refused.field === "expires_at")
                          }
                          disabled={!line.item.tracks_expiry}
                          onChange={(event) =>
                            update(setLines, index, {
                              expires: event.currentTarget.value,
                            })
                          }
                        />
                      </Td>
                      <Td density="counter" align="right">
                        <Input
                          size="sm"
                          inputMode="numeric"
                          aria-label={`Empaques de ${line.item.name}`}
                          invalid={
                            refused?.line === index &&
                            refused.field === "quantity"
                          }
                          className="text-right"
                          value={line.packs}
                          onChange={(event) =>
                            update(setLines, index, {
                              packs: event.currentTarget.value,
                            })
                          }
                        />
                      </Td>
                      <Td density="counter" align="right" numeric>
                        {/* `12 cajas · 360 unidades`, shown before it is
                            confirmed. The conversion is stated on the line so
                            the person holding the box can see it. */}
                        {packLabel(line)}
                      </Td>
                      <Td density="counter" align="right">
                        <Input
                          size="sm"
                          inputMode="decimal"
                          aria-label={`Costo unitario de ${line.item.name}`}
                          className="text-right"
                          value={line.unit_cost}
                          onChange={(event) =>
                            update(setLines, index, {
                              unit_cost: event.currentTarget.value,
                            })
                          }
                        />
                      </Td>
                      <Td density="counter">
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => {
                            setLines((current) =>
                              current.filter(
                                (_one, position) => position !== index,
                              ),
                            );
                            captureRef.current?.focus();
                          }}
                        >
                          Quitar
                        </Button>
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </TableScroll>
            <div className="flex h-12 shrink-0 items-center justify-between gap-4 border-t border-hairline bg-chrome px-[22px]">
              <span className="text-11 tabular-nums text-ink-note">
                {count(lines.length)} {lines.length === 1 ? "línea" : "líneas"}{" "}
                {DOT} {count(units)} unidades base
              </span>
              {elevated ? (
                <Button
                  size="sm"
                  variant="primary"
                  busy={receipt.isPending}
                  disabled={!ready}
                  onClick={() => void confirm()}
                >
                  Confirmar entrada
                </Button>
              ) : (
                <span className="text-11 text-ink-label">
                  La entrada la confirma la administradora.
                </span>
              )}
            </div>
          </TableFrame>
        )}

        {receipt.isError && !refused ? (
          <RegionError
            title="No pudimos registrar la entrada."
            detail={(receipt.error as Error).message}
            requestId={
              receipt.error instanceof ApiError
                ? receipt.error.requestId
                : undefined
            }
            onRetry={() => void confirm()}
          />
        ) : null}

        {lines.length > 0 ? (
          <p className="text-11 text-ink-label">
            Valor de la entrada: {money(value(lines))}. Se registra al costo de
            adquisición, nunca al precio de venta.
          </p>
        ) : null}
      </Content>
    </>
  );
}

function blank(item: ItemRow): Line {
  return {
    key: `${item.id}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    client_uuid: crypto.randomUUID(),
    item,
    lot_code: "",
    expires: "",
    packs: "1",
    unit_cost: "",
  };
}

function update(
  setLines: React.Dispatch<React.SetStateAction<Line[]>>,
  index: number,
  patch: Partial<Line>,
) {
  setLines((current) =>
    current.map((one, position) =>
      position === index ? { ...one, ...patch } : one,
    ),
  );
}

/**
 * **The one conversion, in one place.** `units_per_pack` is the only conversion
 * in the product and it applies at receiving; the ledger counts base units
 * everywhere else.
 */
export function baseUnits(line: {
  item: Pick<ItemRow, "units_per_pack">;
  packs: string;
}): number {
  const packs = Number(line.packs);
  if (!Number.isFinite(packs) || packs <= 0) return 0;
  return Math.round(packs) * Math.max(1, line.item.units_per_pack);
}

export function packLabel(line: Line): string {
  const packs = Math.max(0, Math.round(Number(line.packs) || 0));
  const units = baseUnits(line);
  if (line.item.units_per_pack <= 1) return `${count(units)} ${line.item.unit}`;
  return `${count(packs)} ${TIMES} ${count(line.item.units_per_pack)} = ${count(units)}`;
}

function value(lines: Line[]): number {
  return lines.reduce(
    (total, line) => total + baseUnits(line) * (Number(line.unit_cost) || 0),
    0,
  );
}

function valid(line: Line): boolean {
  if (baseUnits(line) <= 0) return false;
  if (line.item.tracks_lots && !line.lot_code.trim()) return false;
  if (line.item.tracks_expiry && !toIsoMonth(line.expires)) return false;
  return true;
}

/**
 * `MM/AAAA` as the pharmacy writes it, to the `YYYY-MM-DD` the wire takes.
 *
 * A lot expires **at the end of its month**, which is what a box printed
 * `03/2027` means -- so the day is the last of that month and not the first,
 * and a lot is not `Vencido` for thirty days before it actually is.
 */
export function toIsoMonth(raw: string): string | null {
  const match = /^(\d{1,2})\s*\/\s*(\d{4})$/.exec(raw.trim());
  if (!match) return null;
  const month = Number(match[1]);
  const year = Number(match[2]);
  if (month < 1 || month > 12) return null;
  const last = new Date(year, month, 0).getDate();
  return `${year}-${String(month).padStart(2, "0")}-${String(last).padStart(2, "0")}`;
}
