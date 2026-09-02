import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import { useCategories } from "@/api/catalog";
import {
  useStock,
  useStockSummary,
  type StockRow,
  type StockState,
} from "@/api/inventory";
import { useLocations, type Location, type Me } from "@/api/queries";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import { SyncStatus } from "@/sync/sync-status";
import {
  ChipOptions,
  ChipToggles,
  FilterBar,
  FilterChip,
  SearchField,
} from "@/ui/filter-bar";
import { count as formatCount, monthYear } from "@/ui/format";
import { Badge } from "@/ui/status";
import { EmptyState, ProgressLine, RouteError } from "@/ui/states";
import { DataTable, TableFooter } from "@/ui/table";
import { StockBar } from "@/ui/tile";
import { useDebounced } from "@/ui/use-debounced";
import { routeGrid } from "@/ui/use-grid";
import { useListKeys } from "@/ui/use-list-keys";
import { InventoryBreadcrumb } from "./breadcrumb";
import { LotTrace } from "./lot-trace";
import { StockPanel } from "./stock-panel";
import {
  EXPIRY_FILTERS,
  STATE_FILTER_LABEL,
  STATE_ORDER,
  stateBadge,
  stockoutClause,
} from "./vocabulary";

/**
 * Every key is optional and every default is applied when the view is read, so
 * **the URL carries only what differs from the default** -- §B.8.5's own rule
 * that a filter the URL does not carry is not set, and what lets any other
 * surface link here naming one filter and nothing else.
 */
export interface StockSearch {
  q?: string;
  /** Multi-select, so it is plural: `sede` is a single id on the receiving
   *  route and TanStack merges every route's search type into one. */
  sedes?: string[];
  categoria?: string;
  estado?: StockState;
  accion?: boolean;
  vencimiento?: "expired" | "valuation" | "alert" | "notice" | "none";
  page?: number;
  pageSize?: number;
  sort?: string;
  order?: "asc" | "desc";
  /** The open record panel, so a row is a link. */
  row?: string;
  /** The open lot trace, so an INVIMA answer is a link too. */
  lote?: string;
  settings?: string;
}

export const DEFAULTS = {
  page: 1,
  pageSize: 25,
  order: "asc" as const,
};

/**
 * §A.22 + handoff Pantalla 2 · **Inventario · Existencias**.
 *
 * Seven columns at the drawn widths, 48px rows, the in-cell bar, four filter
 * chips, the search field, the footer's range and its `312 requieren acción`
 * annotation, the row-size select and the page group.
 *
 * **The screen is online-only and server-authoritative** (§4, A4): it reads the
 * whole network at lot grain, and a stale 4.284-row grid a person is about to
 * make a purchasing decision from is exactly the confident-number-that-is-wrong
 * §5 rule 1 exists to prevent.
 */
export function StockPage({
  me,
  search: raw,
}: {
  me: Me;
  search: StockSearch;
}) {
  const search = useMemo(() => ({ ...DEFAULTS, ...strip(raw) }), [raw]);
  const navigate = useNavigate();
  const searchRef = useRef<HTMLInputElement | null>(null);
  const elevated = me.role !== "cashier";

  // The typed text lives here and reaches the URL debounced. A field driven
  // straight off the router keeps only the last character typed.
  const [term, setTerm] = useState(raw.q ?? "");
  const [syncedFrom, setSyncedFrom] = useState(raw.q ?? "");
  if ((raw.q ?? "") !== syncedFrom) {
    setSyncedFrom(raw.q ?? "");
    setTerm(raw.q ?? "");
  }

  const go = useCallback(
    (next: Partial<StockSearch>) =>
      void navigate({
        to: "/inventory",
        search: (previous: StockSearch) => ({ ...previous, ...next }),
        replace: next.q !== undefined,
      }),
    [navigate],
  );

  const commitTerm = useDebounced((next: string) =>
    go({ q: next || undefined, page: 1 }),
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

  const locations = useLocations();
  const categories = useCategories();

  // A2 · the chip's own selection, intersected with the helper's set by the
  // server and never replacing it. A `cashier` lands with their home sede
  // pre-selected, which is the UI-default half of the rule.
  const sedes = useMemo(
    () => search.sedes ?? (me.location_id ? [me.location_id] : undefined),
    [search.sedes, me.location_id],
  );

  const filters = {
    q: search.q || undefined,
    location_id: sedes,
    category_id: search.categoria,
    expiry: search.vencimiento,
  };
  const rowsQuery = useStock({
    ...filters,
    state: search.estado,
    action_required: search.accion || undefined,
    page: search.page,
    page_size: search.pageSize,
    sort: search.sort,
    order: search.order,
  });

  // The chip's own counts, over the same filters the chip is about to narrow --
  // so a person choosing `Quiebre` can see how many there are before they
  // choose it, and a state with none says so rather than being absent.
  const summary = useStockSummary(filters);
  const perState = useMemo(() => {
    const counts: Partial<Record<StockState, number>> = {};
    for (const one of summary.data?.states ?? []) counts[one.state] = one.count;
    return counts;
  }, [summary.data]);

  const rows = rowsQuery.data?.rows ?? [];
  const openId = search.row;

  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `stock-row-${index}`,
    pageKey: `${search.page}:${search.q ?? ""}`,
    // §B.13.2 · `j`, `k`, `Enter`, `Esc` and page turning. **`x` is
    // deliberately unbound**, for the reason S1 gave on Catálogo: it toggles a
    // row into a bulk-action set, and this one has none -- a shelf figure is
    // changed by a movement against one row at a time, and there is no
    // network-wide action to apply to a selection of them. Wiring a
    // selection model with nothing to do with it would be a control that does
    // nothing. The first stage that gives this list a bulk action passes
    // `onToggleCheck` and `x` starts working with no other change.
    onOpen: (index) => {
      const row = rows[index];
      if (row) go({ row: row.id });
    },
    onEscape: () => {
      if (!openId) return false;
      go({ row: undefined });
      return true;
    },
    onSearch: () => searchRef.current?.focus(),
    onNextPage: () => {
      if (search.page * search.pageSize >= (rowsQuery.data?.row_count ?? 0))
        return false;
      grid.setPage(search.page + 1);
    },
    onPreviousPage: () => {
      if (search.page <= 1) return false;
      grid.setPage(search.page - 1);
    },
  });

  const active = useMemo(
    () =>
      [
        search.q ? `«${search.q}»` : null,
        sedes?.length
          ? `Sede: ${sedes
              .map(
                (id) =>
                  locations.data?.find((one) => one.id === id)?.name ?? "sede",
              )
              .join(", ")}`
          : null,
        search.categoria
          ? categories.data?.find((one) => one.id === search.categoria)?.name
          : null,
        search.accion ? "Requiere acción" : null,
        search.estado ? STATE_FILTER_LABEL[search.estado] : null,
        search.vencimiento
          ? EXPIRY_FILTERS.find((one) => one.value === search.vencimiento)
              ?.label
          : null,
      ].filter(Boolean) as string[],
    [search, sedes, locations.data, categories.data],
  );

  const clear = () =>
    go({
      q: undefined,
      sedes: undefined,
      categoria: undefined,
      estado: undefined,
      accion: undefined,
      vencimiento: undefined,
      page: 1,
    });

  if (rowsQuery.isError) {
    return (
      <>
        <Header me={me} />
        <Content>
          <RouteError
            title="No pudimos cargar las existencias."
            detail={
              rowsQuery.error instanceof ApiError && rowsQuery.error.status > 0
                ? rowsQuery.error.message
                : "El equipo está sin conexión. Existencias lee toda la red en " +
                  "vivo, así que no mostramos una copia vieja: revise la " +
                  "conexión e intente de nuevo."
            }
            requestId={
              rowsQuery.error instanceof ApiError
                ? rowsQuery.error.requestId
                : undefined
            }
            onRetry={() => void rowsQuery.refetch()}
          />
        </Content>
      </>
    );
  }

  const annotation =
    rowsQuery.data && rowsQuery.data.action_required > 0
      ? `${formatCount(rowsQuery.data.action_required)} requieren acción`
      : undefined;

  return (
    <>
      <Header me={me} />
      <FilterBar provenance={<SyncStatus />}>
        <SearchField
          inputRef={searchRef}
          value={term}
          placeholder="Buscar producto, laboratorio o lote"
          onChange={(next) => {
            setTerm(next);
            commitTerm(next);
          }}
        />
        <SedeChip
          locations={locations.data ?? []}
          selected={sedes ?? []}
          onChange={(next) =>
            go({ sedes: next.length ? next : undefined, page: 1 })
          }
        />
        <FilterChip
          label="Categoría"
          value={
            categories.data?.find((one) => one.id === search.categoria)?.name
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todas" },
                ...(categories.data ?? []).map((one) => ({
                  value: one.id,
                  label: one.parent_name
                    ? `${one.parent_name} · ${one.name}`
                    : one.name,
                })),
              ]}
              value={search.categoria ?? ""}
              onPick={(next) => {
                go({ categoria: next || undefined, page: 1 });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Estado"
          value={
            search.accion
              ? "Requiere acción"
              : search.estado
                ? STATE_FILTER_LABEL[search.estado]
                : undefined
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todos" },
                // The chip the handoff draws active: expired, quiebre, urgent
                // expiry and reorder point. **Not `expiring`** -- a lot eleven
                // months out is not a decision anyone takes today -- and not
                // `overstock`, which is capital and is Compras' screen.
                {
                  value: "accion",
                  label: summary.data
                    ? `Requiere acción · ${formatCount(summary.data.action_required)}`
                    : "Requiere acción",
                },
                ...STATE_ORDER.map((state) => ({
                  value: state,
                  label:
                    perState[state] === undefined
                      ? STATE_FILTER_LABEL[state]
                      : `${STATE_FILTER_LABEL[state]} · ${formatCount(perState[state])}`,
                })),
              ]}
              value={search.accion ? "accion" : (search.estado ?? "")}
              onPick={(next) => {
                go({
                  accion: next === "accion" ? true : undefined,
                  estado:
                    next && next !== "accion"
                      ? (next as StockState)
                      : undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Vencimiento"
          value={
            EXPIRY_FILTERS.find((one) => one.value === search.vencimiento)
              ?.label
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todos" },
                ...EXPIRY_FILTERS.map((one) => ({
                  value: one.value,
                  label: one.label,
                })),
              ]}
              value={search.vencimiento ?? ""}
              onPick={(next) => {
                go({
                  vencimiento:
                    (next as StockSearch["vencimiento"]) || undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
      </FilterBar>
      <ProgressLine active={rowsQuery.isFetching && !rowsQuery.isPending} />

      <div className="flex min-h-0 flex-1">
        <TableContent>
          <DataTable<StockRow>
            rows={rows}
            rowId={(row) => row.id}
            density="standard"
            // §B.4.4 · **no column is dropped below 1440px.** The frame scrolls
            // horizontally inside its own container at a stated minimum, and
            // `Producto` is sticky-left: every one of the seven columns is a
            // filter target, and a column that disappears at a viewport width
            // is a column a user cannot find.
            minWidth={1080}
            loading={rowsQuery.isPending}
            refetching={rowsQuery.isFetching && !rowsQuery.isPending}
            skeletonRows={15}
            skeletonWidths={["70%", "55%", "50%", "45%", "40%", "60%", "65%"]}
            containerProps={keys.containerProps}
            sort={search.sort}
            order={search.order}
            onSort={grid.toggleSort}
            rowProps={(row, index) => ({
              id: `stock-row-${index}`,
              cursor: keys.cursor === index,
              current: openId === row.id,
              onClick: () => go({ row: row.id }),
            })}
            empty={
              active.length > 0 ? (
                <EmptyState
                  kind="filtered"
                  title="Ningún producto coincide con estos filtros"
                  body={`Filtros activos: ${active.join(" · ")}.`}
                  actionLabel="Quitar filtros"
                  onAction={clear}
                />
              ) : (
                <EmptyState
                  title="Todavía no hay existencias"
                  body="La mercancía entra por Cargar mercancía o por un traslado desde otra sede."
                  actionLabel={elevated ? "Cargar mercancía" : undefined}
                  onAction={
                    elevated
                      ? () => void navigate({ to: "/inventory/receive" })
                      : undefined
                  }
                />
              )
            }
            footer={
              <TableFooter
                page={search.page}
                pageSize={search.pageSize}
                rowCount={rowsQuery.data?.row_count}
                loading={rowsQuery.isPending}
                onPage={grid.setPage}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
                annotation={annotation}
              />
            }
            columns={COLUMNS}
          />
        </TableContent>

        {openId ? (
          <StockPanel
            rowId={openId}
            row={rows.find((one) => one.id === openId)}
            me={me}
            onClose={() => go({ row: undefined })}
          />
        ) : null}
      </div>

      {search.lote ? (
        <LotTrace
          lotId={search.lote}
          me={me}
          onClose={() => go({ lote: undefined })}
        />
      ) : null}
    </>
  );
}

/**
 * §A.17 · the drawn column widths. Producto 24 · Laboratorio 13 · Sede 12 ·
 * Lote 9 · Vence 9 · Existencias 13 right-aligned with the in-cell bar ·
 * Estado 20.
 */
const COLUMNS = [
  {
    key: "item",
    label: "Producto",
    width: "24%",
    sortable: true,
    truncate: true,
    sticky: true,
    // The fill is the row's own, so the cell occludes what passes under it and
    // still answers hover, cursor and the open record -- four states, one
    // declaration (§B.4.3).
    className: "sticky left-0 bg-inherit",
    render: (row: StockRow) => (
      <span className="block truncate text-ink">
        {row.item_name}
        {row.presentation ? (
          <span className="ml-1.5 text-12 text-ink-label">
            {row.presentation}
          </span>
        ) : null}
      </span>
    ),
  },
  {
    key: "manufacturer",
    label: "Laboratorio",
    width: "13%",
    sortable: true,
    truncate: true,
    render: (row: StockRow) =>
      row.manufacturer_name ?? <Dash reading="sin laboratorio" />,
  },
  {
    key: "location",
    label: "Sede",
    width: "12%",
    sortable: true,
    truncate: true,
    render: (row: StockRow) => row.location_name,
  },
  {
    key: "lot",
    label: "Lote",
    width: "9%",
    sortable: true,
    truncate: true,
    // §B.9.2 tier 3 · an item that does not track lots renders an em dash with
    // its reading, never a blank cell and never a zero.
    render: (row: StockRow) => row.lot_code ?? <Dash reading="sin lote" />,
  },
  {
    key: "expires_at",
    label: "Vence",
    width: "9%",
    sortable: true,
    render: (row: StockRow) =>
      row.expires_at ? monthYear(row.expires_at) : <Dash reading="sin lote" />,
  },
  {
    key: "quantity",
    label: "Existencias",
    width: "13%",
    align: "right" as const,
    numeric: true,
    sortable: true,
    render: (row: StockRow) =>
      // §A.18.1 · the bar never appears without its number, and **the bar is
      // absent where no policy gives the row a capacity**: a bar with no
      // denominator behind it is a bar measuring nothing.
      row.bar_percentage === null ? (
        <span className="tabular-nums">{formatCount(row.quantity)}</span>
      ) : (
        <StockBar
          fill={row.bar_percentage}
          figure={formatCount(row.quantity)}
          label={`${row.quantity} de ${row.max_quantity ?? 0}`}
        />
      ),
  },
  {
    key: "state",
    label: "Estado",
    width: "20%",
    sortable: true,
    render: (row: StockRow) => {
      const meaning = stateBadge(row);
      const clause = stockoutClause(row);
      return (
        <Badge family={meaning.family} dot={meaning.dot}>
          {meaning.label}
          {clause ? <span className="text-ink-body">{clause}</span> : null}
        </Badge>
      );
    },
  },
];

/** Undefined keys never reach a spread over the defaults. */
function strip(search: StockSearch): StockSearch {
  return Object.fromEntries(
    Object.entries(search).filter(([, value]) => value !== undefined),
  ) as StockSearch;
}

/** §B.9.2 tier 3 · a figure that does not apply is an em dash with a reading,
 *  never a zero. */
function Dash({ reading }: { reading: string }) {
  return (
    <span className="text-ink-soft" title={reading} aria-label={reading}>
      —
    </span>
  );
}

function SedeChip({
  locations,
  selected,
  onChange,
}: {
  locations: Location[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  // The drawn chip reads `Sede · Todas · 6`: the value pill states how many
  // sedes are in play, whether or not one has been chosen.
  const value =
    selected.length === 0
      ? `Todas · ${locations.length}`
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

function Header({ me }: { me: Me }) {
  const navigate = useNavigate();
  return (
    <TopBar
      breadcrumb={<InventoryBreadcrumb />}
      title="Existencias"
      actions={
        // §B.8.3 · an action a role cannot reach is **not rendered**, never
        // rendered disabled. A greyed button advertises a capability that will
        // never arrive.
        me.role === "cashier" ? null : (
          <>
            <TopBarButton
              variant="secondary"
              onClick={() => void navigate({ to: "/inventory/receive" })}
            >
              Cargar mercancía
            </TopBarButton>
            <TopBarButton
              variant="primary"
              onClick={() =>
                void navigate({
                  to: "/inventory/transfers",
                  search: { nuevo: true },
                })
              }
            >
              Nuevo traslado
            </TopBarButton>
          </>
        )
      }
    />
  );
}
