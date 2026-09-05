import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components, paths } from "./schema.gen";

/**
 * S7's typed client. Every shape here is generated from `/api/openapi.json`, so
 * a response that moved under a consumer is a type error at `make check` rather
 * than a blank cell at a pilot.
 *
 * **Precios is an online-only office surface** (§4, A4). Nothing here reads a
 * local store, nothing is queued, and there is no offline write path -- because
 * there is no write path at all. The one mutation in this module enqueues a
 * run, and its whole consequence is that a screen changes.
 *
 * **There is no approve, no apply, no revert, no dismiss and no batch.** A
 * suggestion becomes a price in S1's editor, which this screen navigates to.
 */

export type PricingRow = components["schemas"]["PricingRow"];
export type PricingDetail = components["schemas"]["PricingDetail"];
export type PricingProposal = components["schemas"]["PricingProposalOut"];
export type PricingEstimate = components["schemas"]["PricingEstimateOut"];
export type PricingSummary = components["schemas"]["PricingSummaryOut"];
export type PricingAdoption = components["schemas"]["PricingAdoptionOut"];
export type PricingCap = components["schemas"]["PricingCapOut"];
export type PricingSettings = components["schemas"]["PricingSettingsOut"];
export type ProposalBasis = PricingProposal["basis"];
export type ConfidenceBand = PricingProposal["confidence"];
export type RowState = PricingRow["state"];
/** The `Estado` chip's vocabulary: every rendered state **plus `live`**, the
 *  screen's default view. `live` is a filter value and never a row's state, so
 *  it is not on `PricingRow` -- the two are read off the endpoint that uses
 *  each. */
export type RowStateFilter = NonNullable<
  NonNullable<
    paths["/api/pricing/items"]["get"]["parameters"]["query"]
  >["state"]
>;
export type CapStatus = PricingRow["cap_status"];

export interface PricingQuery {
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
  q?: string;
  manufacturer_id?: string;
  category_id?: string;
  state?: RowStateFilter;
  basis?: ProposalBasis;
  confidence?: ConfidenceBand;
}

export function usePricingItems(params: PricingQuery) {
  return useQuery({
    queryKey: ["pricing-items", params],
    // §B.10.1 · a re-fetch keeps the previous rows and dims them; blanking a
    // populated table on every filter change is worse than the wait.
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/pricing/items", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las propuestas.");
      return data;
    },
  });
}

export function usePricingItem(itemId: string | undefined) {
  return useQuery({
    queryKey: ["pricing-item", itemId],
    enabled: !!itemId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/pricing/items/{item_id}",
        { params: { path: { item_id: itemId! } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la referencia.");
      return data;
    },
  });
}

export function usePricingSummary() {
  return useQuery({
    queryKey: ["pricing-summary"],
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/pricing/summary");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el resumen.");
      return data;
    },
  });
}

export function usePricingAdoption(days = 90, enabled = true) {
  return useQuery({
    queryKey: ["pricing-adoption", days],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/pricing/adoption", {
        params: { query: { days } },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la adopción.");
      return data;
    },
  });
}

export function usePricingFilters() {
  return useQuery({
    queryKey: ["pricing-filters"],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/pricing/filters");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los filtros.");
      return data as {
        manufacturers: { id: string; name: string }[];
        categories: { id: string; name: string }[];
      };
    },
  });
}

/**
 * **The most consequential write this stage exposes, and its whole consequence
 * is that a screen changes.** `owner` only; an `admin` is not rendered the
 * button at all rather than shown one that refuses.
 */
export function useRecalculatePricing() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/api/pricing/runs");
      if (error || !data)
        throw toApiError(response, error, "No pudimos pedir el recálculo.");
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["pricing-items"] });
      void client.invalidateQueries({ queryKey: ["pricing-summary"] });
    },
  });
}

export function usePricingCaps(enabled = true) {
  return useQuery({
    queryKey: ["pricing-caps"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/pricing/caps");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los topes.");
      return data;
    },
  });
}

export function useSetCap() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      itemId: string;
      cap_status: CapStatus;
      regulated_max_price?: string | null;
      source?: string;
    }) => {
      const { data, error, response } = await api.PUT(
        "/api/pricing/caps/{item_id}",
        {
          params: { path: { item_id: input.itemId } },
          body: {
            cap_status: input.cap_status,
            regulated_max_price: input.regulated_max_price ?? null,
            source: input.source ?? "",
          },
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el tope.");
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["pricing-caps"] });
      void client.invalidateQueries({ queryKey: ["pricing-items"] });
    },
  });
}

export function useImportCaps() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (csv: string) => {
      const { data, error, response } = await api.POST(
        "/api/pricing/caps/import",
        { body: { csv } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el archivo.");
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["pricing-caps"] });
      void client.invalidateQueries({ queryKey: ["pricing-items"] });
    },
  });
}

export function usePricingSettings(enabled = true) {
  return useQuery({
    queryKey: ["settings", "pricing"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/settings/pricing");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los ajustes.");
      return data;
    },
  });
}

export function useSavePricingSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (
      body: Partial<PricingSettings> & { clear_margin_goal?: boolean },
    ) => {
      const { data, error, response } = await api.PATCH(
        "/api/settings/pricing",
        { body: { clear_margin_goal: false, ...body } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar los ajustes.");
      return data;
    },
    onSuccess: (data) => {
      client.setQueryData(["settings", "pricing"], data);
      void client.invalidateQueries({ queryKey: ["pricing-items"] });
      void client.invalidateQueries({ queryKey: ["pricing-summary"] });
    },
  });
}
