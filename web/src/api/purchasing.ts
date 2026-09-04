import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

/**
 * S6's typed client. Every shape here is generated from `/api/openapi.json`, so
 * a response that moved under a consumer is a type error at `make check` rather
 * than a blank cell at a pilot.
 *
 * **Every surface in this stage is online-only** (§4, A4). Nothing here reads a
 * local store, nothing here is queued, and there is no offline write path: an
 * approval sent into a void that lands twenty minutes later against a supplier
 * who has already shipped is worse than an approval that did not happen.
 */

export type PurchaseOrderRow = components["schemas"]["OrderRow"];
export type PurchaseOrderDetail = components["schemas"]["OrderDetail"];
export type PurchaseOrderLineRow = components["schemas"]["LineRow"];
export type OrderKpi = components["schemas"]["KpiOut"];
export type OrderProvenance = components["schemas"]["ProvenanceOut"];
export type GoodsReceiptRow = components["schemas"]["GoodsReceiptRow"];
export type GoodsReceiptDetail = components["schemas"]["GoodsReceiptDetail"];
export type GoodsReceiptLineRow = components["schemas"]["GoodsReceiptLineRow"];
export type PurchasingSettings = components["schemas"]["PurchasingSettingsOut"];
export type PurchaseOrderStatus = PurchaseOrderRow["status"];
export type ForecastBasis = NonNullable<PurchaseOrderLineRow["basis"]>;
export type ConfidenceBand = NonNullable<PurchaseOrderLineRow["band"]>;

export interface OrderListQuery {
  status?: PurchaseOrderStatus[];
  supplier_id?: string;
  location_id?: string[];
  source?: "model" | "manual";
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

export function usePurchaseOrders(params: OrderListQuery, enabled = true) {
  return useQuery({
    queryKey: ["purchase-orders", params],
    enabled,
    // §B.10.1 · a re-fetch keeps the previous rows and dims them; blanking a
    // populated table on every filter change is worse than the wait.
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/purchase-orders", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las órdenes.");
      return data;
    },
  });
}

export interface OrderDetailQuery {
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
  basis?: ForecastBasis[];
  band?: ConfidenceBand[];
  category_id?: string;
}

export function usePurchaseOrder(
  orderId: string | undefined,
  params: OrderDetailQuery,
) {
  return useQuery({
    queryKey: ["purchase-order", orderId, params],
    enabled: !!orderId,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/purchase-orders/{order_id}",
        { params: { path: { order_id: orderId! }, query: params } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la orden.");
      return data;
    },
  });
}

/**
 * **The edit that writes `approved_quantity` and never `suggested_quantity`.**
 *
 * Optimistic with no loading state (§B.10.1): the footer total and the four
 * tiles recompute the instant a figure is typed, and a failed write reverts the
 * cell. A stepper that waited for a round trip would make editing eleven
 * quantities eleven waits.
 */
export function useSetLineQuantity(orderId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { lineId: string; quantity: number }) => {
      const { data, error, response } = await api.PATCH(
        "/api/purchase-orders/{order_id}/lines/{line_id}",
        {
          params: { path: { order_id: orderId, line_id: input.lineId } },
          body: { approved_quantity: input.quantity },
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar la cantidad.");
      return data;
    },
    // §B.10.1 · **an optimistic write shows no loading state at all.** The cell,
    // the footer total and the four tiles move the instant a figure is typed;
    // a stepper that dimmed the table for a round trip would make editing
    // eleven quantities eleven waits. The revert path is the snapshot below.
    onMutate: async (input) => {
      await client.cancelQueries({ queryKey: ["purchase-order", orderId] });
      const held = client.getQueriesData({
        queryKey: ["purchase-order", orderId],
      });
      for (const [key, previous] of held) {
        const page = previous as PurchaseOrderDetail | undefined;
        if (!page) continue;
        client.setQueryData(key, applyQuantity(page, input));
      }
      return { held };
    },
    onError: (_error, _input, context) => {
      for (const [key, previous] of context?.held ?? [])
        client.setQueryData(key, previous);
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["purchase-order", orderId] });
      void client.invalidateQueries({ queryKey: ["purchase-orders"] });
    },
  });
}

/**
 * The order as it will read once the write lands: the line's approved quantity,
 * the footer total, and the two tiles that count and value the proposal.
 *
 * **It never touches `suggested_quantity`.** The optimistic copy is the screen's
 * own prediction of the server's answer, and the server does not move that
 * column either.
 */
function applyQuantity(
  page: PurchaseOrderDetail,
  input: { lineId: string; quantity: number },
): PurchaseOrderDetail {
  const lines = page.lines.map((line) =>
    line.id === input.lineId
      ? { ...line, approved_quantity: input.quantity }
      : line,
  );
  const moved = page.lines.find((line) => line.id === input.lineId);
  if (!moved) return page;
  const cost = Number(moved.unit_cost ?? 0);
  const delta = (input.quantity - moved.approved_quantity) * cost;
  const suggested =
    page.suggested_reference_count +
    (input.quantity > 0 ? 1 : 0) -
    (moved.approved_quantity > 0 ? 1 : 0);
  return {
    ...page,
    lines,
    suggested_reference_count: suggested,
    order: { ...page.order, total: String(Number(page.order.total) + delta) },
    kpis: page.kpis.map((tile) =>
      tile.key === "suggested_references"
        ? { ...tile, figure: String(suggested) }
        : tile.key === "order_value" && tile.figure !== null
          ? { ...tile, figure: String(Number(tile.figure) + delta) }
          : tile,
    ),
  };
}

function orderAction(path: "approve" | "discard" | "mark-sent") {
  return async (orderId: string) => {
    const { data, error, response } = await api.POST(
      `/api/purchase-orders/{order_id}/${path}` as "/api/purchase-orders/{order_id}/approve",
      { params: { path: { order_id: orderId } } },
    );
    if (error || !data)
      throw toApiError(response, error, "No pudimos actualizar la orden.");
    return data;
  };
}

export function useOrderAction(action: "approve" | "discard" | "mark-sent") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: orderAction(action),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["purchase-order"] });
      void client.invalidateQueries({ queryKey: ["purchase-orders"] });
      void client.invalidateQueries({ queryKey: ["nav-counters"] });
    },
  });
}

export function useGenerateOrders() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (locationId?: string) => {
      const { data, error, response } = await api.POST(
        "/api/purchase-orders/generate",
        { body: { location_id: locationId ?? null } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos generar las órdenes.");
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["purchase-orders"] });
      void client.invalidateQueries({ queryKey: ["nav-counters"] });
    },
  });
}

export function useGoodsReceipts(
  params: {
    purchase_order_id?: string;
    location_id?: string[];
    type?: "receipt" | "supplier_return";
    status?: "draft" | "confirmed";
    page: number;
    page_size: number;
  },
  enabled = true,
) {
  return useQuery({
    queryKey: ["goods-receipts", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/goods-receipts", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las recepciones.");
      return data;
    },
  });
}

export function useGoodsReceipt(receiptId: string | undefined) {
  return useQuery({
    queryKey: ["goods-receipt", receiptId],
    enabled: !!receiptId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/goods-receipts/{receipt_id}",
        { params: { path: { receipt_id: receiptId! } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la recepción.");
      return data;
    },
  });
}

export function useOpenReceipt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (orderId: string) => {
      const { data, error, response } = await api.POST("/api/goods-receipts", {
        body: { purchase_order_id: orderId, type: "receipt" },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos abrir la recepción.");
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["goods-receipts"] });
    },
  });
}

export type ReceiptLineInput = components["schemas"]["GoodsReceiptLineIn"];

/**
 * **The atomic act.** One call creates or attaches the lots, moves the stock
 * through S3's ledger, writes back the cost actually paid and the lead time
 * actually observed, and settles the order -- or does none of it.
 */
export function useConfirmReceipt(receiptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (lines: ReceiptLineInput[]) => {
      // The typed draft is saved first and confirmed second, in that order and
      // in two calls: the confirmation is the atomic act and it acts on what the
      // server holds, so a screen that confirmed before saving would move the
      // stock somebody typed a minute ago.
      const saved = await api.PATCH("/api/goods-receipts/{receipt_id}", {
        params: { path: { receipt_id: receiptId } },
        body: { lines },
      });
      if (!saved.data)
        throw toApiError(
          saved.response,
          saved.error,
          "No pudimos guardar la recepción.",
        );
      const { data, error, response } = await api.POST(
        "/api/goods-receipts/{receipt_id}/confirm",
        { params: { path: { receipt_id: receiptId } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos confirmar la recepción.");
      return data;
    },
    onSuccess: (data) => {
      client.setQueryData(["goods-receipt", receiptId], data);
      void client.invalidateQueries({ queryKey: ["purchase-order"] });
      void client.invalidateQueries({ queryKey: ["purchase-orders"] });
      void client.invalidateQueries({ queryKey: ["stock"] });
    },
  });
}

export function usePurchasingSettings(enabled = true) {
  return useQuery({
    queryKey: ["settings", "purchasing"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/settings/purchasing",
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar los ajustes de compras.",
        );
      return data;
    },
  });
}

export function useSavePurchasingSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (values: Partial<PurchasingSettings>) => {
      const { data, error, response } = await api.PATCH(
        "/api/settings/purchasing",
        { body: values },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar los ajustes.");
      return data;
    },
    onSuccess: (data) => {
      client.setQueryData(["settings", "purchasing"], data);
    },
  });
}

/**
 * The current forecast for one reference at one sede, which is what the record
 * panel puts under the stamped four: usable weeks after censoring, the
 * coefficient of variation, the imported share of the window.
 *
 * **One row per item per sede and no history** -- what the model said on a given
 * day survives on `purchase_order_lines`, stamped at generation, and that is
 * what the panel shows above this.
 */
export function useDemandForecast(
  locationId: string | undefined,
  itemId: string | undefined,
) {
  return useQuery({
    queryKey: ["demand-forecast", locationId, itemId],
    enabled: !!locationId && !!itemId,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/demand-forecasts", {
        params: {
          query: {
            location_id: locationId!,
            item_id: itemId!,
            page: 1,
            page_size: 25,
          },
        },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el pronóstico.");
      return data;
    },
  });
}

/**
 * One sede's whole forecast, filtered by regime.
 *
 * The Orden sugerida screen reads it for exactly one thing, and it is the
 * distinction between two empty states that look identical and mean opposite
 * things: **a sede the model has measured and found nothing to buy at** is not
 * the same as **a sede the model has nothing to propose from**. The first is
 * the product working; the second is the withholding §1 requires, and it points
 * at Existencias rather than at a `Generar ahora` that would change nothing.
 */
export function useDemandForecasts(
  locationId: string | undefined,
  basis: ForecastBasis[],
  enabled = true,
) {
  return useQuery({
    queryKey: ["demand-forecasts", locationId, basis],
    enabled: enabled && !!locationId,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/demand-forecasts", {
        params: {
          query: { location_id: locationId!, basis, page: 1, page_size: 25 },
        },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el pronóstico.");
      return data;
    },
  });
}
