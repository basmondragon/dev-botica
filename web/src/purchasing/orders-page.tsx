import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  useGenerateOrders,
  usePurchaseOrders,
  type PurchaseOrderRow,
  type PurchaseOrderStatus,
} from "@/api/purchasing";
import { useLocations, type Location } from "@/api/queries";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import {
  ChipOptions,
  ChipToggles,
  FilterBar,
  FilterChip,
} from "@/ui/filter-bar";
import { count as formatCount, dayMonth, money, DOT } from "@/ui/format";
import { EmptyState, ProgressLine, RouteError } from "@/ui/states";
import { Badge } from "@/ui/status";
import { DataTable, TableFooter } from "@/ui/table";
import { routeGrid } from "@/ui/use-grid";
import { useListKeys } from "@/ui/use-list-keys";
import { PurchasingBreadcrumb } from "./breadcrumb";
import { ManualOrderModal } from "./manual-order";
import { ORDER_STATUS, ORDER_STATUS_ORDER } from "./vocabulary";

export interface OrdersSearch {
  /** Named `estadoOrden` and not `estado`, because TanStack merges every
   *  route's search type into one and `estado` already carries S3's stock
   *  states. Two meanings under one key is a filter that type-checks and
   *  filters the wrong thing. */
  estadoOrden?: PurchaseOrderStatus;
  sedes?: string[];
  origen?: "model" | "manual";
  orden?: string;
  page?: number;
  pageSize?: number;
  sort?: string;
  order?: "asc" | "desc";
  settings?: string;
}

const DEFAULTS = { page: 1, pageSize: 25, order: "desc" as const };

/**
 * **Compras · Órdenes de compra** -- the list, in Existencias' own shape
 * (§B.8.4's recipe register): filter bar, table, footer.
 *
 * `Estado` is this table's one badge column (§B.7.3), rendering the six
 * `purchase_order_status` labels verbatim from §B.7.4 -- **`Descartada` on the
 * neutral family**, because discarding a suggestion is the product working.
 */
export function OrdersPage({ search: raw }: { search: OrdersSearch }) {
  const search = useMemo(() => ({ ...DEFAULTS, ...strip(raw) }), [raw]);
  const navigate = useNavigate();
  const locations = useLocations();
  const generate = useGenerateOrders();
  const [writing, setWriting] = useState(false);

  const go = useCallback(
    (next: Partial<OrdersSearch>) =>
      void navigate({
        to: "/purchasing/orders",
        search: (previous: OrdersSearch) => ({ ...previous, ...next }),
      }),
    [navigate],
  );

  const grid = routeGrid(
    {
      page: search.page,
      pageSize: search.pageSize,
      sort: search.sort,
      order: search.order,
    },
    (next) => go(next),
  );

  const query = usePurchaseOrders({
    status: search.estadoOrden ? [search.estadoOrden] : undefined,
    location_id: search.sedes,
    source: search.origen,
    page: search.page,
    page_size: search.pageSize,
    sort: search.sort,
    order: search.order,
  });

  const rows = query.data?.rows ?? [];
  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `order-row-${index}`,
    pageKey: `${search.page}:${search.estadoOrden ?? ""}`,
    onOpen: (index) => {
      const row = rows[index];
      if (row) open(row);
    },
    onNextPage: () => {
      if (search.page * search.pageSize >= (query.data?.row_count ?? 0))
        return false;
      grid.setPage(search.page + 1);
    },
    onPreviousPage: () => {
      if (search.page <= 1) return false;
      grid.setPage(search.page - 1);
    },
  });

  /** The screen a row opens on is the screen its status has work on.
   *
   *  An order out at the supplier opens on Recepción, because the next thing
   *  anybody does with it is type what arrived. Everything else -- still
   *  waiting, approved, closed, discarded -- opens on the order itself, where
   *  its quantities and its `Por qué` are. A list that sent every row to the
   *  same place would send a buyer to a locked table when what they wanted was
   *  the carton on the loading bay, or to a receiving screen for an order
   *  nobody can receive against. */
  const RECEIVABLE = new Set(["sent", "partially_received"]);

  function open(row: PurchaseOrderRow) {
    if (RECEIVABLE.has(row.status)) {
      void navigate({ to: "/purchasing/receipts", search: { orden: row.id } });
    } else {
      void navigate({ to: "/purchasing", search: { orden: row.id } });
    }
  }

  const filtered =
    !!search.estadoOrden || !!search.sedes?.length || !!search.origen;

  if (query.isError) {
    const failure = query.error as unknown;
    const offline = !(failure instanceof ApiError && failure.status > 0);
    return (
      <>
        <Header
          onGenerate={() => generate.mutate(undefined)}
          onNew={() => setWriting(true)}
        />
        <Content>
          <RouteError
            title={offline ? "Sin conexión" : "No pudimos cargar las órdenes."}
            detail={
              offline
                ? "Compras necesita conexión para leer el inventario de la red. " +
                  "El mostrador sigue vendiendo con normalidad."
                : failure instanceof ApiError
                  ? failure.message
                  : "Intente de nuevo."
            }
            requestId={
              failure instanceof ApiError ? failure.requestId : undefined
            }
            onRetry={() => void query.refetch()}
          />
        </Content>
      </>
    );
  }

  return (
    <>
      <Header
        onGenerate={() => generate.mutate(undefined)}
        onNew={() => setWriting(true)}
      />
      <FilterBar>
        <SedeChip
          locations={locations.data ?? []}
          selected={search.sedes ?? []}
          onChange={(next) =>
            go({ sedes: next.length ? next : undefined, page: 1 })
          }
        />
        <FilterChip
          label="Estado"
          value={
            search.estadoOrden
              ? ORDER_STATUS[search.estadoOrden].label
              : undefined
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todos" },
                ...ORDER_STATUS_ORDER.map((one) => ({
                  value: one,
                  label: ORDER_STATUS[one].label,
                })),
              ]}
              value={search.estadoOrden ?? ""}
              onPick={(next) => {
                go({
                  estadoOrden: (next as PurchaseOrderStatus) || undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Origen"
          value={
            search.origen === "model"
              ? "Del modelo"
              : search.origen === "manual"
                ? "Escrita a mano"
                : undefined
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todas" },
                { value: "model", label: "Del modelo" },
                { value: "manual", label: "Escrita a mano" },
              ]}
              value={search.origen ?? ""}
              onPick={(next) => {
                go({
                  origen: (next as "model" | "manual") || undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
      </FilterBar>
      <ProgressLine active={query.isFetching && !query.isPending} />

      <TableContent>
        <DataTable<PurchaseOrderRow>
          rows={rows}
          rowId={(row) => row.id}
          density="standard"
          minWidth={1000}
          loading={query.isPending}
          refetching={query.isFetching && !query.isPending}
          skeletonRows={12}
          skeletonWidths={["40%", "70%", "50%", "35%", "55%", "45%", "60%"]}
          containerProps={keys.containerProps}
          sort={search.sort}
          order={search.order}
          onSort={grid.toggleSort}
          rowProps={(row, index) => ({
            id: `order-row-${index}`,
            cursor: keys.cursor === index,
            onClick: () => open(row),
          })}
          columns={COLUMNS}
          empty={
            filtered ? (
              <EmptyState
                kind="filtered"
                title="Ninguna orden coincide con estos filtros"
                body="Quite los filtros para ver todas las órdenes de la red."
                actionLabel="Quitar filtros"
                onAction={() =>
                  go({
                    estadoOrden: undefined,
                    sedes: undefined,
                    origen: undefined,
                    page: 1,
                  })
                }
              />
            ) : (
              <EmptyState
                title="Todavía no hay órdenes de compra"
                body={
                  "El modelo propone una orden por proveedor y sede cada mañana. " +
                  "Sin histórico usa el punto de reorden de la sede."
                }
                actionLabel="Generar ahora"
                onAction={() => generate.mutate(undefined)}
              />
            )
          }
          footer={
            <TableFooter
              page={search.page}
              pageSize={search.pageSize}
              rowCount={query.data?.row_count}
              loading={query.isPending}
              onPage={grid.setPage}
              onPageSize={(next) => go({ pageSize: next, page: 1 })}
            />
          }
        />
      </TableContent>

      <ManualOrderModal open={writing} onClose={() => setWriting(false)} />
    </>
  );
}

/** §A.17 · Orden 10 · Proveedor 20 · Sede 12 · Referencias 10 · Total 14 ·
 *  Creada 12 · Estado 22. */
const COLUMNS = [
  {
    key: "number",
    label: "Orden",
    width: "10%",
    sortable: true,
    render: (row: PurchaseOrderRow) => (
      <span className="tabular-nums text-ink">{formatCount(row.number)}</span>
    ),
  },
  {
    key: "supplier",
    label: "Proveedor",
    width: "20%",
    sortable: true,
    truncate: true,
    render: (row: PurchaseOrderRow) => row.supplier_name,
  },
  {
    key: "location",
    label: "Sede",
    width: "12%",
    sortable: true,
    truncate: true,
    render: (row: PurchaseOrderRow) => row.location_name,
  },
  {
    key: "lines",
    label: "Referencias",
    width: "10%",
    align: "right" as const,
    numeric: true,
    sortable: true,
    render: (row: PurchaseOrderRow) => formatCount(row.line_count),
  },
  {
    key: "total",
    label: "Total",
    width: "14%",
    align: "right" as const,
    numeric: true,
    sortable: true,
    render: (row: PurchaseOrderRow) => money(Number(row.total)),
  },
  {
    key: "created_at",
    label: "Creada",
    width: "12%",
    sortable: true,
    render: (row: PurchaseOrderRow) => dayMonth(row.created_at),
  },
  {
    key: "status",
    label: "Estado",
    width: "22%",
    sortable: true,
    render: (row: PurchaseOrderRow) => {
      const meaning = ORDER_STATUS[row.status];
      return (
        <Badge family={meaning.family} dot={meaning.dot}>
          {meaning.label}
          {row.source === "manual" ? (
            <span className="text-ink-body">{` ${DOT} a mano`}</span>
          ) : null}
        </Badge>
      );
    },
  },
];

function SedeChip({
  locations,
  selected,
  onChange,
}: {
  locations: Location[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const value =
    selected.length === 0
      ? `Todas ${DOT} ${locations.length}`
      : selected.length === 1
        ? (locations.find((one) => one.id === selected[0])?.name ?? "1")
        : `${selected.length} sedes`;
  return (
    <FilterChip label="Sede" value={locations.length ? value : undefined}>
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

function strip(search: OrdersSearch): OrdersSearch {
  return Object.fromEntries(
    Object.entries(search).filter(([, value]) => value !== undefined),
  ) as OrdersSearch;
}

function Header({
  onGenerate,
  onNew,
}: {
  onGenerate: () => void;
  onNew: () => void;
}) {
  return (
    <TopBar
      breadcrumb={<PurchasingBreadcrumb />}
      title="Órdenes de compra"
      actions={
        <>
          <TopBarButton variant="secondary" onClick={onGenerate}>
            Generar ahora
          </TopBarButton>
          {/* §B.6.2 · one primary per surface, and on this one it is the order
              a person writes rather than the one the model proposes. */}
          <TopBarButton variant="primary" onClick={onNew}>
            Nueva orden
          </TopBarButton>
        </>
      }
    />
  );
}
