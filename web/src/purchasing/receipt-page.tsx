import { useMemo, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import {
  useConfirmReceipt,
  useGoodsReceipt,
  useGoodsReceipts,
  useOpenReceipt,
  usePurchaseOrder,
  usePurchaseOrders,
  type GoodsReceiptLineRow,
  type ReceiptLineInput,
} from "@/api/purchasing";
import { Content, TableContent, TopBar, TopBarButton } from "@/shell/shell";
import { PANEL_INSET } from "@/ui/inset";
import { Input } from "@/ui/field";
import { count as formatCount, money, monthYear, DOT } from "@/ui/format";
import { EmptyState, ProgressLine, RegionError, RouteError } from "@/ui/states";
import { Select } from "@/ui/select";
import { QuantityStepper } from "@/ui/stepper";
import { Badge } from "@/ui/status";
import { DataTable } from "@/ui/table";
import { useListKeys } from "@/ui/use-list-keys";
import { PurchasingBreadcrumb } from "./breadcrumb";
import { ORDER_STATUS } from "./vocabulary";

export interface ReceiptSearch {
  orden?: string;
  recepcion?: string;
  settings?: string;
}

interface Draft {
  quantity: number;
  lotCode: string;
  expiry: string;
}

/**
 * **Compras · Recepción** -- receiving against an order.
 *
 * `Recibido` defaults to `approved_quantity`, because the common case is the
 * whole order arriving and the screen should ask a person to correct what did
 * not. **Over-delivery is accepted and flagged, never refused**: the supplier
 * sent them, they are on the shelf, and a receiving screen that refuses reality
 * is a screen that gets bypassed with a manual adjustment.
 *
 * `Confirmar recepción` is the atomic act -- lots created or attached, one call
 * per line into S3's ledger service, the cost actually paid and the lead time
 * actually observed written back, the order settled. It is idempotent on the
 * receipt, so a double click moves stock exactly once.
 */
export function ReceiptPage({ search }: { search: ReceiptSearch }) {
  const navigate = useNavigate();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [failure, setFailure] = useState<ApiError | null>(null);

  const receivable = usePurchaseOrders({
    status: ["sent", "partially_received"],
    page: 1,
    page_size: 100,
    order: "desc",
    sort: "created_at",
  });
  const orders = receivable.data?.rows ?? [];
  const chosen = orders.find((one) => one.id === search.orden) ?? orders[0];

  const existing = useGoodsReceipts(
    {
      purchase_order_id: chosen?.id,
      status: "draft",
      page: 1,
      page_size: 1,
    },
    !!chosen,
  );
  const openReceipt = useOpenReceipt();
  const receiptId =
    search.recepcion ?? existing.data?.rows?.[0]?.id ?? openReceipt.data?.id;
  const receipt = useGoodsReceipt(receiptId);
  const order = usePurchaseOrder(chosen?.id, {
    page: 1,
    page_size: 100,
    order: "asc",
  });
  const confirm = useConfirmReceipt(receiptId ?? "");

  const lines = useMemo(() => receipt.data?.lines ?? [], [receipt.data?.lines]);

  // §B.13.2 · `j` and `k` on every office list. `Enter`, `x` and `/` are
  // deliberately unbound here: a receipt line has no record to open, no bulk
  // action to toggle it into, and the surface has no search field -- and a key
  // that answers with nothing teaches a person the shortcuts do not work.
  const keys = useListKeys({
    rowCount: lines.length,
    rowId: (index) => `receipt-line-${index}`,
    pageKey: receiptId ?? "",
  });

  function draftFor(line: GoodsReceiptLineRow): Draft {
    return (
      drafts[line.id] ?? {
        quantity: line.quantity,
        lotCode: line.lot_code,
        expiry: line.expires_at ? monthYear(line.expires_at) : "",
      }
    );
  }

  function setDraft(line: GoodsReceiptLineRow, next: Partial<Draft>) {
    setDrafts((current) => ({
      ...current,
      [line.id]: { ...draftFor(line), ...next },
    }));
  }

  const payload: ReceiptLineInput[] = lines.map((line) => {
    const draft = draftFor(line);
    return {
      id: line.id,
      item_id: line.item_id,
      purchase_order_line_id: line.purchase_order_line_id,
      quantity: draft.quantity,
      lot_code: draft.lotCode,
      expires_at: monthToDate(draft.expiry),
      unit_cost: line.unit_cost,
    };
  });

  if (receivable.isError) {
    const error = receivable.error as unknown;
    const offline = !(error instanceof ApiError && error.status > 0);
    return (
      <>
        <TopBar breadcrumb={<PurchasingBreadcrumb />} title="Recepción" />
        <Content>
          <RouteError
            title={offline ? "Sin conexión" : "No pudimos cargar la recepción."}
            detail={
              offline
                ? "Compras necesita conexión para leer el inventario de la red. " +
                  "El mostrador sigue vendiendo con normalidad."
                : error instanceof ApiError
                  ? error.message
                  : "Intente de nuevo."
            }
            onRetry={() => void receivable.refetch()}
          />
        </Content>
      </>
    );
  }

  if (!receivable.isPending && orders.length === 0) {
    return (
      <>
        <TopBar breadcrumb={<PurchasingBreadcrumb />} title="Recepción" />
        <Content>
          <EmptyState
            kind="deliberate"
            title="No hay órdenes por recibir"
            body="Una recepción se abre contra una orden que ya salió al proveedor."
          />
        </Content>
      </>
    );
  }

  const confirmed = receipt.data?.status === "confirmed";

  return (
    <>
      <TopBar
        breadcrumb={<PurchasingBreadcrumb />}
        title={chosen ? `Recepción ${DOT} Orden ${chosen.number}` : "Recepción"}
        actions={
          receiptId && !confirmed ? (
            <TopBarButton
              variant="primary"
              busy={confirm.isPending}
              onClick={() => {
                setFailure(null);
                confirm.mutate(payload, {
                  onError: (error) =>
                    setFailure(
                      error instanceof ApiError
                        ? error
                        : new ApiError("No pudimos confirmar la recepción.", 0),
                    ),
                });
              }}
            >
              Confirmar recepción
            </TopBarButton>
          ) : receiptId ? (
            <TopBarButton
              variant="secondary"
              onClick={() =>
                void navigate({ to: "/purchasing/orders", search: {} })
              }
            >
              Ver órdenes
            </TopBarButton>
          ) : (
            <TopBarButton
              variant="primary"
              busy={openReceipt.isPending}
              onClick={() => chosen && openReceipt.mutate(chosen.id)}
            >
              Abrir recepción
            </TopBarButton>
          )
        }
      />
      <ProgressLine active={receipt.isFetching && !receipt.isPending} />

      <TableContent className={PANEL_INSET}>
        {chosen ? (
          <div className="mb-4 flex items-center gap-3">
            <Badge
              family={ORDER_STATUS[chosen.status].family}
              dot={ORDER_STATUS[chosen.status].dot}
            >
              {ORDER_STATUS[chosen.status].label}
            </Badge>
            <span className="text-12 text-ink-label">
              {`${chosen.supplier_name} ${DOT} ${chosen.location_name} ${DOT} ${money(
                Number(chosen.total),
              )}`}
            </span>
            {orders.length > 1 ? (
              <span className="ml-auto">
                <Select
                  size="sm"
                  aria-label="Orden por recibir"
                  value={chosen.id}
                  onValueChange={(value) =>
                    void navigate({
                      to: "/purchasing/receipts",
                      search: { orden: String(value) },
                    })
                  }
                  options={orders.map((one) => ({
                    value: one.id,
                    label: `Orden ${one.number} ${DOT} ${one.supplier_name}`,
                  }))}
                />
              </span>
            ) : null}
          </div>
        ) : null}

        {failure ? (
          <div className="mb-4">
            <RegionError
              title="No pudimos confirmar la recepción."
              detail={
                failure.line
                  ? `Línea ${failure.line}: ${failure.message}`
                  : failure.message
              }
              requestId={failure.requestId}
            />
          </div>
        ) : null}

        <DataTable<GoodsReceiptLineRow>
          rows={lines}
          rowId={(line) => line.id}
          density="standard"
          minWidth={1040}
          loading={receipt.isPending && !!receiptId}
          skeletonRows={8}
          skeletonWidths={["60%", "35%", "45%", "50%", "40%", "45%", "60%"]}
          containerProps={keys.containerProps}
          rowProps={(_line, index) => ({
            id: `receipt-line-${index}`,
            cursor: keys.cursor === index,
          })}
          columns={[
            {
              key: "item",
              label: "Producto",
              width: "24%",
              truncate: true,
              sticky: true,
              className: "sticky left-0 bg-inherit",
              render: (line) => (
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
              key: "ordered",
              label: "Pedido",
              width: "9%",
              align: "right" as const,
              numeric: true,
              render: (line) =>
                line.ordered_quantity === null ||
                line.ordered_quantity === undefined
                  ? "—"
                  : formatCount(line.ordered_quantity),
            },
            {
              key: "received",
              label: "Recibido",
              width: "11%",
              align: "right" as const,
              numeric: true,
              render: (line) =>
                confirmed ? (
                  formatCount(line.quantity)
                ) : (
                  <QuantityStepper
                    value={draftFor(line).quantity}
                    label={`Cantidad recibida de ${line.item_name}`}
                    onCommit={(next) => setDraft(line, { quantity: next })}
                  />
                ),
            },
            {
              key: "lot",
              label: "Lote",
              width: "12%",
              render: (line) =>
                !line.tracks_lots ? (
                  <span className="text-ink-soft" title="sin lote">
                    —
                  </span>
                ) : confirmed ? (
                  line.lot_code
                ) : (
                  <Input
                    value={draftFor(line).lotCode}
                    aria-label={`Lote de ${line.item_name}`}
                    invalid={!draftFor(line).lotCode}
                    onChange={(event) =>
                      setDraft(line, { lotCode: event.currentTarget.value })
                    }
                  />
                ),
            },
            {
              key: "expiry",
              label: "Vence",
              width: "10%",
              render: (line) =>
                !line.tracks_expiry ? (
                  <span className="text-ink-soft" title="sin vencimiento">
                    —
                  </span>
                ) : confirmed ? (
                  line.expires_at ? (
                    monthYear(line.expires_at)
                  ) : (
                    "—"
                  )
                ) : (
                  <Input
                    value={draftFor(line).expiry}
                    placeholder="MM/AAAA"
                    aria-label={`Vencimiento de ${line.item_name}`}
                    invalid={!monthToDate(draftFor(line).expiry)}
                    onChange={(event) =>
                      setDraft(line, { expiry: event.currentTarget.value })
                    }
                  />
                ),
            },
            {
              key: "cost",
              label: "Costo unitario",
              width: "12%",
              align: "right" as const,
              numeric: true,
              render: (line) =>
                line.unit_cost === null || line.unit_cost === undefined ? (
                  <span className="text-ink-soft" title="sin costo registrado">
                    —
                  </span>
                ) : (
                  money(Number(line.unit_cost))
                ),
            },
            {
              key: "state",
              label: "Estado",
              width: "22%",
              render: (line) => {
                const ordered = line.ordered_quantity ?? 0;
                const received = confirmed
                  ? line.quantity
                  : draftFor(line).quantity;
                if (ordered && received > ordered)
                  // Flagged on the informative family, never refused.
                  return (
                    <Badge family="info">
                      {`Recibido de más ${DOT} ${formatCount(received - ordered)}`}
                    </Badge>
                  );
                if (ordered && received < ordered)
                  return (
                    <Badge family="warning">
                      {`Falta ${formatCount(ordered - received)}`}
                    </Badge>
                  );
                return (
                  <Badge
                    family={confirmed ? "positive" : "neutral"}
                    dot={confirmed ? "solid" : "hollow"}
                  >
                    {confirmed ? "Recibido" : "Completo"}
                  </Badge>
                );
              },
            },
          ]}
          empty={
            <EmptyState
              kind="deliberate"
              title="Abra la recepción de esta orden"
              body="La recepción se abre con una línea por cada referencia que todavía no ha llegado."
            />
          }
        />

        {order.data ? (
          <p className="mt-3 text-11 text-ink-label">
            {`Orden ${order.data.order.number} ${DOT} ${formatCount(
              order.data.row_count,
            )} referencias ${DOT} ${money(Number(order.data.order.total))}`}
          </p>
        ) : null}
      </TableContent>
    </>
  );
}

/**
 * `MM/AAAA` back to the `YYYY-MM-01` the server takes (§A.11).
 *
 * A lot's date is a month, not a day: the carton prints `03/2027` and asking a
 * person to invent a day of the month is asking them to type something the box
 * does not say.
 */
function monthToDate(value: string): string | null {
  const match = /^(\d{2})\/(\d{4})$/.exec(value.trim());
  if (!match) return null;
  const month = Number(match[1]);
  if (month < 1 || month > 12) return null;
  return `${match[2]}-${match[1]}-01`;
}
