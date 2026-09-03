import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

/**
 * S4's typed client, for the **office half only**.
 *
 * The counter calls none of it: it reads the same rows from its own local store
 * at zero latency, which is the whole of §4's two-read-models boundary and the
 * reason none of §4's counter budgets contains a request. Every shape here is
 * generated from `/api/openapi.json`, so a response that moved under a consumer
 * is a type error at `make check` rather than a blank cell at a pilot.
 */

export type SaleRow = components["schemas"]["SaleRow"];
export type SaleDetail = components["schemas"]["SaleDetail"];
export type ReturnRow = components["schemas"]["ReturnRow"];
export type ReturnDetail = components["schemas"]["ReturnDetail"];
export type ShiftRow = components["schemas"]["ShiftRow"];
export type ShiftDetail = components["schemas"]["ShiftDetail"];
export type SaleStatus = SaleRow["status"];
export type PaymentMethod = ReturnRow["refund_method"];

export interface SaleQuery {
  location_id?: string[];
  shift_id?: string;
  status?: SaleStatus;
  source?: "counter" | "imported";
  since?: string;
  until?: string;
  q?: string;
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

export function useSales(params: SaleQuery, enabled = true) {
  return useQuery({
    queryKey: ["sales", params],
    enabled,
    // §B.10.1 · a re-fetch keeps the previous rows and dims them; blanking a
    // populated table on every keystroke is worse than the wait.
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/sales", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las ventas.");
      return data;
    },
  });
}

export function useSale(saleId: string | null) {
  return useQuery({
    queryKey: ["sale", saleId],
    enabled: !!saleId,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/sales/{sale_id}", {
        params: { path: { sale_id: saleId! } },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la venta.");
      return data;
    },
  });
}

export interface ReturnQuery {
  location_id?: string[];
  sale_id?: string;
  since?: string;
  until?: string;
  q?: string;
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

export function useReturns(params: ReturnQuery, enabled = true) {
  return useQuery({
    queryKey: ["sale-returns", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/sale-returns", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar las devoluciones.",
        );
      return data;
    },
  });
}

export function useReturn(returnId: string | null) {
  return useQuery({
    queryKey: ["sale-return", returnId],
    enabled: !!returnId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/sale-returns/{return_id}",
        { params: { path: { return_id: returnId! } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la devolución.");
      return data;
    },
  });
}

export interface ShiftQuery {
  location_id?: string[];
  device_id?: string;
  status?: "open" | "closed";
  since?: string;
  until?: string;
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

export function useShifts(params: ShiftQuery, enabled = true) {
  return useQuery({
    queryKey: ["shifts", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/shifts", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los turnos.");
      return data;
    },
  });
}

export function useShift(shiftId: string | null) {
  return useQuery({
    queryKey: ["shift", shiftId],
    enabled: !!shiftId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/shifts/{shift_id}",
        {
          params: { path: { shift_id: shiftId! } },
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el turno.");
      return data;
    },
  });
}

/** Voiding a sale already on the server. **The cashier's own same-shift void is
 *  a client write and does not call this.** */
export function useVoidSale() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { saleId: string; reason: string }) => {
      const { data, error, response } = await api.POST(
        "/api/sales/{sale_id}/void",
        {
          params: { path: { sale_id: input.saleId } },
          body: { reason: input.reason },
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos anular la venta.");
      return data;
    },
    onSuccess: (sale) => {
      void client.invalidateQueries({ queryKey: ["sales"] });
      void client.invalidateQueries({ queryKey: ["sale", sale.id] });
      void client.invalidateQueries({ queryKey: ["nav-counters"] });
    },
  });
}

/** A forced close **leaves `declared_total` and `variance` null** and is never
 *  rendered as a count. */
export function useForceCloseShift() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: { shiftId: string; reason: string }) => {
      const { data, error, response } = await api.POST(
        "/api/shifts/{shift_id}/force-close",
        {
          params: { path: { shift_id: input.shiftId } },
          body: { reason: input.reason },
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cerrar el turno.");
      return data;
    },
    onSuccess: (shift) => {
      void client.invalidateQueries({ queryKey: ["shifts"] });
      void client.invalidateQueries({ queryKey: ["shift", shift.id] });
    },
  });
}
