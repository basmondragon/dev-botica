import { useCallback, useMemo, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  usePricingFilters,
  usePricingItems,
  usePricingSummary,
  useRecalculatePricing,
  type ConfidenceBand,
  type PricingRow,
  type ProposalBasis,
  type RowStateFilter,
} from "@/api/pricing";
import { useMe } from "@/api/queries";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import {
  ChipOptions,
  FilterBar,
  FilterChip,
  SearchField,
} from "@/ui/filter-bar";
import { count, dayMonth, DOT, money, percent, points } from "@/ui/format";
import { Button } from "@/ui/button";
import { EmptyState, ProgressLine, RegionError, RouteError } from "@/ui/states";
import { Badge, StatusDot } from "@/ui/status";
import { DataTable, TableFooter } from "@/ui/table";
import { Tile, TileSkeleton } from "@/ui/tile";
import { routeGrid } from "@/ui/use-grid";
import { useDebounced } from "@/ui/use-debounced";
import { useListKeys } from "@/ui/use-list-keys";
import { AdoptionPanel } from "./adoption";
import { PricingBreadcrumb } from "./breadcrumb";
import { PricingRecordPanel } from "./record-panel";
import {
  BASIS_LABEL,
  CONFIDENCE_LABEL,
  ROW_STATE,
  ROW_STATE_ORDER,
  basisLine,
} from "./vocabulary";

export interface PricingSearch {
  q?: string;
  laboratorio?: string;
  categoria?: string;
  /** Named `estadoPrecio` and not `estado`: TanStack merges every route's
   *  search type into one, and `estado` already carries S3's stock states.
   *  Two meanings under one key is a filter that type-checks and filters the
   *  wrong thing. */
  estadoPrecio?: RowStateFilter;
  /** Named `basePrecio` and not `base`: TanStack merges every route's search
   *  type into one, and `base` already carries S6's forecast regimes. */
  basePrecio?: ProposalBasis;
  confianza?: ConfidenceBand;
  page?: number;
  pageSize?: number;
  sort?: string;
  order?: "asc" | "desc";
  /** The open record panel, so a row is a link. */
  ref?: string;
  adopcion?: string;
  settings?: string;
}

const DEFAULTS = {
  page: 1,
  pageSize: 25,
  order: "asc" as const,
  estadoPrecio: "live" as RowStateFilter,
};

/**
 * **Precios · `Propuestas de precio`** -- the pricing *analytics* surface
 * (§B.8.4·1), and **nothing on it changes a price**.
 *
 * The header has no `Aplicar propuestas` and no `Descartar`: the first wrote
 * prices and the second wrote a resolution that belongs to S1. There is no
 * checkbox column and no bulk bar, because there is no bulk action left for
 * them to carry. The row action is a **navigation** -- it opens the catalog's
 * own price editor with the suggested number already in the field -- and what
 * lands in `item_prices` is a `manual` row carrying that person's name.
 */
export function PricingPage({ search: raw }: { search: PricingSearch }) {
  const search = useMemo(() => ({ ...DEFAULTS, ...strip(raw) }), [raw]);
  const navigate = useNavigate();
  const me = useMe();
  const summary = usePricingSummary();
  const filters = usePricingFilters();
  const recalculate = useRecalculatePricing();
  const [typed, setTyped] = useState(search.q ?? "");
  const searchField = useRef<HTMLInputElement | null>(null);

  const go = useCallback(
    (next: Partial<PricingSearch>) =>
      void navigate({
        to: "/pricing",
        search: (previous: PricingSearch) => ({ ...previous, ...next }),
      }),
    [navigate],
  );
  // §B.8.5 · the search field writes its param debounced, so the typed text
  // lives in component state and the URL catches up once the typing stops.
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

  const query = usePricingItems({
    page: search.page,
    page_size: search.pageSize,
    sort: search.sort,
    order: search.order,
    q: search.q || undefined,
    manufacturer_id: search.laboratorio,
    category_id: search.categoria,
    state: search.estadoPrecio,
    basis: search.basePrecio,
    confidence: search.confianza,
  });

  const rows = query.data?.rows ?? [];
  const keys = useListKeys({
    rowCount: rows.length,
    rowId: (index) => `pricing-row-${index}`,
    pageKey: `${search.page}:${search.estadoPrecio ?? ""}`,
    onOpen: (index) => {
      const row = rows[index];
      if (row) go({ ref: row.item_id });
    },
    // §B.13.2 · `Esc` closes the panel before it clears the cursor, and `/`
    // reaches the search field. **`x`, `Shift+J`/`Shift+K` and `⌘Enter` are
    // gone with the selection and the approval they drove** -- there is nothing
    // on this surface to select and nothing to approve.
    onEscape: () => {
      if (!search.ref) return false;
      go({ ref: undefined });
      return true;
    },
    onSearch: () => searchField.current?.focus(),
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

  const owner = me.data?.role === "owner";
  const goal =
    summary.data?.margin_goal_pct == null
      ? null
      : Number(summary.data.margin_goal_pct);
  const laboratorio = filters.data?.manufacturers.find(
    (one) => one.id === search.laboratorio,
  );
  const categoria = filters.data?.categories.find(
    (one) => one.id === search.categoria,
  );
  const activeFilters = [
    search.q ? `«${search.q}»` : "",
    laboratorio ? `Laboratorio ${laboratorio.name}` : "",
    categoria ? `Categoría ${categoria.name}` : "",
    search.estadoPrecio && search.estadoPrecio !== DEFAULTS.estadoPrecio
      ? `Estado ${ROW_STATE[search.estadoPrecio]?.label ?? ""}`
      : "",
    search.basePrecio ? `Base ${BASIS_LABEL[search.basePrecio]}` : "",
    search.confianza ? `Confianza ${CONFIDENCE_LABEL[search.confianza]}` : "",
  ].filter(Boolean);
  const filtered = activeFilters.length > 0;

  const header = (
    <TopBar
      breadcrumb={<PricingBreadcrumb />}
      title="Propuestas de precio"
      actions={
        <>
          <TopBarButton
            variant="ghost"
            onClick={() => go({ adopcion: search.adopcion ? undefined : "1" })}
          >
            Adopción
          </TopBarButton>
          {/* §B.6.2 · exactly one primary, and an `admin` is **not rendered**
              it rather than shown one that refuses. */}
          {owner ? (
            <TopBarButton
              variant="primary"
              busy={recalculate.isPending}
              busyLabel="Calculando…"
              onClick={() => recalculate.mutate()}
            >
              Calcular ahora
            </TopBarButton>
          ) : null}
        </>
      }
    />
  );

  if (query.isError) {
    const failure = query.error as unknown;
    const offline = !(failure instanceof ApiError && failure.status > 0);
    return (
      <>
        {header}
        <Content>
          <RouteError
            title={
              offline ? "Sin conexión" : "No pudimos cargar las propuestas."
            }
            detail={
              offline
                ? "Precios necesita conexión para recalcular el precio y el costo de cada " +
                  "referencia. El mostrador sigue vendiendo con normalidad."
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
      {header}
      <div className="shrink-0 px-10 pb-6 pt-8">
        <Tiles
          summary={summary.data}
          loading={summary.isPending}
          onDefineGoal={() => go({ settings: "pricing" })}
          owner={owner}
        />
        {recalculate.isError ? (
          <div className="mt-4">
            <RegionError
              title="No pudimos recalcular las propuestas."
              detail={
                summary.data?.computed_at
                  ? `Se muestran las del ${dayMonth(summary.data.computed_at)}. Vuelva a intentarlo.`
                  : "Vuelva a intentarlo."
              }
              requestId={
                recalculate.error instanceof ApiError
                  ? recalculate.error.requestId
                  : undefined
              }
              onRetry={() => recalculate.mutate()}
            />
          </div>
        ) : null}
      </div>

      <FilterBar
        provenance={
          <>
            <Provenance summary={summary.data} />
            {summary.data?.allow_raise_without_cap ? (
              <span className="block">
                Se permiten alzas sin tope regulado conocido.
              </span>
            ) : null}
          </>
        }
      >
        <SearchField
          inputRef={searchField}
          value={typed}
          placeholder="Buscar una referencia"
          onChange={(next) => {
            setTyped(next);
            commitTerm(next);
          }}
        />
        <PickChip
          label="Laboratorio"
          value={search.laboratorio}
          options={filters.data?.manufacturers ?? []}
          onPick={(next) => go({ laboratorio: next, page: 1 })}
        />
        <PickChip
          label="Categoría"
          value={search.categoria}
          options={filters.data?.categories ?? []}
          onPick={(next) => go({ categoria: next, page: 1 })}
        />
        <FilterChip
          label="Estado"
          value={
            search.estadoPrecio
              ? ROW_STATE[search.estadoPrecio]?.label
              : undefined
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todas" },
                ...ROW_STATE_ORDER.map((one) => ({
                  value: one,
                  label: ROW_STATE[one].label,
                })),
              ]}
              value={search.estadoPrecio ?? ""}
              onPick={(next) => {
                go({
                  estadoPrecio: (next as RowStateFilter) || undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
        {/* **The one control that answers *how much of this screen is
            measured***, which is why it sits beside `Confianza` rather than in
            the record panel. */}
        <FilterChip
          label="Base"
          value={search.basePrecio ? BASIS_LABEL[search.basePrecio] : undefined}
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todas" },
                { value: "margin_rule", label: BASIS_LABEL.margin_rule },
                { value: "elasticity", label: BASIS_LABEL.elasticity },
              ]}
              value={search.basePrecio ?? ""}
              onPick={(next) => {
                go({
                  basePrecio: (next as ProposalBasis) || undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip
          label="Confianza"
          value={
            search.confianza ? CONFIDENCE_LABEL[search.confianza] : undefined
          }
        >
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todas" },
                { value: "high", label: CONFIDENCE_LABEL.high },
                { value: "medium", label: CONFIDENCE_LABEL.medium },
                { value: "low", label: CONFIDENCE_LABEL.low },
              ]}
              value={search.confianza ?? ""}
              onPick={(next) => {
                go({
                  confianza: (next as ConfidenceBand) || undefined,
                  page: 1,
                });
                close();
              }}
            />
          )}
        </FilterChip>
      </FilterBar>
      {search.adopcion ? (
        <div className="border-b border-hairline bg-canvas px-10 py-5">
          <AdoptionPanel onClose={() => go({ adopcion: undefined })} />
        </div>
      ) : null}
      <ProgressLine active={query.isFetching && !query.isPending} />

      <div className="flex min-h-0 flex-1">
        <TableContent>
          <DataTable<PricingRow>
            rows={rows}
            rowId={(row) => row.item_id}
            density="standard"
            minWidth={1440}
            loading={query.isPending}
            refetching={query.isFetching && !query.isPending}
            skeletonRows={15}
            skeletonWidths={[
              "70%",
              "60%",
              "50%",
              "50%",
              "45%",
              "55%",
              "60%",
              "70%",
            ]}
            containerProps={keys.containerProps}
            sort={search.sort}
            order={search.order}
            onSort={grid.toggleSort}
            rowProps={(row, index) => ({
              id: `pricing-row-${index}`,
              cursor: keys.cursor === index,
              current: row.item_id === search.ref,
              onClick: () => go({ ref: row.item_id }),
            })}
            columns={columns(goal, (row) => openEditor(navigate, row))}
            empty={
              <Empty
                filtered={filtered}
                active={activeFilters}
                summary={summary.data}
                owner={owner}
                onClear={() =>
                  go({
                    q: undefined,
                    laboratorio: undefined,
                    categoria: undefined,
                    basePrecio: undefined,
                    confianza: undefined,
                    estadoPrecio: undefined,
                    page: 1,
                  })
                }
                onDefineGoal={() => go({ settings: "pricing" })}
                onRecalculate={() => recalculate.mutate()}
              />
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

        {search.ref ? (
          <PricingRecordPanel
            itemId={search.ref}
            goal={goal}
            onClose={() => go({ ref: undefined })}
            onAdjust={(row) => openEditor(navigate, row)}
          />
        ) : null}
      </div>
    </>
  );
}

/**
 * **The row action is a navigation, not a write.** It opens S1's item editor at
 * its `Precio` section, pre-filled with the suggested price and carrying
 * `proposal_id`, so that whatever the person saves is a `manual` row with their
 * name and a link back to the suggestion that informed it.
 *
 * A **stale** suggestion opens the editor with **no pre-fill**: the person
 * prices it on today's numbers, or waits for Monday.
 */
function openEditor(
  navigate: ReturnType<typeof useNavigate>,
  row: PricingRow,
): void {
  const proposal = row.proposal;
  const prefill = proposal && !proposal.stale;
  void navigate({
    to: "/inventory/catalog",
    search: {
      item: row.item_id,
      proposal: prefill ? proposal.id : undefined,
      sugerido: prefill ? String(proposal.suggested_price) : undefined,
    },
  });
}

/** A row with a live suggestion may be carried into the editor. **Absent, not
 *  disabled**, on an `above_cap` row -- the price editor cannot be opened from
 *  this screen against a price already above a legal ceiling -- and absent on a
 *  resolved row, whose reference is in cooldown anyway. */
function canAdjust(row: PricingRow): boolean {
  return row.proposal?.status === "proposed";
}

function columns(goal: number | null, onAdjust: (row: PricingRow) => void) {
  return [
    {
      key: "name",
      label: "Producto",
      width: "24%",
      sortable: true,
      truncate: true,
      render: (row: PricingRow) => (
        <span className="block truncate">
          <span className="text-ink">{row.name}</span>
          {row.presentation ? (
            <span className="text-ink-label">{` ${DOT} ${row.presentation}`}</span>
          ) : null}
        </span>
      ),
    },
    {
      key: "manufacturer",
      label: "Laboratorio",
      width: "12%",
      sortable: true,
      truncate: true,
      render: (row: PricingRow) => row.manufacturer_name ?? "—",
    },
    {
      key: "cost",
      label: "Costo",
      width: "10%",
      align: "right" as const,
      numeric: true,
      render: (row: PricingRow) =>
        row.cost_basis === null ? "—" : money(Number(row.cost_basis)),
    },
    {
      key: "price",
      label: "Precio",
      width: "10%",
      align: "right" as const,
      numeric: true,
      render: (row: PricingRow) => (
        <span>
          {row.current_price === null ? "—" : money(Number(row.current_price))}
          {/* §B.8.4·1 · a hollow warning dot after the value on a reference
              carrying a cap. **It is a marker, not a status** -- metadata about
              a figure in the sense §B.9.2 gives the staleness dot -- so §B.7.3's
              rule against status-by-colour does not apply, and its accessible
              name carries the figure. */}
          {row.regulated_max_price !== null ? (
            <span
              className="ml-1.5 inline-block"
              aria-label={`Tope regulado ${money(Number(row.regulated_max_price))}`}
              title={`Tope regulado ${money(Number(row.regulated_max_price))}`}
            >
              <StatusDot family="warning" dot="hollow" />
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "margin",
      label: "Margen",
      width: "10%",
      align: "right" as const,
      numeric: true,
      render: (row: PricingRow) => (
        <span className="block">
          <span className="text-ink">
            {row.current_margin === null
              ? "—"
              : percent(Number(row.current_margin))}
          </span>
          {/* The gap to goal, beside the margin rather than one click away:
              it is the figure the margin rule acts on. Nothing at all when the
              row is at or above the goal, or when no goal is set. */}
          {row.margin_gap_pp !== null && goal !== null ? (
            <span className="block text-11 text-ink-note">
              {`meta ${percent(goal)} ${DOT} faltan ${points(Number(row.margin_gap_pp)).replace("+", "")}`}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "suggested",
      label: "Sugerido",
      width: "12%",
      align: "right" as const,
      numeric: true,
      render: (row: PricingRow) => <Suggested row={row} />,
    },
    {
      key: "impact",
      label: "Impacto mensual",
      width: "12%",
      align: "right" as const,
      numeric: true,
      render: (row: PricingRow) => {
        const proposal = row.proposal;
        if (!proposal) return "—";
        if (proposal.estimated_monthly_impact === null)
          // **Not `$0`.** A suggestion on a reference with no trailing sales has
          // no volume to project against, and a zero would read as *no impact*
          // rather than as *no basis for an impact*.
          return <span className="text-ink-note">Sin volumen</span>;
        return (
          <span className={proposal.stale ? "text-ink-note" : undefined}>
            {money(Number(proposal.estimated_monthly_impact))}
          </span>
        );
      },
    },
    {
      key: "state",
      label: "Estado",
      width: "10%",
      render: (row: PricingRow) => {
        const meaning = ROW_STATE[row.state];
        const line = basisLine(row.basis, row.confidence);
        return (
          <span className="relative flex flex-col items-start gap-0.5">
            <Badge family={meaning.family} dot={meaning.dot}>
              {meaning.label}
            </Badge>
            {/* **Every suggestion states its basis in its own row**, not only
                in the panel: an owner deciding which of two hundred suggestions
                is worth opening an editor for needs to see, without opening
                anything, which rest on evidence and which rest on margin
                arithmetic. */}
            {line ? (
              <span className="text-11 text-ink-note">{line}</span>
            ) : null}
            {/* **The row action is a navigation, not a write**, and it is
                revealed on hover and on focus over the metadata it covers --
                the row keeps its 48px whatever it shows. **Absent, not
                disabled**, on an `above_cap` row and on a resolved one:
                offering a button that leads to a denial is worse than not
                offering it. */}
            {canAdjust(row) ? (
              // **Revealed, not removed.** `display:none` would take the
              // button out of the document and with it out of the tab order,
              // so the "on focus" half of §B.6.1's reveal could never fire --
              // a row action a keyboard cannot reach is a row action a
              // keyboard user does not have. Opacity keeps it focusable and
              // the pointer events follow the opacity.
              <span className="pointer-events-none absolute -left-1 top-1/2 -translate-y-1/2 bg-hover-row pr-1 opacity-0 transition-opacity duration-140 group-hover:pointer-events-auto group-hover:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100">
                <Button
                  variant="secondary"
                  size="xs"
                  onClick={(event) => {
                    event.stopPropagation();
                    onAdjust(row);
                  }}
                >
                  Ajustar precio
                </Button>
              </span>
            ) : null}
          </span>
        );
      },
    },
  ];
}

/**
 * **The measurement column, and it renders three different things.**
 *
 * On a live suggestion, the suggested price alone. On a **resolved** one, what
 * the person actually chose with the suggestion beneath it -- the same
 * treatment, the same slot and the same reason as S6's `Sugerido` cell: the
 * deviation between the model's number and the human's is the only honest
 * measure of whether the model is trusted. On a **stale** one, the figures
 * greyed with a line naming what moved.
 */
function Suggested({ row }: { row: PricingRow }) {
  const proposal = row.proposal;
  if (!proposal) return <span className="text-ink-note">—</span>;
  if (proposal.resolved_price !== null) {
    return (
      <span className="block">
        <span className="text-ink">
          {money(Number(proposal.resolved_price))}
        </span>
        <span className="block text-12 text-ink-note">
          {`${DOT} sugerido ${money(Number(proposal.suggested_price))}`}
        </span>
      </span>
    );
  }
  if (proposal.status === "dismissed")
    return (
      <span className="text-ink-note">
        {money(Number(proposal.suggested_price))}
      </span>
    );
  return (
    <span className={proposal.stale ? "block text-ink-note" : "block"}>
      {money(Number(proposal.suggested_price))}
      {proposal.stale ? (
        <span className="block text-11 text-ink-note">
          {proposal.stale.detail}
        </span>
      ) : null}
    </span>
  );
}

function Tiles({
  summary,
  loading,
  owner,
  onDefineGoal,
}: {
  summary: ReturnType<typeof usePricingSummary>["data"];
  loading: boolean;
  owner: boolean;
  onDefineGoal: () => void;
}) {
  if (loading || !summary) {
    return (
      <div className="grid grid-cols-4 gap-4">
        <TileSkeleton />
        <TileSkeleton />
        <TileSkeleton />
        <TileSkeleton />
      </div>
    );
  }
  const goal =
    summary.margin_goal_pct == null ? null : Number(summary.margin_goal_pct);
  const projected =
    summary.projected_margin == null ? null : Number(summary.projected_margin);
  return (
    <div className="grid grid-cols-4 gap-4">
      <Tile
        label="Referencias con propuesta"
        figure={count(summary.references_with_proposal)}
        footnote={`${count(summary.estimable)} con elasticidad estimada`}
      />
      <Tile
        label="Impacto mensual estimado"
        figure={money(Number(summary.estimated_monthly_impact), {
          abbreviate: true,
        })}
        footnote="Si el volumen no cambia"
      />
      {/* §A.19.1's reference-and-progress variant, against `margin_goal_pct` --
          the same component as the Panel's `Margen bruto`, so the goal reads
          identically in both places. **With no goal set it is the only tile
          that changes**: the other three are computed from cost, price and
          caps, which exist without a goal. */}
      {goal === null ? (
        <Tile
          label="Margen proyectado"
          figure={<span className="text-20">Sin meta definida</span>}
          footnote={owner ? "Defínala en Ajustes → Precios y topes" : undefined}
          badge={
            owner ? (
              <button
                type="button"
                onClick={onDefineGoal}
                className="text-12 text-ink-body underline underline-offset-2"
              >
                Definir meta
              </button>
            ) : undefined
          }
        />
      ) : (
        <Tile
          label="Margen proyectado"
          figure={projected === null ? "—" : percent(projected)}
          reference={`meta ${percent(goal)}`}
          progress={{
            fill: projected === null ? 0 : (projected / 40) * 100,
            target: (goal / 40) * 100,
          }}
        />
      )}
      <Tile
        label="Propuestas sobre el tope"
        figure={count(summary.above_cap)}
        badge={
          summary.above_cap > 0 ? (
            <Badge family="critical" dot="solid">
              Revisar
            </Badge>
          ) : undefined
        }
        footnote={
          summary.above_cap > 0
            ? "El precio de venta supera el tope regulado"
            : "Ninguna referencia por encima de su tope"
        }
      />
    </div>
  );
}

/**
 * The provenance line, in the filter bar's right slot -- the same slot §B.8.5
 * gives the model's training line on Compras. **It carries the mix**, because
 * *how much of this screen is evidence yet* is the question a pilot review
 * opens with.
 */
function Provenance({
  summary,
}: {
  summary: ReturnType<typeof usePricingSummary>["data"];
}) {
  if (!summary?.computed_at) return null;
  const elasticity = summary.by_basis.elasticity ?? 0;
  const total = summary.references_with_proposal;
  return (
    <>
      {`Estimado al ${dayMonth(summary.computed_at)} ${DOT} 26 semanas ${DOT} ` +
        `v1 ${DOT} ${count(elasticity)} de ${count(total)} propuestas con elasticidad`}
    </>
  );
}

/**
 * §B.10.2 · **four kinds, and conflating them is the defect.**
 *
 * The first is the day-one state rather than an edge case, now that no default
 * margin goal ships; the last is the honest heart of the screen -- it reports
 * the method's reach in the same breath as its output, every week, without
 * anyone asking, and it names both engines' reasons rather than only the
 * estimator's.
 */
function Empty({
  filtered,
  active,
  summary,
  owner,
  onClear,
  onDefineGoal,
  onRecalculate,
}: {
  filtered: boolean;
  /** The filters actually set, in the words the chips used. */
  active: string[];
  summary: ReturnType<typeof usePricingSummary>["data"];
  owner: boolean;
  onClear: () => void;
  onDefineGoal: () => void;
  onRecalculate: () => void;
}) {
  if (summary && summary.margin_goal_pct === null) {
    return (
      <EmptyState
        title="Falta definir la meta de margen"
        body={
          "El motor de margen no propone nada hasta que exista una meta. La " +
          "elasticidad se sigue calculando y aparece en cuanto una referencia " +
          "tenga suficiente variación de precio."
        }
        actionLabel={owner ? "Definir meta" : undefined}
        onAction={owner ? onDefineGoal : undefined}
      />
    );
  }
  if (filtered) {
    return (
      <EmptyState
        kind="filtered"
        title="Ninguna referencia coincide con estos filtros"
        // §B.10.2 · **the active filters echoed verbatim**, because the reader
        // is looking at an empty table and the only useful sentence is the one
        // that names what they asked for.
        body={active.length ? active.join(` ${DOT} `) : "Sin filtros activos."}
        actionLabel="Quitar filtros"
        onAction={onClear}
      />
    );
  }
  const reasons = summary?.by_reason ?? {};
  const blocked = reasons.cap_blocks_raise ?? 0;
  // **Where the real cause is the cap default**, the body says that instead --
  // and it carries the link, because a screen that blames a missing model for
  // a setting the owner could change in a minute costs that owner a week.
  if (summary && blocked > 0) {
    return (
      <EmptyState
        title="No hay propuestas esta semana"
        body={
          `${count(blocked)} referencias sin tope regulado conocido. ` +
          "Las alzas están desactivadas hasta que se carguen los topes."
        }
        actionLabel={owner ? "Cargar topes" : undefined}
        onAction={owner ? onDefineGoal : undefined}
      />
    );
  }
  // **The honest heart of the screen**: it reports the method's reach in the
  // same breath as its output, every week, without anyone asking, and it names
  // both engines' reasons rather than only the estimator's.
  if (summary && summary.evaluated > 0) {
    return (
      <EmptyState
        kind="deliberate"
        title="No hay propuestas esta semana"
        body={
          `${count(summary.evaluated)} referencias evaluadas ${DOT} ` +
          `${count((summary.by_status?.insufficient_variation ?? 0) + (summary.by_status?.insufficient_observations ?? 0))} sin variación de precio suficiente para estimar ${DOT} ` +
          `${count(reasons.at_margin_goal ?? 0)} ya en la meta de margen ${DOT} ` +
          `${count((reasons.below_materiality ?? 0) + (reasons.margin_gap_immaterial ?? 0))} sin impacto material.`
        }
      />
    );
  }
  return (
    <EmptyState
      title="Todavía no hay propuestas de precio"
      body={
        "Ninguna referencia está por debajo de la meta de margen y todavía no hay " +
        "elasticidad estimada. Se recalcula cada lunes a las 03:00."
      }
      actionLabel={owner ? "Calcular ahora" : undefined}
      onAction={owner ? onRecalculate : undefined}
    />
  );
}

function PickChip({
  label,
  value,
  options,
  onPick,
}: {
  label: string;
  value: string | undefined;
  options: { id: string; name: string }[];
  onPick: (next: string | undefined) => void;
}) {
  const current = options.find((one) => one.id === value);
  return (
    <FilterChip label={label} value={current?.name}>
      {(close) => (
        <ChipOptions
          options={[
            { value: "", label: "Todos" },
            ...options.map((one) => ({ value: one.id, label: one.name })),
          ]}
          value={value ?? ""}
          onPick={(next) => {
            onPick(next || undefined);
            close();
          }}
        />
      )}
    </FilterChip>
  );
}

function strip(search: PricingSearch): PricingSearch {
  return Object.fromEntries(
    Object.entries(search).filter(([, value]) => value !== undefined),
  ) as PricingSearch;
}
