import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import type { Me } from "@/api/queries";
import { useLocations } from "@/api/queries";
import {
  useForceCloseShift,
  useReturn,
  useReturns,
  useSale,
  useSales,
  useShift,
  useShifts,
  useVoidSale,
  type ReturnDetail,
  type ReturnRow,
} from "@/api/counter";
import { useDevices } from "@/api/sync";
import { TableContent, TopBar } from "@/shell/shell";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Field, Textarea } from "@/ui/field";
import {
  ChipToggles,
  FilterBar,
  FilterChip,
  SearchField,
} from "@/ui/filter-bar";
import { DOT, count as formatCount, money, since, time } from "@/ui/format";
import { SaleFiscalReadOut } from "@/fiscal/sale-read-out";
import { Modal, RecordPanel } from "@/ui/panel";
import { Segmented } from "@/ui/segmented";
import { Badge, StatusLine } from "@/ui/status";
import { EmptyState, RouteError } from "@/ui/states";
import { DataTable, TableFooter } from "@/ui/table";
import { PAYMENT_METHOD, SALE_STATUS, SHIFT_STATUS } from "./vocabulary";

/**
 * `Mostrador` for the office.
 *
 * **An `owner` or an `admin` lands here rather than on a till**, because the nav
 * has seven items and §B.8.1 fixes that ceiling — an eighth item called
 * `Ventas` would cost a cashier attention every day to save an administrator a
 * click a month. The route header carries a three-segment control (§A.15.3) and
 * each segment is the same filter bar + table + record panel shape (§B.8.5).
 *
 * *If design rejects a segmented control used as navigation*, the alternative is
 * three rail items in the settings dialog; it is not a nav item, and this is
 * where that decision lives.
 *
 * **This surface is online-only**, which is the correct answer for the office
 * read model (§4): it shows the route-scope error with a retry, not a stale
 * table.
 */

export interface CounterSearch {
  segment?: "ventas" | "turnos" | "devoluciones";
  page?: number;
  pageSize?: number;
  /** Multi-select, so it is plural — the convention S3 fixed: `sede` is a
   *  single id on a receiving surface, `sedes` is a filter over several. */
  sedes?: string[];
  /** §9 · the grid contract's server sort. On a route it lives in the search
   *  params, so any view is a link. */
  sort?: string;
  order?: "asc" | "desc";
  /** The two segments filter different enums, so they take different keys: one
   *  `estado` would be a search param whose meaning changed with the segment,
   *  and a link somebody shared would land on the wrong filter. */
  estadoVenta?: "open" | "closed" | "voided";
  estadoTurno?: "open" | "closed";
  q?: string;
  venta?: string;
  turno?: string;
  devolucion?: string;
  settings?: string;
}

const SALE_FILTERS: { value: CounterSearch["estadoVenta"]; label: string }[] = [
  { value: undefined, label: "Todas" },
  { value: "open", label: "Abierta" },
  { value: "closed", label: "Cerrada" },
  { value: "voided", label: "Anulada" },
];

const SHIFT_FILTERS: { value: CounterSearch["estadoTurno"]; label: string }[] =
  [
    { value: undefined, label: "Todos" },
    { value: "open", label: "Abierto" },
    { value: "closed", label: "Cerrado" },
  ];

const SEGMENTS = [
  { value: "ventas" as const, label: "Ventas" },
  { value: "turnos" as const, label: "Turnos" },
  { value: "devoluciones" as const, label: "Devoluciones" },
];

export function CounterOffice({
  me,
  search,
}: {
  me: Me;
  search: CounterSearch;
}) {
  const navigate = useNavigate();
  const segment = search.segment ?? "ventas";
  const page = search.page ?? 1;
  const pageSize = search.pageSize ?? 25;
  const locations = useLocations();

  const go = (next: Partial<CounterSearch>) =>
    void navigate({
      to: "/counter",
      search: (previous) => ({ ...previous, ...next }) as CounterSearch,
    });

  return (
    <>
      <TopBar
        breadcrumb={["Mostrador"]}
        title="Ventas de la red"
        actions={
          <Segmented
            label="Vista de mostrador"
            value={segment}
            segments={SEGMENTS}
            onChange={(next) =>
              go({ segment: next, page: 1, venta: undefined, turno: undefined })
            }
          />
        }
      />
      {segment === "ventas" ? (
        <Sales
          me={me}
          search={search}
          page={page}
          pageSize={pageSize}
          go={go}
          locations={locations.data ?? []}
        />
      ) : segment === "turnos" ? (
        <Shifts
          search={search}
          page={page}
          pageSize={pageSize}
          go={go}
          locations={locations.data ?? []}
        />
      ) : (
        <Returns
          search={search}
          page={page}
          pageSize={pageSize}
          go={go}
          locations={locations.data ?? []}
        />
      )}
    </>
  );
}

/**
 * The filter bar's right slot carries **device provenance rather than a sync
 * line**, because this surface does not read a local store: it is the office's
 * form of the same question a cashier asks of the chip (§B.8.5, §B.9.1).
 */
function Provenance() {
  const devices = useDevices({ page: 1, page_size: 100, order: "asc" });
  const rows = devices.data?.rows ?? [];
  if (rows.length === 0) return null;
  const synced = rows
    .map((one) => one.last_synced_at)
    .filter((one): one is string => !!one)
    .sort();
  const newest = synced[synced.length - 1];
  // **Quiet is measured against the freshest till, not against the clock.** A
  // device a day behind the network is the support question -- *which till has
  // not synced, and since when* -- and reading it off the fleet rather than off
  // `Date.now()` keeps the line the same figure between two renders.
  const cutoff = newest
    ? new Date(new Date(newest).getTime() - DAY_MS).toISOString()
    : null;
  const quiet = rows.filter(
    (one) =>
      !one.last_synced_at || (cutoff !== null && one.last_synced_at < cutoff),
  );
  return (
    <>
      {newest
        ? `Equipos sincronizados ${since(newest)}`
        : "Equipos sin sincronizar"}
      {quiet.length > 0
        ? ` ${DOT} ${quiet.length} sin conectar${
            quiet[0]?.last_synced_at
              ? ` desde ${since(quiet[0].last_synced_at)}`
              : ""
          }`
        : ""}
    </>
  );
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** One sort column at a time; clicking a different column replaces the sort,
 *  and any change resets to the first page — the same rule `useGrid` applies to
 *  every other grid in the product. */
function toggleSort(
  search: CounterSearch,
  key: string,
): Partial<CounterSearch> {
  return {
    page: 1,
    sort: key,
    order: search.sort === key && search.order === "asc" ? "desc" : "asc",
  };
}

type Location = { id: string; name: string };
type Go = (next: Partial<CounterSearch>) => void;

function SedeChip({
  locations,
  selected,
  onChange,
}: {
  locations: Location[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <FilterChip
      label="Sede"
      value={selected.length ? `${selected.length}` : undefined}
    >
      {() => (
        <ChipToggles
          options={locations.map((one) => ({ value: one.id, label: one.name }))}
          selected={selected}
          onChange={onChange}
        />
      )}
    </FilterChip>
  );
}

// ---------------------------------------------------------------------------
// Ventas
// ---------------------------------------------------------------------------

function Sales({
  me,
  search,
  page,
  pageSize,
  go,
  locations,
}: {
  me: Me;
  search: CounterSearch;
  page: number;
  pageSize: number;
  go: Go;
  locations: Location[];
}) {
  const sedes = search.sedes ?? [];
  const order = search.order ?? "desc";
  const query = useSales({
    page,
    page_size: pageSize,
    sort: search.sort,
    order,
    location_id: sedes.length ? sedes : undefined,
    status: search.estadoVenta,
    q: search.q || undefined,
  });
  const rows = query.data?.rows ?? [];

  return (
    <>
      <FilterBar provenance={<Provenance />}>
        <SearchField
          value={search.q ?? ""}
          placeholder="Buscar por número de venta"
          onChange={(next) => go({ q: next, page: 1 })}
        />
        <SedeChip
          locations={locations}
          selected={sedes}
          onChange={(next) => go({ sedes: next, page: 1 })}
        />
        <FilterChip label="Estado" value={search.estadoVenta}>
          {(close) => (
            <div className="flex flex-col">
              {SALE_FILTERS.map((option) => (
                <button
                  key={option.value ?? "todos"}
                  type="button"
                  className="flex h-8 items-center rounded-control px-2.5 text-left text-12 text-ink-body hover:bg-hover-row"
                  onClick={() => {
                    go({ estadoVenta: option.value, page: 1 });
                    close();
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </FilterChip>
      </FilterBar>

      <TableContent>
        <div className="flex min-h-0 flex-1">
          <DataTable
            rows={rows}
            rowId={(row) => row.id}
            loading={query.isLoading}
            refetching={query.isFetching && !query.isLoading}
            empty={
              query.isError ? (
                <RouteError
                  title="No pudimos cargar las ventas."
                  detail="Revise la conexión y vuelva a intentarlo."
                  onRetry={() => void query.refetch()}
                />
              ) : search.q || sedes.length || search.estadoVenta ? (
                <EmptyState
                  kind="filtered"
                  title="Ninguna venta coincide con estos filtros"
                  body="Quite un filtro para ver más."
                  actionLabel="Quitar filtros"
                  onAction={() =>
                    go({
                      q: undefined,
                      sedes: undefined,
                      estadoVenta: undefined,
                      page: 1,
                    })
                  }
                />
              ) : (
                <EmptyState
                  title="Todavía no hay ventas"
                  body="Las ventas aparecen aquí en cuanto una caja empieza a vender."
                  actionLabel={me.location_id ? "Abrir Mostrador" : undefined}
                  onAction={
                    me.location_id ? () => go({ segment: "ventas" }) : undefined
                  }
                />
              )
            }
            columns={[
              {
                key: "number",
                label: "Venta",
                width: "12ch",
                render: (row) => (
                  <span className="tabular-nums">{row.number}</span>
                ),
              },
              {
                key: "location",
                label: "Sede",
                width: "12ch",
                truncate: true,
                render: (row) => row.location_name,
              },
              {
                key: "shift",
                label: "Turno",
                width: "10ch",
                render: (row) =>
                  row.shift_id ? (
                    <button
                      type="button"
                      className="text-brand hover:underline"
                      onClick={() =>
                        go({ segment: "turnos", turno: row.shift_id! })
                      }
                    >
                      Ver
                    </button>
                  ) : (
                    "—"
                  ),
              },
              {
                key: "recorded_at",
                label: "Fecha",
                width: "14ch",
                sortable: true,
                render: (row) => since(row.recorded_at as string),
              },
              {
                key: "items",
                label: "Ítems",
                width: "8ch",
                align: "right",
                numeric: true,
                render: (row) => formatCount(row.item_count),
              },
              {
                key: "total",
                label: "Total",
                width: "14ch",
                align: "right",
                numeric: true,
                sortable: true,
                render: (row) => money(Number(row.total)),
              },
              {
                key: "methods",
                label: "Medio",
                width: "14ch",
                truncate: true,
                render: (row) =>
                  row.methods.length
                    ? row.methods
                        .map((one) => PAYMENT_METHOD[one] ?? one)
                        .join(` ${DOT} `)
                    : "—",
              },
              {
                key: "status",
                label: "Estado",
                width: "16ch",
                render: (row) => {
                  const meaning = SALE_STATUS[row.status]!;
                  return <Badge family={meaning.family}>{meaning.label}</Badge>;
                },
              },
            ]}
            sort={search.sort}
            order={order}
            onSort={(key) => go(toggleSort(search, key))}
            rowProps={(row) => ({
              onClick: () => go({ venta: row.id }),
              className: cn(search.venta === row.id && "bg-hover-row"),
            })}
            footer={
              <TableFooter
                page={page}
                pageSize={pageSize}
                rowCount={query.data?.row_count}
                loading={query.isLoading}
                onPage={(next) => go({ page: next })}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
              />
            }
          />
          <SalePanel
            saleId={search.venta ?? null}
            onClose={() => go({ venta: undefined })}
          />
        </div>
      </TableContent>
    </>
  );
}

function SalePanel({
  saleId,
  onClose,
}: {
  saleId: string | null;
  onClose: () => void;
}) {
  const query = useSale(saleId);
  const voiding = useVoidSale();
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const sale = query.data;

  return (
    <>
      <RecordPanel
        title={sale ? `Venta ${sale.number}` : "Venta"}
        open={!!saleId}
        onClose={onClose}
        footer={
          sale && sale.status === "closed" ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setConfirming(true)}
            >
              Anular venta
            </Button>
          ) : null
        }
      >
        {!sale ? null : (
          <div className="flex flex-col gap-5">
            <dl className="flex flex-col gap-2 text-12">
              <Pair label="Sede" value={sale.location_name} />
              <Pair label="Cajera" value={sale.sold_by_name || "—"} />
              <Pair label="Equipo" value={sale.device_label ?? "—"} />
              <Pair
                label="Cliente"
                value={sale.customer_name ?? "Consumidor final"}
              />
              <Pair
                label="Registrada"
                value={`${since(sale.recorded_at as string)} ${DOT} en caja ${time(sale.occurred_at as string)}`}
              />
              {sale.status === "voided" && sale.void_reason ? (
                <Pair label="Motivo de anulación" value={sale.void_reason} />
              ) : null}
            </dl>

            <div>
              <p className="mb-2 text-11 uppercase tracking-eyebrow text-ink-note">
                Líneas
              </p>
              <ul className="flex flex-col gap-2">
                {sale.lines.map((line) => (
                  <li
                    key={line.id}
                    className="flex items-baseline gap-3 text-12"
                  >
                    <span className="min-w-0 flex-1 truncate text-ink">
                      {line.item_name}
                    </span>
                    <span className="shrink-0 tabular-nums text-ink-note">
                      {line.quantity} × {money(Number(line.unit_price))}
                    </span>
                    <span className="w-20 shrink-0 text-right tabular-nums text-ink">
                      {money(
                        Number(line.unit_price) * line.quantity -
                          Number(line.discount),
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <p className="mb-2 text-11 uppercase tracking-eyebrow text-ink-note">
                Pagos
              </p>
              <ul className="flex flex-col gap-2">
                {sale.payments.map((payment) => (
                  <li key={payment.id} className="flex justify-between text-12">
                    <span className="text-ink-body">
                      {PAYMENT_METHOD[payment.method] ?? payment.method}
                      {payment.reference ? ` ${DOT} ${payment.reference}` : ""}
                    </span>
                    <span className="tabular-nums text-ink">
                      {money(Number(payment.amount))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <dl className="flex flex-col gap-2 border-t border-hairline pt-4 text-12">
              <Pair label="Subtotal" value={money(Number(sale.subtotal))} />
              <Pair label="Descuento" value={money(Number(sale.discount))} />
              <Pair label="Total" value={money(Number(sale.total))} />
              {/* The IVA is **contained** in the total and is stated as such
                  rather than added to it (§3). */}
              <Pair label="IVA incluido" value={money(Number(sale.tax))} />
            </dl>

            {sale.returns.length > 0 ? (
              <div className="border-t border-hairline pt-4">
                <p className="mb-2 text-11 uppercase tracking-eyebrow text-ink-note">
                  Devoluciones
                </p>
                <ul className="flex flex-col gap-2">
                  {sale.returns.map((row) => (
                    <li key={row.id} className="flex justify-between text-12">
                      <span className="text-ink-body">
                        {row.number} {DOT} {since(row.recorded_at as string)}
                      </span>
                      <span className="tabular-nums text-ink">
                        {money(Number(row.total))}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {/* S5's read-out. **Nothing at all** when the client's invoicing
                system returned nothing, when the handoff has not landed, or
                when no target is configured -- which is the default and the
                state every demo runs in (§8). */}
            <SaleFiscalReadOut saleId={sale.id} />

            {/* **A devolución is registered at the counter**, which is where the
                stock and the customer are. The office reads them and starts
                none: every write in this stage originates on a device (§5). */}
            <p className="text-11 text-ink-note">
              Una devolución se registra en el mostrador de la sede que hizo la
              venta.
            </p>
          </div>
        )}
      </RecordPanel>

      <Modal
        open={confirming}
        title={`Anular la venta ${sale?.number ?? ""}`}
        size="confirm"
        busy={voiding.isPending}
        onClose={() => setConfirming(false)}
        footer={
          <>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setConfirming(false)}
            >
              Cancelar
            </Button>
            <Button
              size="sm"
              variant="destructive"
              confirming
              busy={voiding.isPending}
              busyLabel="Anulando"
              onClick={() => {
                if (!sale) return;
                voiding.mutate(
                  { saleId: sale.id, reason },
                  { onSuccess: () => setConfirming(false) },
                );
              }}
            >
              {`Anular ${sale?.number ?? ""}`}
            </Button>
          </>
        }
      >
        <div className="mt-4 flex flex-col gap-4">
          <p className="text-14 text-ink-body">
            Las existencias vuelven a su lote y la venta queda como anulada.
            Ninguna fila se borra. Si el documento ya llegó al sistema de
            facturación de la droguería, la corrección se hace allí.
          </p>
          <Field label="Motivo" htmlFor="void-reason" optional>
            <Textarea
              id="void-reason"
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          {voiding.isError ? (
            <p className="text-12 text-critical">
              {voiding.error instanceof Error
                ? voiding.error.message
                : "No pudimos anular la venta."}
            </p>
          ) : null}
        </div>
      </Modal>
    </>
  );
}

// ---------------------------------------------------------------------------
// Turnos
// ---------------------------------------------------------------------------

function Shifts({
  search,
  page,
  pageSize,
  go,
  locations,
}: {
  search: CounterSearch;
  page: number;
  pageSize: number;
  go: Go;
  locations: Location[];
}) {
  const sedes = search.sedes ?? [];
  const order = search.order ?? "desc";
  const query = useShifts({
    page,
    page_size: pageSize,
    sort: search.sort,
    order,
    location_id: sedes.length ? sedes : undefined,
    status: search.estadoTurno,
  });
  const rows = query.data?.rows ?? [];

  return (
    <>
      <FilterBar provenance={<Provenance />}>
        <SedeChip
          locations={locations}
          selected={sedes}
          onChange={(next) => go({ sedes: next, page: 1 })}
        />
        <FilterChip label="Estado" value={search.estadoTurno}>
          {(close) => (
            <div className="flex flex-col">
              {SHIFT_FILTERS.map((option) => (
                <button
                  key={option.value ?? "todos"}
                  type="button"
                  className="flex h-8 items-center rounded-control px-2.5 text-left text-12 text-ink-body hover:bg-hover-row"
                  onClick={() => {
                    go({ estadoTurno: option.value, page: 1 });
                    close();
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </FilterChip>
      </FilterBar>

      <TableContent>
        <div className="flex min-h-0 flex-1">
          <DataTable
            rows={rows}
            rowId={(row) => row.id}
            loading={query.isLoading}
            refetching={query.isFetching && !query.isLoading}
            empty={
              query.isError ? (
                <RouteError
                  title="No pudimos cargar los turnos."
                  detail="Revise la conexión y vuelva a intentarlo."
                  onRetry={() => void query.refetch()}
                />
              ) : (
                <EmptyState
                  title="Todavía no hay turnos"
                  body="Un turno aparece aquí en cuanto una cajera abre su caja."
                />
              )
            }
            columns={[
              {
                key: "location",
                label: "Sede",
                width: "14ch",
                truncate: true,
                render: (row) => row.location_name,
              },
              {
                key: "device",
                label: "Equipo",
                width: "14ch",
                render: (row) => row.device_label ?? "—",
              },
              {
                key: "opened_at",
                label: "Abrió",
                width: "16ch",
                sortable: true,
                render: (row) => (
                  <span>
                    {row.user_name || "—"}{" "}
                    <span className="text-ink-note">
                      {time(row.opened_at as string)}
                    </span>
                  </span>
                ),
              },
              {
                key: "opening_float",
                label: "Apertura",
                width: "12ch",
                align: "right",
                numeric: true,
                render: (row) => money(Number(row.opening_float)),
              },
              {
                key: "declared_total",
                label: "Declarado",
                width: "12ch",
                align: "right",
                numeric: true,
                render: (row) =>
                  row.declared_total === null
                    ? "—"
                    : money(Number(row.declared_total)),
              },
              {
                key: "expected_total",
                label: "Esperado",
                width: "12ch",
                align: "right",
                numeric: true,
                render: (row) => money(Number(row.expected_total)),
              },
              {
                key: "variance",
                label: "Diferencia",
                width: "10ch",
                align: "right",
                numeric: true,
                sortable: true,
                // §B.12.3 · the colour is never the only signal — the figure
                // carries its own sign and the record panel restates it.
                render: (row) =>
                  row.variance === null ? (
                    "—"
                  ) : (
                    <span
                      className={cn(
                        Number(row.variance) === 0
                          ? "text-ink"
                          : Math.abs(Number(row.variance)) >= 50000
                            ? "text-critical"
                            : "text-warning",
                      )}
                    >
                      {money(Number(row.variance))}
                    </span>
                  ),
              },
              {
                key: "status",
                label: "Estado",
                width: "10ch",
                render: (row) => {
                  const meaning = SHIFT_STATUS[row.status]!;
                  return <Badge family={meaning.family}>{meaning.label}</Badge>;
                },
              },
            ]}
            sort={search.sort}
            order={order}
            onSort={(key) => go(toggleSort(search, key))}
            rowProps={(row) => ({
              onClick: () => go({ turno: row.id }),
              className: cn(search.turno === row.id && "bg-hover-row"),
            })}
            footer={
              <TableFooter
                page={page}
                pageSize={pageSize}
                rowCount={query.data?.row_count}
                loading={query.isLoading}
                onPage={(next) => go({ page: next })}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
              />
            }
          />
          <ShiftPanel
            shiftId={search.turno ?? null}
            onClose={() => go({ turno: undefined })}
          />
        </div>
      </TableContent>
    </>
  );
}

function ShiftPanel({
  shiftId,
  onClose,
}: {
  shiftId: string | null;
  onClose: () => void;
}) {
  const query = useShift(shiftId);
  const forcing = useForceCloseShift();
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const shift = query.data;

  return (
    <>
      <RecordPanel
        title={shift ? `Turno ${shift.location_name}` : "Turno"}
        open={!!shiftId}
        onClose={onClose}
        footer={
          shift && shift.status === "open" ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setConfirming(true)}
            >
              Forzar cierre
            </Button>
          ) : null
        }
      >
        {!shift ? null : (
          <div className="flex flex-col gap-5">
            <dl className="flex flex-col gap-2 text-12">
              <Pair label="Equipo" value={shift.device_label ?? "—"} />
              <Pair label="Abrió" value={shift.user_name || "—"} />
              <Pair label="Apertura" value={time(shift.opened_at as string)} />
              <Pair
                label="Cierre"
                value={
                  shift.closed_at
                    ? time(shift.closed_at as string)
                    : "Sin cerrar"
                }
              />
              <Pair label="Ventas" value={formatCount(shift.sale_count)} />
            </dl>

            <dl className="flex flex-col gap-2 border-t border-hairline pt-4 text-12">
              <Pair
                label="Efectivo inicial"
                value={money(Number(shift.opening_float))}
              />
              <Pair
                label="Ventas en efectivo"
                value={money(Number(shift.cash_sales))}
              />
              <Pair
                label="Devoluciones en efectivo"
                value={money(-Number(shift.cash_returns))}
              />
              <Pair
                label="Efectivo esperado"
                value={money(Number(shift.expected_total))}
              />
              <Pair
                label="Efectivo contado"
                value={
                  shift.declared_total === null
                    ? "Sin conteo"
                    : money(Number(shift.declared_total))
                }
              />
              <Pair
                label="Diferencia"
                value={
                  shift.variance === null
                    ? "Sin conteo"
                    : money(Number(shift.variance))
                }
              />
            </dl>

            {shift.forced_close_reason ? (
              // A forced close is not a count and is never rendered as one.
              <StatusLine
                family="warning"
                label={`Cierre forzado ${DOT} ${shift.forced_close_reason}`}
              />
            ) : null}

            <div className="border-t border-hairline pt-4">
              <p className="mb-2 text-11 uppercase tracking-eyebrow text-ink-note">
                Medios de pago
              </p>
              <ul className="flex flex-col gap-2">
                {shift.payments.map((row) => (
                  <li key={row.method} className="flex justify-between text-12">
                    <span className="text-ink-body">
                      {PAYMENT_METHOD[row.method] ?? row.method}
                    </span>
                    <span className="tabular-nums text-ink">
                      {money(Number(row.amount))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </RecordPanel>

      <Modal
        open={confirming}
        title="Forzar el cierre del turno"
        size="confirm"
        busy={forcing.isPending}
        onClose={() => setConfirming(false)}
        footer={
          <>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => setConfirming(false)}
            >
              Cancelar
            </Button>
            <Button
              size="sm"
              variant="destructive"
              confirming
              busy={forcing.isPending}
              busyLabel="Cerrando"
              disabled={!reason.trim()}
              onClick={() => {
                if (!shift) return;
                forcing.mutate(
                  { shiftId: shift.id, reason: reason.trim() },
                  { onSuccess: () => setConfirming(false) },
                );
              }}
            >
              Forzar cierre
            </Button>
          </>
        }
      >
        <div className="mt-4 flex flex-col gap-4">
          <p className="text-14 text-ink-body">
            El turno queda cerrado **sin conteo**: no se guarda efectivo
            declarado ni diferencia, porque nadie contó la caja. Úselo cuando el
            equipo ya no existe o la cajera se fue sin cerrar.
          </p>
          <Field label="Motivo" htmlFor="force-reason" required>
            <Textarea
              id="force-reason"
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
        </div>
      </Modal>
    </>
  );
}

// ---------------------------------------------------------------------------
// Devoluciones
// ---------------------------------------------------------------------------

function Returns({
  search,
  page,
  pageSize,
  go,
  locations,
}: {
  search: CounterSearch;
  page: number;
  pageSize: number;
  go: Go;
  locations: Location[];
}) {
  const sedes = search.sedes ?? [];
  const order = search.order ?? "desc";
  const query = useReturns({
    page,
    page_size: pageSize,
    sort: search.sort,
    order,
    location_id: sedes.length ? sedes : undefined,
    q: search.q || undefined,
  });
  const rows = query.data?.rows ?? [];

  return (
    <>
      <FilterBar provenance={<Provenance />}>
        <SearchField
          value={search.q ?? ""}
          placeholder="Buscar por número"
          onChange={(next) => go({ q: next, page: 1 })}
        />
        <SedeChip
          locations={locations}
          selected={sedes}
          onChange={(next) => go({ sedes: next, page: 1 })}
        />
      </FilterBar>

      <TableContent>
        <div className="flex min-h-0 flex-1">
          <DataTable
            rows={rows}
            rowId={(row) => row.id}
            loading={query.isLoading}
            refetching={query.isFetching && !query.isLoading}
            empty={
              query.isError ? (
                <RouteError
                  title="No pudimos cargar las devoluciones."
                  detail="Revise la conexión y vuelva a intentarlo."
                  onRetry={() => void query.refetch()}
                />
              ) : (
                <EmptyState
                  title="Todavía no hay devoluciones"
                  body="Una devolución se registra en el mostrador, contra una venta cerrada."
                />
              )
            }
            columns={[
              {
                key: "number",
                label: "Devolución",
                width: "12ch",
                render: (row: ReturnRow) => (
                  <span className="tabular-nums">{row.number}</span>
                ),
              },
              {
                key: "sale",
                label: "Venta",
                width: "12ch",
                render: (row: ReturnRow) => (
                  <button
                    type="button"
                    className="tabular-nums text-brand hover:underline"
                    onClick={() =>
                      go({ segment: "ventas", venta: row.sale_id })
                    }
                  >
                    {row.sale_number}
                  </button>
                ),
              },
              {
                key: "location",
                label: "Sede",
                width: "12ch",
                truncate: true,
                render: (row: ReturnRow) => row.location_name,
              },
              {
                key: "recorded_at",
                label: "Fecha",
                width: "14ch",
                sortable: true,
                render: (row: ReturnRow) => since(row.recorded_at as string),
              },
              {
                key: "items",
                label: "Ítems",
                width: "8ch",
                align: "right",
                numeric: true,
                render: (row: ReturnRow) => formatCount(row.item_count),
              },
              {
                key: "total",
                label: "Total",
                width: "14ch",
                align: "right",
                numeric: true,
                sortable: true,
                render: (row: ReturnRow) => money(Number(row.total)),
              },
              {
                key: "reason",
                label: "Motivo",
                width: "28ch",
                truncate: true,
                render: (row: ReturnRow) => row.reason,
              },
            ]}
            sort={search.sort}
            order={order}
            onSort={(key) => go(toggleSort(search, key))}
            rowProps={(row: ReturnRow) => ({
              onClick: () => go({ devolucion: row.id }),
              className: cn(search.devolucion === row.id && "bg-hover-row"),
            })}
            footer={
              <TableFooter
                page={page}
                pageSize={pageSize}
                rowCount={query.data?.row_count}
                loading={query.isLoading}
                onPage={(next) => go({ page: next })}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
              />
            }
          />
          <ReturnPanel
            returnId={search.devolucion ?? null}
            onClose={() => go({ devolucion: undefined })}
            onOpenSale={(saleId) => go({ segment: "ventas", venta: saleId })}
          />
        </div>
      </TableContent>
    </>
  );
}

function ReturnPanel({
  returnId,
  onClose,
  onOpenSale,
}: {
  returnId: string | null;
  onClose: () => void;
  onOpenSale: (saleId: string) => void;
}) {
  const query = useReturn(returnId);
  const row: ReturnDetail | undefined = query.data;
  return (
    <RecordPanel
      title={row ? `Devolución ${row.number}` : "Devolución"}
      open={!!returnId}
      onClose={onClose}
    >
      {!row ? null : (
        <div className="flex flex-col gap-5">
          <dl className="flex flex-col gap-2 text-12">
            <Pair label="Sede" value={row.location_name} />
            <Pair label="Recibió" value={row.returned_by_name || "—"} />
            <Pair label="Registrada" value={since(row.recorded_at as string)} />
            <Pair
              label="Reembolso"
              value={PAYMENT_METHOD[row.refund_method] ?? row.refund_method}
            />
          </dl>

          <div>
            <p className="mb-2 text-11 uppercase tracking-eyebrow text-ink-note">
              Motivo
            </p>
            <p className="text-12 text-ink-body">{row.reason}</p>
          </div>

          <div>
            <p className="mb-2 text-11 uppercase tracking-eyebrow text-ink-note">
              Líneas
            </p>
            <ul className="flex flex-col gap-2">
              {row.lines.map((line) => (
                <li key={line.id} className="flex items-baseline gap-3 text-12">
                  <span className="min-w-0 flex-1 truncate text-ink">
                    {line.item_name}
                    {line.lot_code ? (
                      <span className="text-ink-note">
                        {" "}
                        {DOT} {line.lot_code}
                      </span>
                    ) : null}
                  </span>
                  <span className="shrink-0 tabular-nums text-ink-note">
                    {line.quantity} × {money(Number(line.unit_price))}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <dl className="flex flex-col gap-2 border-t border-hairline pt-4 text-12">
            <Pair label="Total" value={money(Number(row.total))} />
            {/* Contained in the total, not added to it (§3). */}
            <Pair label="IVA incluido" value={money(Number(row.tax))} />
          </dl>

          <button
            type="button"
            className="self-start text-12 text-brand hover:underline"
            onClick={() => onOpenSale(row.sale_id)}
          >
            Ver la venta {row.sale_number}
          </button>
        </div>
      )}
    </RecordPanel>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="shrink-0 text-ink-label">{label}</dt>
      <dd className="min-w-0 truncate text-right text-ink">{value}</dd>
    </div>
  );
}
