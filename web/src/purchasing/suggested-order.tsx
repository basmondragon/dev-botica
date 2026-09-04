import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import { useCategories } from "@/api/catalog";
import {
  useDemandForecasts,
  useGenerateOrders,
  useOrderAction,
  usePurchaseOrder,
  usePurchaseOrders,
  useSetLineQuantity,
  type ConfidenceBand,
  type ForecastBasis,
  type PurchaseOrderLineRow,
} from "@/api/purchasing";
import { useLocations } from "@/api/queries";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import { PANEL_INSET } from "@/ui/inset";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { ChipOptions, FilterBar, FilterChip } from "@/ui/filter-bar";
import { count as formatCount, money, DOT } from "@/ui/format";
import { ConfirmDialog, Modal } from "@/ui/panel";
import { EmptyState, ProgressLine, RegionError, RouteError } from "@/ui/states";
import { QuantityStepper } from "@/ui/stepper";
import { useToast } from "@/ui/toast";
import { Badge, StatusDot } from "@/ui/status";
import { DataTable, TableFooter } from "@/ui/table";
import { Tile, TileSkeleton } from "@/ui/tile";
import { routeGrid } from "@/ui/use-grid";
import { useListKeys } from "@/ui/use-list-keys";
import { PurchasingBreadcrumb } from "./breadcrumb";
import { LinePanel } from "./line-panel";
import {
  BAND_FAMILY,
  BAND_LABEL,
  BAND_ORDER,
  BASIS_LABEL,
  BASIS_ORDER,
  COVERAGE_INK,
  ORDER_STATUS,
  bandPrefix,
  coverageFamily,
  coverageReading,
  provenanceLine,
  reasonText,
  refreshedAt,
  REVIEW_FIRST,
} from "./vocabulary";

export interface OrderSearch {
  orden?: string;
  proveedor?: string;
  sede?: string;
  base?: ForecastBasis[];
  banda?: ConfidenceBand[];
  categoria?: string;
  page?: number;
  pageSize?: number;
  sort?: string;
  order?: "asc" | "desc";
  row?: string;
  settings?: string;
}

export const DEFAULTS = {
  page: 1,
  pageSize: 25,
  order: "asc" as const,
};

/**
 * Handoff pantalla 4 · **Compras · Orden sugerida**.
 *
 * Four KPI tiles, the six-column table at the drawn widths with the coloured
 * `Cobertura` and the editable `Sugerido`, the four filter chips including
 * **Confianza del modelo**, the training-provenance line and the footer total.
 *
 * **Online-only, and it says so** (§4, A4). Compras is the office read model:
 * server-authoritative over the network, fetched per view, never synced to a
 * browser. There is no partial mode, no read-only cache of the last order and
 * no queued approval -- an approval sent into a void that lands twenty minutes
 * later against a supplier who has already shipped is worse than one that did
 * not happen.
 */
export function SuggestedOrderPage({ search: raw }: { search: OrderSearch }) {
  const search = useMemo(() => ({ ...DEFAULTS, ...strip(raw) }), [raw]);
  const navigate = useNavigate();
  const [approving, setApproving] = useState(false);
  const [discarding, setDiscarding] = useState(false);

  const go = useCallback(
    (next: Partial<OrderSearch>) =>
      void navigate({
        to: "/purchasing",
        search: (previous: OrderSearch) => ({ ...previous, ...next }),
      }),
    [navigate],
  );

  const locations = useLocations();
  const categories = useCategories();

  // The chips pick the order; the order is what the screen is about. A
  // `suggested` order per supplier per sede exists every morning, so the
  // default view is the first one waiting rather than a picker somebody has to
  // answer before the screen says anything.
  const waiting = usePurchaseOrders({
    status: ["suggested"],
    location_id: search.sede ? [search.sede] : undefined,
    page: 1,
    page_size: 100,
    order: "asc",
    sort: "created_at",
  });
  const orders = waiting.data?.rows ?? [];
  // **A named order stays on the screen after it is approved.** `waiting` lists
  // only what is still `suggested`, so resolving the id through that list would
  // throw a person off their own order the instant they pressed the button --
  // and the badge and the `Ver envío` action they need next are on it.
  const chosenId =
    search.orden ??
    orders.find(
      (one) => !search.proveedor || one.supplier_id === search.proveedor,
    )?.id ??
    orders[0]?.id;

  const grid = routeGrid(
    {
      page: search.page,
      pageSize: search.pageSize,
      sort: search.sort,
      order: search.order,
    },
    (next) => go(next),
  );

  const detail = usePurchaseOrder(chosenId, {
    page: search.page,
    page_size: search.pageSize,
    sort: search.sort,
    order: search.order,
    basis: search.base,
    band: search.banda,
    category_id: search.categoria,
  });

  const toast = useToast();
  const setQuantity = useSetLineQuantity(chosenId ?? "");
  const approve = useOrderAction("approve");
  const discard = useOrderAction("discard");
  const markSent = useOrderAction("mark-sent");
  const generate = useGenerateOrders();

  const lines = detail.data?.lines ?? [];
  const order = detail.data?.order;
  const openId = search.row;

  const keys = useListKeys({
    rowCount: lines.length,
    rowId: (index) => `order-line-${index}`,
    pageKey: `${chosenId ?? ""}:${search.page}`,
    onOpen: (index) => {
      const line = lines[index];
      if (line) go({ row: line.id });
    },
    onEscape: () => {
      if (!openId) return false;
      go({ row: undefined });
      return true;
    },
    onNextPage: () => {
      if (search.page * search.pageSize >= (detail.data?.row_count ?? 0))
        return false;
      grid.setPage(search.page + 1);
    },
    onPreviousPage: () => {
      if (search.page <= 1) return false;
      grid.setPage(search.page - 1);
    },
  });

  const filtered =
    (search.base?.length ?? 0) > 0 ||
    (search.banda?.length ?? 0) > 0 ||
    !!search.categoria;

  const activeFilters = [
    search.base?.map((one) => BASIS_LABEL[one]).join(", "),
    search.banda?.map((one) => BAND_LABEL[one]).join(", "),
    search.categoria
      ? categories.data?.find((one) => one.id === search.categoria)?.name
      : undefined,
  ].filter(Boolean) as string[];

  if (waiting.isError || detail.isError) {
    const failure = (waiting.error ?? detail.error) as unknown;
    // **Anything that is not a refusal the server sent is a connection that is
    // not there.** `fetch` rejects on a dead network, so the error that reaches
    // here is not necessarily an `ApiError` at all -- and Compras is the office
    // read model, so a screen that could not reach the server has nothing true
    // to show (§4, A4).
    const offline = !(failure instanceof ApiError && failure.status > 0);
    return (
      <>
        <Header title="Orden sugerida" />
        <Content>
          <RouteError
            title={offline ? "Sin conexión" : "No pudimos cargar la orden."}
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
            onRetry={() => {
              void waiting.refetch();
              void detail.refetch();
            }}
          />
        </Content>
      </>
    );
  }

  if (!waiting.isPending && orders.length === 0 && !search.orden) {
    return (
      <>
        <Header title="Orden sugerida" />
        <Content>
          <NoOrders
            locationId={search.sede}
            onGenerate={() => generate.mutate(search.sede)}
            onPolicies={() => void navigate({ to: "/inventory", search: {} })}
          />
        </Content>
      </>
    );
  }

  const provenance = detail.data?.provenance;
  const locked = !!order && order.status !== "suggested";

  return (
    <>
      <Header
        title={
          order
            ? `Orden ${order.status === "suggested" ? "sugerida " : ""}${order.number}`
            : "Orden sugerida"
        }
        badge={
          order && order.status !== "suggested" ? (
            <Badge
              family={ORDER_STATUS[order.status].family}
              dot={ORDER_STATUS[order.status].dot}
            >
              {ORDER_STATUS[order.status].label}
            </Badge>
          ) : null
        }
        actions={
          order && !locked ? (
            <>
              <TopBarButton
                variant="secondary"
                onClick={() => setDiscarding(true)}
              >
                Descartar
              </TopBarButton>
              <TopBarButton
                variant="primary"
                onClick={() => setApproving(true)}
              >
                Aprobar y enviar
              </TopBarButton>
            </>
          ) : order ? (
            <TopBarButton
              variant="secondary"
              onClick={() =>
                void navigate({
                  to: "/purchasing/orders",
                  search: { orden: order.id },
                })
              }
            >
              Ver envío
            </TopBarButton>
          ) : null
        }
      />

      <FilterBar
        provenance={
          provenance ? (
            <span>
              {provenanceLine(
                provenance.basis,
                provenance.window,
                refreshedAt(provenance.computed_at),
                provenance.model_prose,
              )}
            </span>
          ) : null
        }
      >
        <FilterChip label="Proveedor" value={order?.supplier_name}>
          {(close) => (
            <ChipOptions
              options={orders.map((one) => ({
                value: one.id,
                label: `${one.supplier_name} ${DOT} ${one.location_name}`,
              }))}
              value={chosenId ?? ""}
              onPick={(next) => {
                go({ orden: next, page: 1, row: undefined });
                close();
              }}
            />
          )}
        </FilterChip>
        <FilterChip label="Sede" value={order?.location_name}>
          {(close) => (
            <ChipOptions
              options={[
                { value: "", label: "Todas" },
                ...(locations.data ?? []).map((one) => ({
                  value: one.id,
                  label: one.name,
                })),
              ]}
              value={search.sede ?? ""}
              onPick={(next) => {
                go({
                  sede: next || undefined,
                  orden: undefined,
                  page: 1,
                  row: undefined,
                });
                close();
              }}
            />
          )}
        </FilterChip>
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
                    ? `${one.parent_name} ${DOT} ${one.name}`
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
        <ConfidenceChip
          basis={search.base ?? []}
          band={search.banda ?? []}
          basisCounts={detail.data?.basis_counts ?? {}}
          bandCounts={detail.data?.band_counts ?? {}}
          onChange={(next) => go({ ...next, page: 1 })}
        />
      </FilterBar>
      <ProgressLine active={detail.isFetching && !detail.isPending} />

      <div className="flex min-h-0 flex-1">
        {/* §B.3 · `28px 40px`, because this route's `main` is one working
            panel: the four tiles, the table and the footer are one thing a
            person reads top to bottom. */}
        <TableContent className={PANEL_INSET}>
          {order && order.last_dispatch_error ? (
            <div className="mb-4">
              <RegionError
                title={`No pudimos enviar la orden ${order.number} a ${order.supplier_name}.`}
                detail={`${order.last_dispatch_error} Intentos de envío: ${order.dispatch_attempts}.`}
                retryLabel="Reintentar ahora"
                onRetry={() => approve.mutate(order.id)}
              >
                {/* The second way out, and it is not a lesser one: a supplier
                    with no address on file is dispatched by a person through
                    whatever channel they already use, and the order still has
                    to reach `Enviada al proveedor` so it can be received
                    against. */}
                <Button
                  size="sm"
                  variant="secondary"
                  className="mt-3"
                  onClick={() => markSent.mutate(order.id)}
                >
                  Marcar como enviada
                </Button>
              </RegionError>
            </div>
          ) : null}

          <div className="mb-4 grid grid-cols-4 gap-4">
            {detail.isPending
              ? [0, 1, 2, 3].map((one) => <TileSkeleton key={one} />)
              : (detail.data?.kpis ?? []).map((kpi) => (
                  <Tile
                    key={kpi.key}
                    label={kpi.label}
                    // §B.9.2 tier 3 · a figure that cannot be computed is an em
                    // dash with its reason in the secondary slot, **never a
                    // zero**. Three of these four rest on a demand estimate,
                    // and on a parametric order there is none.
                    figure={
                      kpi.figure === null || kpi.figure === undefined ? (
                        <span className="text-ink-soft">—</span>
                      ) : (
                        formatKpi(kpi.key, Number(kpi.figure))
                      )
                    }
                    footnote={
                      kpi.key === "suggested_references"
                        ? // §A.11 · the thousands dot is the client's, so a
                          // denominator of 1.184 does not render as 1184.
                          `de ${formatCount(
                            detail.data?.active_reference_count ?? 0,
                          )} activas en la sede`
                        : kpi.reading
                    }
                  />
                ))}
          </div>

          <DataTable<PurchaseOrderLineRow>
            rows={lines}
            rowId={(line) => line.id}
            density="standard"
            minWidth={1080}
            loading={detail.isPending}
            refetching={detail.isFetching && !detail.isPending}
            skeletonRows={11}
            skeletonWidths={["70%", "40%", "45%", "35%", "50%", "80%"]}
            containerProps={keys.containerProps}
            sort={search.sort}
            order={search.order}
            onSort={grid.toggleSort}
            rowProps={(line, index) => ({
              id: `order-line-${index}`,
              cursor: keys.cursor === index,
              current: openId === line.id,
              onClick: () => go({ row: line.id }),
            })}
            columns={columns({
              locked,
              onCommit: (line, quantity) =>
                setQuantity.mutate(
                  { lineId: line.id, quantity },
                  {
                    // §B.10.1 · the revert is silent in the cell and named
                    // once, so a write that did not land is never mistaken for
                    // one that did.
                    onError: (error) =>
                      toast(
                        error instanceof ApiError
                          ? error.message
                          : "No pudimos guardar la cantidad.",
                      ),
                  },
                ),
            })}
            empty={
              filtered ? (
                <EmptyState
                  kind="filtered"
                  title="Ninguna referencia coincide con estos filtros"
                  body={`Filtros activos: ${activeFilters.join(` ${DOT} `)}.`}
                  actionLabel="Quitar filtros"
                  onAction={() =>
                    go({
                      base: undefined,
                      banda: undefined,
                      categoria: undefined,
                      page: 1,
                    })
                  }
                />
              ) : (
                <EmptyState
                  kind="deliberate"
                  title="No hay nada que pedir en esta sede"
                  body={`Las ${formatCount(
                    detail.data?.active_reference_count ?? 0,
                  )} referencias activas tienen cobertura suficiente.`}
                />
              )
            }
            footer={
              <TableFooter
                page={search.page}
                pageSize={search.pageSize}
                rowCount={detail.data?.row_count}
                loading={detail.isPending}
                onPage={grid.setPage}
                onPageSize={(next) => go({ pageSize: next, page: 1 })}
                annotation={undefined}
              />
            }
          />

          <div className="mt-3 flex items-baseline justify-between">
            <span className="text-11 text-ink-label">
              {`Mostrando ${formatCount(lines.length)} de ${formatCount(
                detail.data?.suggested_reference_count ?? 0,
              )} referencias sugeridas`}
            </span>
            <span className="flex items-baseline gap-2">
              <span className="text-11 text-ink-label">Total de la orden</span>
              {/* §A.11 · a table-cell figure, so **never abbreviated**: this is
                  where the exact number is read. */}
              <span className="text-20 tracking-display tabular-nums text-ink">
                {money(Number(order?.total ?? 0))}
              </span>
            </span>
          </div>
        </TableContent>

        {openId ? (
          <LinePanel
            line={lines.find((one) => one.id === openId)}
            order={order}
            onClose={() => go({ row: undefined })}
          />
        ) : null}
      </div>

      <Modal
        open={approving && !!order}
        title="Aprobar y enviar la orden"
        size="confirm"
        busy={approve.isPending}
        onClose={() => setApproving(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setApproving(false)}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              busy={approve.isPending}
              onClick={() =>
                order &&
                approve.mutate(order.id, {
                  onSuccess: () => {
                    setApproving(false);
                    go({ orden: order.id });
                  },
                })
              }
            >
              Aprobar y enviar
            </Button>
          </>
        }
      >
        <p className="mt-2 text-14 text-ink-body">
          {order
            ? `${order.supplier_name} ${DOT} ${order.location_name} ${DOT} ${formatCount(
                detail.data?.suggested_reference_count ?? 0,
              )} referencias ${DOT} ${money(Number(order.total))}. Las cantidades quedan congeladas y la orden sale al proveedor.`
            : ""}
        </p>
      </Modal>

      <ConfirmDialog
        open={discarding && !!order}
        title="Descartar la orden"
        body={
          order
            ? `La orden ${order.number} de ${order.supplier_name} deja de estar pendiente y no se envía a nadie. El modelo propondrá una nueva mañana.`
            : ""
        }
        confirmLabel="Descartar orden"
        busy={discard.isPending}
        onConfirm={() =>
          order &&
          discard.mutate(order.id, { onSuccess: () => setDiscarding(false) })
        }
        onCancel={() => setDiscarding(false)}
      />
    </>
  );
}

/**
 * Three empty states that look the same and mean different things (§B.10.2).
 *
 * **Never populated** -- the model has not run here yet, and the copy must not
 * promise a minimum history: it proposes from day one on the sede's own
 * parameters, and body text saying otherwise teaches a pilot to sit and wait
 * for a screen that is already working.
 *
 * **Nothing to propose from** -- the model *has* run, has measured nothing, and
 * the sede has no reorder points to fall back on. **This state is the decision
 * working**, not a failure: an order of guessed quantities would be worse than
 * no order at all, so the action points at Existencias rather than at a
 * `Generar ahora` that would change nothing.
 *
 * **Nothing to buy** -- the model has measured this sede and everything on the
 * shelf has cover. That is the product working too, and it takes no action.
 */
function NoOrders({
  locationId,
  onGenerate,
  onPolicies,
}: {
  locationId: string | undefined;
  onGenerate: () => void;
  onPolicies: () => void;
}) {
  const measured = useDemandForecasts(locationId, ["learning", "learned"]);
  const any = useDemandForecasts(locationId, [], !!locationId);

  if (!locationId || any.isPending || measured.isPending) {
    return (
      <EmptyState
        title="Todavía no hay órdenes sugeridas"
        body={
          "El modelo propone una orden por proveedor y sede cada mañana. " +
          "Sin histórico usa el punto de reorden de la sede."
        }
        actionLabel="Generar ahora"
        onAction={onGenerate}
      />
    );
  }
  if ((any.data?.row_count ?? 0) === 0) {
    return (
      <EmptyState
        title="Todavía no hay órdenes sugeridas"
        body={
          "El modelo propone una orden por proveedor y sede cada mañana. " +
          "Sin histórico usa el punto de reorden de la sede."
        }
        actionLabel="Generar ahora"
        onAction={onGenerate}
      />
    );
  }
  if ((measured.data?.row_count ?? 0) === 0) {
    return (
      <EmptyState
        kind="filtered"
        title="Todavía no hay con qué proponer"
        body={
          "Sin histórico y sin puntos de reorden en la sede, el modelo no " +
          "inventa una cantidad. Defina los puntos de reorden en Existencias o " +
          "cargue el histórico de ventas."
        }
        actionLabel="Ir a Existencias"
        onAction={onPolicies}
      />
    );
  }
  return (
    <EmptyState
      kind="deliberate"
      title="No hay nada que pedir en esta sede"
      body="Las referencias activas tienen cobertura suficiente."
    />
  );
}

function formatKpi(key: string, figure: number) {
  // §A.11 · a display figure abbreviates above a million; a count does not, and
  // the counterfactual carries U+2212 rather than a hyphen.
  if (key === "order_value" || key === "manual_order_saving")
    return money(figure, { abbreviate: true });
  return formatCount(figure);
}

/** §A.17 · the drawn widths. Producto 26 · Stock 11 · Venta / sem 14 ·
 *  Cobertura 13 · Sugerido 13 · Por qué 23. */
function columns({
  locked,
  onCommit,
}: {
  locked: boolean;
  onCommit: (line: PurchaseOrderLineRow, quantity: number) => void;
}) {
  return [
    {
      key: "item",
      label: "Producto",
      width: "26%",
      sortable: true,
      truncate: true,
      sticky: true,
      className: "sticky left-0 bg-inherit",
      render: (line: PurchaseOrderLineRow) => (
        <span className="block truncate text-ink">
          {line.item_name}
          {line.presentation ? (
            <span className="ml-1.5 text-12 text-ink-label">
              {line.presentation}
            </span>
          ) : null}
        </span>
      ),
    },
    {
      key: "stock",
      label: "Stock",
      width: "11%",
      align: "right" as const,
      numeric: true,
      sortable: true,
      render: (line: PurchaseOrderLineRow) => formatCount(line.stock),
    },
    {
      key: "weekly",
      label: "Venta / sem",
      width: "14%",
      align: "right" as const,
      numeric: true,
      sortable: true,
      render: (line: PurchaseOrderLineRow) =>
        line.weekly_sales === null || line.weekly_sales === undefined ? (
          <Dash reading={coverageReading(line)} />
        ) : (
          formatCount(Math.round(Number(line.weekly_sales)))
        ),
    },
    {
      key: "coverage",
      label: "Cobertura",
      width: "13%",
      align: "right" as const,
      numeric: true,
      sortable: true,
      // §B.7.4 · coloured by urgency, as a numeral with no dot. The `Por qué`
      // cell always restates the reading in words, so the colour is never the
      // only signal (§B.12.3).
      render: (line: PurchaseOrderLineRow) => {
        const days =
          line.coverage_days === null || line.coverage_days === undefined
            ? null
            : Math.round(Number(line.coverage_days));
        if (days === null) return <Dash reading={coverageReading(line)} />;
        return (
          <span
            className={cn("tabular-nums", COVERAGE_INK[coverageFamily(days)])}
          >
            {formatCount(days)}
          </span>
        );
      },
    },
    {
      key: "suggested",
      label: "Sugerido",
      width: "13%",
      align: "right" as const,
      numeric: true,
      sortable: true,
      render: (line: PurchaseOrderLineRow) =>
        locked ? (
          // §B.5.2 · an order past `suggested` renders its quantities read-only
          // at full-strength text: they are what the supplier was sent.
          <span className="inline-block rounded-control bg-chrome px-3 py-[5px] text-14 tabular-nums text-ink">
            {formatCount(line.approved_quantity)}
          </span>
        ) : (
          <span onClick={(event) => event.stopPropagation()}>
            <QuantityStepper
              value={line.approved_quantity}
              label={`Cantidad sugerida de ${line.item_name}`}
              onCommit={(next) => onCommit(line, next)}
            />
          </span>
        ),
    },
    {
      key: "reason",
      label: "Por qué",
      width: "23%",
      sortable: true,
      render: (line: PurchaseOrderLineRow) => {
        const band = bandPrefix(line);
        const adjusted =
          line.suggested_quantity !== null &&
          line.suggested_quantity !== undefined &&
          line.suggested_quantity !== line.approved_quantity;
        return (
          <span className="block text-12 text-ink-label">
            {band ? (
              <>
                <StatusDot family={BAND_FAMILY[line.band!]} dot="hollow" />
                <span className="mr-1 text-ink-body">{band}</span>
                <span className="mr-1 text-ink-disabled">{DOT}</span>
              </>
            ) : null}
            {reasonText(line)}
            {adjusted ? (
              <span className="text-ink-label">{` ${DOT} ajustado de ${formatCount(
                line.suggested_quantity!,
              )}`}</span>
            ) : null}
          </span>
        );
      },
    },
  ];
}

/**
 * **Confianza del modelo** -- the chip that makes a day-one order reviewable.
 *
 * It filters on two readings at once, because on a cold-start tenant they are
 * not the same population: the basis says what the model computed from, the band
 * says how much it knows. Selecting across the groups intersects; selecting
 * inside one unions. Its first entry is the shortcut the affordance exists for.
 */
function ConfidenceChip({
  basis,
  band,
  basisCounts,
  bandCounts,
  onChange,
}: {
  basis: ForecastBasis[];
  band: ConfidenceBand[];
  basisCounts: Record<string, number>;
  bandCounts: Record<string, number>;
  onChange: (next: {
    base?: ForecastBasis[];
    banda?: ConfidenceBand[];
  }) => void;
}) {
  const chosen = [...basis, ...band];
  const value =
    chosen.length === 0
      ? undefined
      : chosen.length === 1
        ? (BASIS_LABEL[basis[0] as ForecastBasis] ??
          BAND_LABEL[band[0] as ConfidenceBand])
        : String(chosen.length);

  const toggle = <T extends string>(list: T[], one: T) =>
    list.includes(one) ? list.filter((each) => each !== one) : [...list, one];

  return (
    <FilterChip label="Confianza del modelo" value={value}>
      {(close) => (
        <div className="max-h-72 overflow-y-auto">
          <button
            type="button"
            onClick={() => {
              onChange({
                base: REVIEW_FIRST.basis,
                banda: REVIEW_FIRST.band,
              });
              close();
            }}
            className="flex h-8 w-full items-center rounded-control px-2.5 text-left text-12 font-medium text-ink transition-colors duration-140 ease-out hover:bg-hover-row"
          >
            Revisar primero
          </button>
          <p className="px-2.5 pb-1 pt-3 font-mono text-10 uppercase tracking-eyebrow text-ink-note">
            Base
          </p>
          {BASIS_ORDER.map((one) => (
            <ChipToggle
              key={one}
              label={BASIS_LABEL[one]}
              count={basisCounts[one] ?? 0}
              checked={basis.includes(one)}
              onToggle={() => onChange({ base: toggle(basis, one) })}
            />
          ))}
          <p className="px-2.5 pb-1 pt-3 font-mono text-10 uppercase tracking-eyebrow text-ink-note">
            Confianza
          </p>
          {BAND_ORDER.map((one) => (
            <ChipToggle
              key={one}
              label={BAND_LABEL[one]}
              count={bandCounts[one] ?? 0}
              checked={band.includes(one)}
              onToggle={() => onChange({ banda: toggle(band, one) })}
            />
          ))}
        </div>
      )}
    </FilterChip>
  );
}

function ChipToggle({
  label,
  count,
  checked,
  onToggle,
}: {
  label: string;
  count: number;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      className={cn(
        "flex h-8 w-full cursor-pointer items-center gap-2.5 rounded-control px-2.5 text-12",
        "transition-colors duration-140 ease-out hover:bg-hover-row",
        checked ? "font-medium text-ink" : "text-ink-body",
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        className="size-3.5 accent-ink"
      />
      <span className="flex-1">{label}</span>
      <span className="tabular-nums text-ink-label">{formatCount(count)}</span>
    </label>
  );
}

function Dash({ reading }: { reading: string }) {
  return (
    <span className="text-ink-soft" title={reading} aria-label={reading}>
      —
    </span>
  );
}

function strip(search: OrderSearch): OrderSearch {
  return Object.fromEntries(
    Object.entries(search).filter(([, value]) => value !== undefined),
  ) as OrderSearch;
}

function Header({
  title,
  badge,
  actions,
}: {
  title: string;
  /** §B.7.3 · this surface's one badge, and it appears only once the order has
   *  left `Sugerida` -- a badge saying what the screen's own title already says
   *  is a badge nobody reads. */
  badge?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <TopBar
      breadcrumb={<PurchasingBreadcrumb />}
      title={title}
      actions={
        <>
          {badge}
          {actions}
        </>
      }
    />
  );
}
