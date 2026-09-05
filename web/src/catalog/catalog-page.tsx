import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  useCatalogSummary,
  useCategories,
  useItems,
  useManufacturers,
  type ActiveFilter,
  type InvimaStatus,
  type ItemRow,
  type ItemType,
} from "@/api/catalog";
import { useLocations, type Me } from "@/api/queries";
import {
  ChipOptions,
  FilterBar,
  FilterChip,
  SearchField,
} from "@/ui/filter-bar";
import { count, money } from "@/ui/format";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import { Badge, INVIMA_STATUS } from "@/ui/status";
import { DataTable, TableFooter } from "@/ui/table";
import { EmptyState, ProgressLine, RouteError } from "@/ui/states";
import { useListKeys } from "@/ui/use-list-keys";
import { routeGrid } from "@/ui/use-grid";
import { useDebounced } from "@/ui/use-debounced";
import { InventoryBreadcrumb } from "@/inventory/breadcrumb";
import { ItemPanel } from "./item-panel";
import { INVIMA_LABEL, ITEM_TYPE } from "./vocabulary";

/**
 * Every key is optional and every default is applied when the view is read, so
 * **the URL carries only what differs from the default** -- which is §B.8.5's
 * own rule that a filter the URL does not carry is not set. It is also what
 * lets any other surface link here naming one filter and nothing else.
 */
export interface CatalogSearch {
  q?: string;
  type?: ItemType;
  manufacturer_id?: string;
  category_id?: string;
  invima_status?: InvimaStatus;
  active?: ActiveFilter;
  page?: number;
  pageSize?: number;
  sort?: string;
  order?: "asc" | "desc";
  /** The open record panel, so a row is a link. `nuevo` is the empty panel. */
  item?: string;
  /** The S7 suggestion this editor was opened against, and the figure it
   *  proposes. Both come from the URL, because the Precios row action is a
   *  navigation and not a write. */
  proposal?: string;
  sugerido?: string;
  settings?: string;
}

export const DEFAULTS = {
  active: "true" as ActiveFilter,
  page: 1,
  pageSize: 25,
  order: "asc" as const,
};

/**
 * §B.8.5 · **Inventario · Catálogo**, a sibling route of `Existencias`.
 *
 * The nav is one flat list of seven items and it is at its ceiling (§B.8.1), so
 * an eighth item is not available; and a four-thousand-row grid inside a
 * 1120 × 720 settings dialog is the wrong container. The two routes are
 * switched by the drawn segmented control (§A.15.3) in the header's action
 * slot. **At S1 the module has one route and Catálogo is its landing**; S3 adds
 * `Existencias`, takes the landing, and the control gains its second segment.
 */
export function CatalogPage({
  me,
  search: raw,
}: {
  me: Me;
  search: CatalogSearch;
}) {
  // Memoised because it is a dependency of the filter summary below, and a
  // fresh object every render would recompute it on every render.
  const search = useMemo(() => ({ ...DEFAULTS, ...strip(raw) }), [raw]);
  const navigate = useNavigate();
  const searchRef = useRef<HTMLInputElement | null>(null);
  const elevated = me.role !== "cashier";
  // The typed text lives here and reaches the URL debounced. A field driven
  // straight off the router keeps only the last character typed, because a
  // navigation is asynchronous and the next keystroke lands on the value the
  // URL had before the previous one.
  const [term, setTerm] = useState(raw.q ?? "");
  // ...and it follows the URL back when the URL changes for some other reason
  // -- `Back`, a pasted link, `Quitar filtros`. Adjusted during render rather
  // than in an effect, which is React's own answer for state derived from a
  // prop: an effect would paint the stale term for a frame first.
  const [syncedFrom, setSyncedFrom] = useState(raw.q ?? "");
  if ((raw.q ?? "") !== syncedFrom) {
    setSyncedFrom(raw.q ?? "");
    setTerm(raw.q ?? "");
  }

  const go = useCallback(
    (next: Partial<CatalogSearch>) =>
      void navigate({
        to: "/inventory/catalog",
        search: (previous: CatalogSearch) => ({ ...previous, ...next }),
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

  const items = useItems({
    q: search.q || undefined,
    type: search.type,
    manufacturer_id: search.manufacturer_id,
    category_id: search.category_id,
    invima_status: search.invima_status,
    active: search.active,
    page: search.page,
    page_size: search.pageSize,
    sort: search.sort,
    order: search.order,
  });
  const summary = useCatalogSummary();
  const manufacturers = useManufacturers();
  const categories = useCategories();
  const locations = useLocations(elevated);

  const rows = items.data?.rows ?? [];
  const openId = search.item;

  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `catalog-row-${index}`,
    pageKey: `${search.page}:${search.q ?? ""}`,
    // §B.13.2 · `j`, `k`, `Enter`, `Esc`, `/` and page turning. **`x` is
    // deliberately unbound here**: it toggles a row into a bulk-action set, and
    // this surface has none — the spec draws no bulk bar on Catálogo, and there
    // is no catalog-wide action to apply to a selection. Wiring a selection
    // model with nothing to do with it would be a control that does nothing.
    // The first stage that gives this grid a bulk action passes
    // `onToggleCheck` and `x` starts working with no other change.
    onOpen: (index) => {
      const row = rows[index];
      if (row) go({ item: row.id });
    },
    onEscape: () => {
      if (!openId) return false;
      go({ item: undefined });
      return true;
    },
    onSearch: () => searchRef.current?.focus(),
    onNextPage: () => {
      // `j` on the last row of the last page must not walk off the end: the
      // grid would render the never-populated empty state over a catalog of
      // four thousand references.
      if (search.page * search.pageSize >= (items.data?.row_count ?? 0))
        return false;
      grid.setPage(search.page + 1);
    },
    onPreviousPage: () => {
      if (search.page <= 1) return false;
      grid.setPage(search.page - 1);
    },
  });

  const filtered = useMemo(
    () =>
      [
        search.q ? `«${search.q}»` : null,
        search.type ? ITEM_TYPE[search.type] : null,
        search.manufacturer_id
          ? manufacturers.data?.find((one) => one.id === search.manufacturer_id)
              ?.name
          : null,
        search.category_id
          ? categories.data?.find((one) => one.id === search.category_id)?.name
          : null,
        search.invima_status ? INVIMA_LABEL[search.invima_status] : null,
        search.active === "false"
          ? "Inactivos"
          : search.active === "all"
            ? "Activos e inactivos"
            : null,
      ].filter(Boolean) as string[],
    [search, manufacturers.data, categories.data],
  );

  if (items.isError) {
    return (
      <>
        <Header me={me} onNew={() => go({ item: "nuevo" })} />
        <Content>
          <RouteError
            title="No pudimos cargar el catálogo."
            detail={
              items.error instanceof ApiError && items.error.status > 0
                ? items.error.message
                : "Botica necesita conexión para esta pantalla. Revise la " +
                  "conexión de este equipo e intente de nuevo."
            }
            requestId={
              items.error instanceof ApiError
                ? items.error.requestId
                : undefined
            }
            onRetry={() => void items.refetch()}
          />
        </Content>
      </>
    );
  }

  const annotation =
    summary.data && summary.data.expired_registrations > 0
      ? `${count(summary.data.expired_registrations)} con registro vencido`
      : undefined;

  return (
    <>
      <Header me={me} onNew={() => go({ item: "nuevo" })} />
      <FilterBar
        provenance={
          summary.data
            ? `${count(summary.data.active_items)} referencias activas · ${count(
                summary.data.services,
              )} servicios`
            : undefined
        }
      >
        <SearchField
          inputRef={searchRef}
          value={term}
          placeholder="Buscar producto, laboratorio, código o registro"
          onChange={(next) => {
            setTerm(next);
            commitTerm(next);
          }}
        />
        <FilterChip
          label="Tipo"
          value={search.type ? ITEM_TYPE[search.type] : undefined}
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todos" },
                { value: "product", label: "Productos" },
                { value: "service", label: "Servicios" },
              ]}
              value={search.type ?? ""}
              onPick={(next) => {
                go({ type: (next as ItemType) || undefined, page: 1 });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Laboratorio"
          value={
            manufacturers.data?.find((one) => one.id === search.manufacturer_id)
              ?.name
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todos" },
                ...(manufacturers.data ?? []).map((one) => ({
                  value: one.id,
                  label: one.name,
                })),
              ]}
              value={search.manufacturer_id ?? ""}
              onPick={(next) => {
                go({ manufacturer_id: next || undefined, page: 1 });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Categoría"
          value={
            categories.data?.find((one) => one.id === search.category_id)?.name
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
              value={search.category_id ?? ""}
              onPick={(next) => {
                go({ category_id: next || undefined, page: 1 });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Registro INVIMA"
          value={
            search.invima_status
              ? INVIMA_LABEL[search.invima_status]
              : undefined
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todos" },
                ...Object.entries(INVIMA_LABEL).map(([value, label]) => ({
                  value,
                  label,
                })),
              ]}
              value={search.invima_status ?? ""}
              onPick={(next) => {
                go({
                  invima_status: (next as InvimaStatus) || undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Estado"
          value={
            search.active === "true"
              ? undefined
              : search.active === "false"
                ? "Inactivos"
                : "Todos"
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "true", label: "Activos" },
                { value: "false", label: "Inactivos" },
                { value: "all", label: "Activos e inactivos" },
              ]}
              value={search.active}
              onPick={(next) => {
                go({ active: next as ActiveFilter, page: 1 });
                close();
              }}
            />
          )}
        </FilterChip>
      </FilterBar>
      <ProgressLine active={items.isFetching && !items.isPending} />

      <div className="flex min-h-0 flex-1">
        <TableContent>
          <DataTable<ItemRow>
            rows={rows}
            rowId={(row) => row.id}
            density="standard"
            minWidth={1000}
            loading={items.isPending}
            refetching={items.isFetching && !items.isPending}
            skeletonRows={25}
            skeletonWidths={["70%", "55%", "50%", "55%", "60%", "40%", "55%"]}
            containerProps={keys.containerProps}
            sort={search.sort}
            order={search.order}
            onSort={grid.toggleSort}
            rowProps={(row, index) => ({
              id: `catalog-row-${index}`,
              cursor: keys.cursor === index,
              current: openId === row.id,
              onClick: () => go({ item: row.id }),
            })}
            empty={
              filtered.length > 0 ? (
                <EmptyState
                  kind="filtered"
                  title="Ningún producto coincide con estos filtros"
                  body={`Filtros activos: ${filtered.join(" · ")}.`}
                  actionLabel="Quitar filtros"
                  onAction={() => {
                    go({
                      q: undefined,
                      type: undefined,
                      manufacturer_id: undefined,
                      category_id: undefined,
                      invima_status: undefined,
                      active: "true",
                      page: 1,
                    });
                  }}
                />
              ) : (
                <EmptyState
                  title="Todavía no hay catálogo"
                  body="El catálogo de la red se carga desde el sistema anterior durante la puesta en marcha. También puede crear una referencia a mano."
                  actionLabel={elevated ? "Nuevo producto" : undefined}
                  onAction={elevated ? () => go({ item: "nuevo" }) : undefined}
                />
              )
            }
            footer={
              <TableFooter
                page={search.page}
                pageSize={search.pageSize}
                rowCount={items.data?.row_count}
                loading={items.isPending}
                onPage={grid.setPage}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
                annotation={annotation}
              />
            }
            columns={[
              {
                key: "name",
                label: "Producto",
                width: "26%",
                sortable: true,
                truncate: true,
                render: (row) => (
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-ink">
                      {row.name}
                      {row.active ? null : (
                        <span className="ml-1.5 text-12 text-ink-label">
                          · Inactivo
                        </span>
                      )}
                    </span>
                  </span>
                ),
              },
              {
                key: "manufacturer",
                label: "Laboratorio",
                width: "13%",
                sortable: true,
                truncate: true,
                render: (row) => row.manufacturer_name ?? <Dash />,
              },
              {
                key: "category",
                label: "Categoría",
                width: "12%",
                sortable: true,
                truncate: true,
                render: (row) => row.category_name ?? <Dash />,
              },
              {
                key: "presentation",
                label: "Presentación",
                width: "11%",
                truncate: true,
                render: (row) => row.presentation || <Dash />,
              },
              {
                key: "invima_registration",
                label: "Registro INVIMA",
                width: "12%",
                sortable: true,
                truncate: true,
                render: (row) => row.invima_registration || <Dash />,
              },
              {
                key: "price",
                label: "Precio",
                width: "11%",
                align: "right",
                numeric: true,
                sortable: true,
                render: (row) =>
                  row.price === null ? (
                    <Dash />
                  ) : (
                    <span>
                      {money(Number(row.price))}
                      {row.splittable ? (
                        <span className="ml-1 text-11 text-ink-label">
                          /{row.unit}
                        </span>
                      ) : null}
                    </span>
                  ),
              },
              {
                key: "invima_status",
                label: "Registro",
                width: "15%",
                sortable: true,
                render: (row) => {
                  const meaning = INVIMA_STATUS[row.invima_status];
                  return meaning ? (
                    <Badge family={meaning.family} dot={meaning.dot}>
                      {meaning.label}
                    </Badge>
                  ) : null;
                },
              },
            ]}
          />
        </TableContent>

        {openId ? (
          <ItemPanel
            itemId={openId === "nuevo" ? undefined : openId}
            creating={openId === "nuevo"}
            me={me}
            locations={locations.data ?? []}
            proposalId={search.proposal}
            suggestedPrice={search.sugerido}
            onClose={() =>
              go({ item: undefined, proposal: undefined, sugerido: undefined })
            }
            onCreated={(id) => go({ item: id })}
          />
        ) : null}
      </div>
    </>
  );
}

/** Undefined keys never reach a spread over the defaults. */
function strip(search: CatalogSearch): CatalogSearch {
  return Object.fromEntries(
    Object.entries(search).filter(([, value]) => value !== undefined),
  ) as CatalogSearch;
}

/** §B.9.2 tier 3 · a cell that does not apply renders an em dash, never a zero. */
function Dash() {
  return <span className="text-ink-soft">—</span>;
}

function Header({ me, onNew }: { me: Me; onNew: () => void }) {
  return (
    <TopBar
      // S3 · **the module is one nav item and five routes**, and the header
      // breadcrumb's first segment is the menu that moves between them
      // (§B.8.1). The one-segment control that stood here at S1 was a
      // placeholder for exactly this, and the drawn Existencias header already
      // carries two buttons -- a third control would crowd them.
      breadcrumb={<InventoryBreadcrumb />}
      title="Catálogo"
      actions={
        /* §B.8.3 · a `cashier` reaches the grid read-only. The action is
         **not rendered**, never rendered disabled. */
        me.role !== "cashier" ? (
          <TopBarButton variant="primary" onClick={onNew}>
            Nuevo producto
          </TopBarButton>
        ) : null
      }
    />
  );
}
