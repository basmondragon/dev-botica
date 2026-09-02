import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

/**
 * S3's typed client. Every shape here is generated from `/api/openapi.json`, so
 * a response that moved under a consumer is a type error at `make check` rather
 * than a blank cell at a pilot.
 */

export type StockRow = components["schemas"]["StockRow"];
export type StockPage = components["schemas"]["StockPage"];
export type StockSummary = components["schemas"]["StockSummaryOut"];
export type ItemAvailability = components["schemas"]["ItemAvailabilityOut"];
export type Expiring = components["schemas"]["ExpiringOut"];
export type MoveRow = components["schemas"]["MoveOut"];
export type LotRow = components["schemas"]["LotOut"];
export type Trace = components["schemas"]["TraceOut"];
export type TransferRow = components["schemas"]["TransferOut"];
export type TransferLineRow = components["schemas"]["TransferLineOut"];
export type CountRow = components["schemas"]["CountOut"];
export type CountPage = components["schemas"]["CountPage"];
export type CountLineRow = components["schemas"]["CountLineOut"];
export type CountDue = components["schemas"]["CountDueOut"];
export type PolicyRow = components["schemas"]["PolicyOut"];
export type InventorySettings = components["schemas"]["InventorySettingsOut"];
export type StockState = StockRow["state"];
export type ExpiryFilter = NonNullable<
  Parameters<typeof useStock>[0]["expiry"]
>;

export interface StockQuery {
  q?: string;
  location_id?: string[];
  category_id?: string;
  state?: StockState;
  action_required?: boolean;
  expiry?: "expired" | "valuation" | "alert" | "notice" | "none";
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

export function useStock(params: StockQuery, enabled = true) {
  return useQuery({
    queryKey: ["stock", params],
    enabled,
    // §B.10.1 · a re-fetch keeps the previous rows and dims them; blanking a
    // populated table on every keystroke is worse than the wait.
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/stock", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las existencias.");
      return data;
    },
  });
}

export function useStockSummary(
  params: Omit<StockQuery, "page" | "page_size" | "order" | "state">,
  enabled = true,
) {
  return useQuery({
    queryKey: ["stock-summary", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/stock/summary", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el resumen.");
      return data;
    },
  });
}

export function useAvailability(itemId: string | undefined) {
  return useQuery({
    queryKey: ["stock-availability", itemId],
    enabled: !!itemId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/stock/availability",
        { params: { query: { item_id: itemId! } } },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar las existencias en otras sedes.",
        );
      return data;
    },
  });
}

export function useMoves(
  params: {
    location_id?: string[];
    item_id?: string;
    lot_id?: string;
    page: number;
    page_size: number;
  },
  enabled = true,
) {
  return useQuery({
    queryKey: ["stock-moves", params],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/stock-moves", {
        params: { query: { ...params, order: "desc" as const } },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los movimientos.");
      return data;
    },
  });
}

export function useLot(lotId: string | null | undefined) {
  return useQuery({
    queryKey: ["lot", lotId],
    enabled: !!lotId,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/lots/{lot_id}", {
        params: { path: { lot_id: lotId! } },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el lote.");
      return data;
    },
  });
}

export function useTrace(lotId: string | null | undefined) {
  return useQuery({
    queryKey: ["lot-trace", lotId],
    enabled: !!lotId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/lots/{lot_id}/trace",
        { params: { path: { lot_id: lotId! } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la trazabilidad.");
      return data;
    },
  });
}

export function useTransfers(
  params: { status?: TransferRow["status"]; page: number; page_size: number },
  enabled = true,
) {
  return useQuery({
    queryKey: ["transfers", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/transfers", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los traslados.");
      return data;
    },
  });
}

export function useCounts(
  params: { status?: CountRow["status"]; page: number; page_size: number },
  enabled = true,
) {
  return useQuery({
    queryKey: ["stock-counts", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/stock-counts", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los conteos.");
      return data;
    },
  });
}

export function useInventorySettings(enabled = true) {
  return useQuery({
    queryKey: ["settings", "inventory"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/settings/inventory",
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar los ajustes de inventario.",
        );
      return data;
    },
  });
}

/**
 * Everything a write moves. **Stated once**, because a mutation that invalidated
 * the grid and forgot the summary would leave the footer's `312 requieren
 * acción` disagreeing with the rows above it -- and that annotation is the one
 * figure on the screen a person acts on.
 */
function invalidateStock(client: QueryClient) {
  for (const key of [
    "stock",
    "stock-summary",
    "stock-availability",
    "stock-moves",
    "stock-counts",
    "stock-counts-due",
    "transfers",
    "lot",
    "lot-trace",
    "stock-policies",
  ]) {
    void client.invalidateQueries({ queryKey: [key] });
  }
  void client.invalidateQueries({ queryKey: ["audit-log"] });
}

export function useCreateMove() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["MoveIn"]) => {
      const { data, error, response } = await api.POST("/api/stock-moves", {
        body,
      });
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos registrar el movimiento.",
        );
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

/**
 * A scan, resolved on demand rather than by a query keyed on the code.
 *
 * **A scan is an action, not a view.** Modelling it as a query means the answer
 * arrives in an effect, and an effect that appends a line is an effect that
 * appends it twice under React's strict double-invoke -- which at a counter is
 * a box received twice.
 */
export function useScan() {
  return useMutation({
    mutationFn: async (barcode: string) => {
      const { data, error, response } = await api.GET("/api/items", {
        params: {
          query: {
            barcode,
            active: "true" as const,
            page: 1,
            // The grid contract fixes the three page sizes, and a scan is the
            // same endpoint: a `1` here is a 422 the surface would swallow as
            // "no such code", which is the wrong answer to give somebody
            // holding the box.
            page_size: 25,
            order: "asc" as const,
          },
        },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos resolver el código.");
      return data.rows[0] ?? null;
    },
  });
}

export function useCreateReceipt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["ReceiptIn"]) => {
      const { data, error, response } = await api.POST("/api/receipts", {
        body,
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos registrar la entrada.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useSavePolicies() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["PolicyWriteIn"]) => {
      const { data, error, response } = await api.PUT("/api/stock-policies", {
        body,
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar los umbrales.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useCreateTransfer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["TransferIn"]) => {
      const { data, error, response } = await api.POST("/api/transfers", {
        body,
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos crear el traslado.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useDispatchTransfer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.POST(
        "/api/transfers/{transfer_id}/dispatch",
        { params: { path: { transfer_id: id } }, body: {} },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos despachar el traslado.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useReceiveTransfer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      lines,
    }: {
      id: string;
      lines?: { line_id: string; quantity: number }[];
    }) => {
      const { data, error, response } = await api.POST(
        "/api/transfers/{transfer_id}/receive",
        {
          params: { path: { transfer_id: id } },
          body: lines ? { lines } : {},
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos recibir el traslado.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useResolveTransfer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      line_id,
      resolution,
    }: {
      id: string;
      line_id: string;
      resolution: "received_late" | "lost_in_transit";
    }) => {
      const { data, error, response } = await api.POST(
        "/api/transfers/{transfer_id}/resolve",
        {
          params: { path: { transfer_id: id } },
          body: { line_id, resolution },
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos resolver el faltante.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useCreateCount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["CountIn"]) => {
      const { data, error, response } = await api.POST("/api/stock-counts", {
        body,
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos abrir el conteo.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useEnterCountLines() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      lines,
    }: {
      id: string;
      lines: components["schemas"]["CountLineIn"][];
    }) => {
      const { data, error, response } = await api.POST(
        "/api/stock-counts/{count_id}/lines",
        { params: { path: { count_id: id } }, body: { lines } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el conteo.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}

export function useCloseCount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.POST(
        "/api/stock-counts/{count_id}/close",
        { params: { path: { count_id: id } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cerrar el conteo.");
      return data;
    },
    onSuccess: () => invalidateStock(client),
  });
}
