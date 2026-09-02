import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import { ItemCombobox } from "@/catalog/item-combobox";
import {
  useCloseCount,
  useCounts,
  useCreateCount,
  useEnterCountLines,
  type CountRow,
} from "@/api/inventory";
import { useLocations, type Me } from "@/api/queries";
import { useSync } from "@/sync/context";
import { queueCountLine, scanBarcode } from "@/sync/local";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { DOT, count as formatCount, money, since } from "@/ui/format";
import { ConfirmDialog, Modal, RecordPanel } from "@/ui/panel";
import { Select } from "@/ui/select";
import { Badge } from "@/ui/status";
import { EmptyState, RegionError, RouteError } from "@/ui/states";
import { DataTable, TableFooter } from "@/ui/table";
import { useToast } from "@/ui/toast";
import { useListKeys } from "@/ui/use-list-keys";
import { InventoryBreadcrumb } from "./breadcrumb";
import { COUNT_STATUS } from "./vocabulary";

export interface CountsSearch {
  page?: number;
  pageSize?: number;
  conteo?: string;
  nuevo?: boolean;
  lote?: string;
  settings?: string;
}

/**
 * §B.8.1 · **Inventario · Conteos.**
 *
 * A cycle count reconciles the shelf to the record **by writing the difference
 * down, not by erasing it** (§6). The list flags a sede whose
 * `count_cadence_days` has elapsed; opening a count gives the counting surface.
 *
 * **The list is online-only and the counting surface is not**: a count is
 * walked around a back room where the wifi is worst. **Closing is online-only
 * too**, because it writes adjusting moves against a projection the device
 * cannot see whole.
 */
export function CountsPage({ me, search }: { me: Me; search: CountsSearch }) {
  const navigate = useNavigate();
  const elevated = me.role !== "cashier";
  const page = search.page ?? 1;
  const pageSize = search.pageSize ?? 25;
  // The due set rides on the list's own envelope, so the heading and the rows
  // arrive together rather than the screen rendering one before it knows the
  // other.
  const query = useCounts({ page, page_size: pageSize });
  const rows = query.data?.rows ?? [];
  const open = rows.find((one) => one.id === search.conteo);

  const go = (next: Partial<CountsSearch>) =>
    void navigate({
      to: "/inventory/counts",
      search: (previous: CountsSearch) => ({ ...previous, ...next }),
    });

  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `count-row-${index}`,
    pageKey: String(page),
    // §B.13.2 · `j`, `k`, `Enter`, `Esc` and page turning. **`x` is
    // deliberately unbound**, for the reason S1 gave on Catálogo: it toggles a
    // row into a bulk-action set, and this one has none -- a count is opened
    // at one sede and closed on its own. Wiring a
    // selection model with nothing to do with it would be a control that does
    // nothing. The first stage that gives this list a bulk action passes
    // `onToggleCheck` and `x` starts working with no other change.
    // §B.13.3 · **the scanner owns the keyboard while a count is open.** The
    // list and the counting surface share a route, so the list's own `j`/`k`
    // would still be listening at the window while somebody is scanning into
    // the panel -- and a scan is a burst of characters. The list keys go quiet
    // for as long as the capture surface is up.
    enabled: !search.conteo,
    onOpen: (index) => {
      const row = rows[index];
      if (row) go({ conteo: row.id });
    },
    onEscape: () => {
      if (!search.conteo) return false;
      go({ conteo: undefined });
      return true;
    },
  });

  const overdue = (query.data?.due_locations ?? []).filter((one) => one.due);

  if (query.isError) {
    return (
      <>
        <Header elevated={elevated} onNew={() => go({ nuevo: true })} />
        <Content>
          <RouteError
            title="No pudimos cargar los conteos."
            detail={
              query.error instanceof ApiError && query.error.status > 0
                ? query.error.message
                : "Esta lista necesita conexión. El conteo en sí sigue " +
                  "funcionando sin ella: es la lista lo que lee toda la red."
            }
            requestId={
              query.error instanceof ApiError
                ? query.error.requestId
                : undefined
            }
            onRetry={() => void query.refetch()}
          />
        </Content>
      </>
    );
  }

  return (
    <>
      <Header elevated={elevated} onNew={() => go({ nuevo: true })} />
      <div className="flex min-h-0 flex-1">
        <TableContent>
          {overdue.length > 0 ? (
            // Three names and a count, not twenty: a line that wraps to two
            // rows of sede names is a line nobody reads, and the figure is what
            // a person acts on.
            <p className="mb-4 text-12 text-ink-body">
              Con conteo pendiente:{" "}
              {overdue
                .slice(0, 3)
                .map((one) => one.location_name)
                .join(", ")}
              {overdue.length > 3
                ? ` y ${formatCount(overdue.length - 3)} sedes más`
                : ""}
              .
            </p>
          ) : null}
          <DataTable<CountRow>
            rows={rows}
            rowId={(row) => row.id}
            density="standard"
            minWidth={860}
            loading={query.isPending}
            refetching={query.isFetching && !query.isPending}
            skeletonRows={8}
            containerProps={keys.containerProps}
            rowProps={(row, index) => ({
              id: `count-row-${index}`,
              cursor: keys.cursor === index,
              current: search.conteo === row.id,
              onClick: () => go({ conteo: row.id }),
            })}
            empty={
              <EmptyState
                title="Todavía no hay conteos"
                body="Un conteo compara la estantería con el registro y escribe la diferencia; nunca sobrescribe una cantidad."
                actionLabel={elevated ? "Nuevo conteo" : undefined}
                onAction={elevated ? () => go({ nuevo: true }) : undefined}
              />
            }
            footer={
              <TableFooter
                page={page}
                pageSize={pageSize}
                rowCount={query.data?.row_count}
                loading={query.isPending}
                onPage={(next) => go({ page: next })}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
              />
            }
            columns={[
              {
                key: "location",
                label: "Sede",
                width: "20%",
                truncate: true,
                render: (row) => (
                  <span className="text-ink">{row.location_name}</span>
                ),
              },
              {
                key: "scope",
                label: "Alcance",
                width: "18%",
                truncate: true,
                render: (row) =>
                  row.scope === "full"
                    ? "Toda la sede"
                    : (row.category_name ?? "Lista de productos"),
              },
              {
                key: "recorded_at",
                label: "Abierto",
                width: "14%",
                render: (row) => since(row.recorded_at),
              },
              {
                key: "lines",
                label: "Líneas",
                width: "10%",
                align: "right",
                numeric: true,
                render: (row) => formatCount(row.lines_count),
              },
              {
                key: "differences",
                label: "Diferencias",
                width: "12%",
                align: "right",
                numeric: true,
                render: (row) => formatCount(row.differences),
              },
              {
                key: "value",
                label: "Valor",
                width: "12%",
                align: "right",
                numeric: true,
                render: (row) => money(Number(row.difference_value)),
              },
              {
                key: "status",
                label: "Estado",
                width: "14%",
                render: (row) => {
                  const meaning = COUNT_STATUS[row.status]!;
                  return (
                    <Badge family={meaning.family} dot={meaning.dot}>
                      {meaning.label}
                    </Badge>
                  );
                },
              },
            ]}
          />
        </TableContent>

        {open ? (
          <CountPanel
            countRow={open}
            me={me}
            onClose={() => go({ conteo: undefined })}
          />
        ) : null}
      </div>

      <NewCount
        open={!!search.nuevo && elevated}
        onClose={() => go({ nuevo: undefined })}
        onCreated={(id) => go({ nuevo: undefined, conteo: id })}
      />
    </>
  );
}

function Header({ elevated, onNew }: { elevated: boolean; onNew: () => void }) {
  return (
    <TopBar
      breadcrumb={<InventoryBreadcrumb />}
      title="Conteos"
      actions={
        elevated ? (
          <TopBarButton variant="primary" onClick={onNew}>
            Nuevo conteo
          </TopBarButton>
        ) : null
      }
    />
  );
}

/**
 * The counting surface: **Counter density, a capture field, one line per scan,
 * and the expected quantity hidden until the line is entered** -- so the count
 * is a count and not a confirmation.
 *
 * **No single-letter shortcuts anywhere on this surface** (§B.13.3): a scanner
 * is a keyboard, and any surface where `j` means something is a surface where
 * scanning a product code navigates. Focus returns to the capture field after
 * every action.
 */
function CountPanel({
  countRow,
  me,
  onClose,
}: {
  countRow: CountRow;
  me: Me;
  onClose: () => void;
}) {
  const elevated = me.role !== "cashier";
  const enter = useEnterCountLines();
  const close = useCloseCount();
  const toast = useToast();
  const sync = useSync();
  const [item, setItem] = useState("");
  const [scanned, setScanned] = useState<{ id: string; name: string } | null>(
    null,
  );
  const [code, setCode] = useState("");
  const [scanFailed, setScanFailed] = useState<string | null>(null);
  const [quantity, setQuantity] = useState("");
  const [confirming, setConfirming] = useState(false);
  const captureRef = useRef<HTMLInputElement | null>(null);
  const quantityRef = useRef<HTMLInputElement | null>(null);
  const closed = countRow.status === "closed";
  const offline = !!sync.snapshot && !sync.snapshot.online;

  useEffect(() => {
    if (!closed) captureRef.current?.focus();
  }, [closed, countRow.lines.length]);

  async function resolve(value: string) {
    setScanFailed(null);
    // The local store where there is one -- a count is walked around a back
    // room where the wifi is worst, and a scan that needs the network is a
    // scan that does not happen there.
    const found = sync.database
      ? await scanBarcode(sync.database, value)
      : null;
    if (found) {
      setScanned({ id: found.id, name: found.name });
      setItem(found.id);
      quantityRef.current?.focus();
      return;
    }
    setScanFailed(
      sync.database
        ? `Ningún producto tiene el código ${value}. Búsquelo por nombre.`
        : "Este equipo no tiene el catálogo descargado. Busque el producto por nombre.",
    );
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!scanned || !quantity.trim()) return;
    const line = {
      item_id: scanned.id,
      counted_quantity: Number(quantity) || 0,
    };
    const clear = () => {
      setItem("");
      setScanned(null);
      setQuantity("");
      captureRef.current?.focus();
    };
    if (offline && sync.database) {
      // Queued with its own `client_uuid` and the device's clock, and applied
      // through the same writer the online path uses when it lands (rule 8).
      void queueCountLine(sync.database, {
        count_id: countRow.id,
        lot_id: null,
        ...line,
      }).then(clear);
      return;
    }
    enter.mutate({ id: countRow.id, lines: [line] }, { onSuccess: clear });
  }

  //  §5 rule 2 · **the open exceptions at this sede, counted or not.** A
  //  negative raised by a direct movement, a transfer receipt or the
  //  opening-stock command has no device behind it, so the device-scoped
  //  arrival queue on S2's own screen never shows it -- and this screen is
  //  where §5 says an oversell is resolved.
  const open = countRow.negatives;
  const pending = open.filter((one) => !one.counted);

  return (
    <>
      <RecordPanel
        title={`Conteo · ${countRow.location_name}`}
        open
        onClose={onClose}
        footer={
          elevated && !closed ? (
            <Button
              size="sm"
              variant="primary"
              busy={close.isPending}
              onClick={() => setConfirming(true)}
            >
              Cerrar conteo
            </Button>
          ) : undefined
        }
      >
        {open.length > 0 ? (
          <div className="mb-4 rounded-card border border-edge-critical bg-tint-critical p-4">
            <p className="text-12 text-ink-body">
              {open.length === 1
                ? "Esta sede tiene una referencia en negativo."
                : `Esta sede tiene ${open.length} referencias en negativo.`}{" "}
              {pending.length === 0
                ? "Todas están contadas: al cerrar, quedan resueltas en el mismo movimiento."
                : "Cuéntelas en este conteo y al cerrar quedan resueltas en el mismo movimiento."}
            </p>
            <ul className="mt-3 flex flex-col gap-1.5">
              {open.map((one) => (
                <li
                  key={one.conflict_id}
                  className="flex items-baseline justify-between gap-4"
                >
                  <span className="min-w-0 truncate text-12 text-ink">
                    {one.item_name}
                    {one.lot_code ? (
                      <span className="text-ink-label">
                        {" "}
                        {DOT} lote {one.lot_code}
                      </span>
                    ) : null}
                  </span>
                  <span className="shrink-0 text-12 tabular-nums text-ink">
                    {formatCount(one.quantity)}
                    <span className="ml-2 text-11 text-ink-label">
                      {one.counted ? "contada" : "sin contar"}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {!closed ? (
          <form className="flex flex-col gap-3" onSubmit={submit}>
            {/* §B.11 · Counter density and a capture field that holds focus.
                A count is walked with a scanner in one hand, so the code comes
                first and the quantity second -- and the surface never asks for
                a product by name unless the scan found nothing. */}
            <Field
              label="Escanee el producto"
              error={scanFailed ?? undefined}
              help={
                scanned
                  ? undefined
                  : "El cursor vuelve aquí después de cada línea."
              }
            >
              <Input
                ref={captureRef}
                autoComplete="off"
                placeholder="Código de barras"
                value={code}
                onChange={(event) => setCode(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter") return;
                  event.preventDefault();
                  const value = code.trim();
                  if (!value) return;
                  setCode("");
                  void resolve(value);
                }}
              />
            </Field>

            {scanned ? (
              <p className="text-14 text-ink">{scanned.name}</p>
            ) : (
              <Field label="…o búsquelo por nombre">
                <ItemCombobox
                  value={item}
                  type="product"
                  onChange={(id, row) => {
                    setItem(id);
                    if (row) setScanned({ id: row.id, name: row.name });
                  }}
                />
              </Field>
            )}

            <Field
              label="Contado, en unidades base"
              help="Lo que hay en la estantería. Lo que dice el registro se muestra después de guardar la línea."
            >
              <Input
                ref={quantityRef}
                inputMode="numeric"
                value={quantity}
                onChange={(event) => setQuantity(event.currentTarget.value)}
              />
            </Field>
            <Button
              type="submit"
              size="sm"
              variant="secondary"
              busy={enter.isPending}
              disabled={!scanned || !quantity.trim()}
              className="self-start"
            >
              Guardar línea
            </Button>
            {offline ? (
              <p className="text-11 text-ink-label">
                Sin conexión {DOT} las líneas quedan en este equipo y se envían
                al volver. El conteo se cierra en línea.
              </p>
            ) : null}
            {enter.isError ? (
              <RegionError
                title="No pudimos guardar la línea."
                detail={(enter.error as Error).message}
              />
            ) : null}
          </form>
        ) : null}

        <section className="mt-5 border-t border-hairline pt-4">
          <h3 className="font-mono text-10 uppercase tracking-eyebrow text-ink-note">
            Líneas contadas
          </h3>
          {countRow.lines.length === 0 ? (
            <p className="mt-3 text-12 text-ink-label">
              Todavía no hay líneas. Escanee o busque el primer producto.
            </p>
          ) : (
            <ul className="mt-3 flex flex-col gap-3">
              {countRow.lines.map((line) => (
                <li key={line.id}>
                  <p className="text-14 text-ink">{line.item_name}</p>
                  <p className="text-12 text-ink-label">
                    {line.lot_code ? `Lote ${line.lot_code} · ` : ""}
                    contado {formatCount(line.counted_quantity)} · registro{" "}
                    {formatCount(line.expected_quantity)} ·{" "}
                    <span
                      className={
                        line.difference === 0 ? "text-ink-label" : "text-ink"
                      }
                    >
                      {line.difference > 0
                        ? `+${formatCount(line.difference)}`
                        : formatCount(line.difference)}
                    </span>
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </RecordPanel>

      <ConfirmDialog
        open={confirming}
        title="Cerrar el conteo"
        body={`Al cerrar se registran ${formatCount(
          countRow.differences,
        )} movimientos de ajuste. Las diferencias quedan en el historial.`}
        confirmLabel="Cerrar conteo"
        busyLabel="Cerrando…"
        busy={close.isPending}
        onCancel={() => setConfirming(false)}
        onConfirm={() =>
          close.mutate(countRow.id, {
            onSuccess: () => {
              setConfirming(false);
              toast("Conteo cerrado. Las diferencias quedaron registradas.");
            },
          })
        }
      />
    </>
  );
}

function NewCount({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const locations = useLocations();
  const create = useCreateCount();
  const [location, setLocation] = useState("");

  return (
    <Modal
      open={open}
      title="Nuevo conteo"
      busy={create.isPending}
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            size="sm"
            busy={create.isPending}
            disabled={!location}
            onClick={() =>
              create.mutate(
                { location_id: location, scope: "full" },
                { onSuccess: (created) => onCreated(created.id) },
              )
            }
          >
            Abrir conteo
          </Button>
        </>
      }
    >
      <div className="mt-4 flex flex-col gap-4">
        <Field label="Sede">
          <Select
            value={location}
            onValueChange={setLocation}
            options={[
              { value: "", label: "Elija una sede" },
              ...(locations.data ?? []).map((one) => ({
                value: one.id,
                label: one.name,
              })),
            ]}
          />
        </Field>
        {create.isError ? (
          <RegionError
            title="No pudimos abrir el conteo."
            detail={(create.error as Error).message}
          />
        ) : null}
      </div>
    </Modal>
  );
}
