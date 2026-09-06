import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { APP_VERSION } from "@/app-version";
import type { DeviceRecord } from "@/sync/device";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

/**
 * S8's typed client. Every shape here is generated from `/api/openapi.json`, so
 * a response that moved under a consumer is a type error at `make check` rather
 * than a blank cell at a pilot.
 *
 * **Two kinds of call, and they behave differently on purpose.** `ask` is made
 * from the counter and is the only one that may fail without anybody being
 * told: it fetches the recommendation's prose, the card already has a local
 * version of it, and §B.10.3 is binding that no error at the counter obstructs
 * a sale. Everything else is an office read on an online-only surface and
 * renders a region-scope error like every other office surface does.
 */

export type AssistantSettings = components["schemas"]["AssistantSettingsOut"];
export type AssistantMetrics = components["schemas"]["AssistantMetricsOut"];
export type ItemWarning = components["schemas"]["ItemWarningOut"];
export type CrossSellRule = components["schemas"]["CrossSellRuleOut"];
export type AssistantAsk = components["schemas"]["AssistantAskOut"];
export type AssistantQueryRow = components["schemas"]["AssistantQueryRowOut"];
export type WarningType = ItemWarning["type"];
export type WarningSeverity = ItemWarning["severity"];
export type ConfidenceBand = CrossSellRule["confidence_band"];

/**
 * Ask, from the counter.
 *
 * `fetch` rather than the generated client, because this is the one call in the
 * product that must carry the device key **and** must be allowed to fail
 * silently: it is awaited by nothing else on the surface, and a rejection here
 * resolves to the local recommendation with the `MODO LOCAL` eyebrow.
 */
export async function ask(
  device: DeviceRecord,
  body: Record<string, unknown>,
  timeoutMs: number,
): Promise<AssistantAsk | null> {
  const abort = new AbortController();
  const timer = window.setTimeout(() => abort.abort(), timeoutMs);
  try {
    const response = await fetch("/api/assistant/queries", {
      method: "POST",
      credentials: "same-origin",
      signal: abort.signal,
      headers: {
        "Content-Type": "application/json",
        "X-Botica-Device-Key": device.key,
        "X-Botica-App-Version": APP_VERSION,
        ...csrf(),
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) return null;
    return (await response.json()) as AssistantAsk;
  } catch {
    // Offline, refused, timed out or answered in a shape we do not read — one
    // treatment for all four, and it is the local fallback.
    return null;
  } finally {
    window.clearTimeout(timer);
  }
}

function csrf(): Record<string, string> {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
  return match?.[1] ? { "X-CSRFToken": decodeURIComponent(match[1]) } : {};
}

// ---------------------------------------------------------------------------
// The office surfaces
// ---------------------------------------------------------------------------

export function useAssistantSettings() {
  return useQuery({
    queryKey: ["assistant-settings"],
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/settings/assistant",
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar los ajustes del asistente.",
        );
      return data;
    },
  });
}

export function useSaveAssistantSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (values: Partial<AssistantSettings>) => {
      const { data, error, response } = await api.PATCH(
        "/api/settings/assistant",
        { body: values },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar los ajustes.");
      return data;
    },
    onSuccess: (data) => {
      client.setQueryData(["assistant-settings"], data);
      void client.invalidateQueries({ queryKey: ["assistant-metrics"] });
    },
  });
}

export function useAssistantMetrics(params: {
  days: number;
  location_id?: string;
}) {
  return useQuery({
    queryKey: ["assistant-metrics", params],
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/assistant/metrics",
        { params: { query: params } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las cifras.");
      return data;
    },
  });
}

export interface WarningQuery {
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
  item_id?: string;
  type?: WarningType;
  severity?: WarningSeverity;
  active?: boolean;
}

export function useItemWarnings(params: WarningQuery) {
  return useQuery({
    queryKey: ["item-warnings", params],
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/item-warnings", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar las advertencias de producto.",
        );
      return data;
    },
  });
}

export function useSaveWarning() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      values,
    }: {
      id: string;
      values: {
        text?: string;
        severity?: WarningSeverity;
        triggers?: Record<string, unknown>[];
        active?: boolean;
      };
    }) => {
      const { data, error, response } = await api.PATCH(
        "/api/item-warnings/{warning_id}",
        { params: { path: { warning_id: id } }, body: values },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar la advertencia.");
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["item-warnings"] });
    },
  });
}

export function useDeactivateWarning() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.DELETE(
        "/api/item-warnings/{warning_id}",
        { params: { path: { warning_id: id } } },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos desactivar la advertencia.",
        );
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["item-warnings"] });
    },
  });
}

export function useCrossSellRules(params: {
  page: number;
  page_size: number;
  order: "asc" | "desc";
  sort?: string;
  location_id?: string;
  network?: boolean;
}) {
  return useQuery({
    queryKey: ["cross-sell-rules", params],
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/cross-sell-rules", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las reglas.");
      return data;
    },
  });
}

export function useRefreshRules() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST(
        "/api/cross-sell-rules/refresh",
        { params: { query: {} } },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos poner el cálculo en cola.",
        );
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["cross-sell-rules"] });
    },
  });
}

export function useAssistantQueries(params: {
  page: number;
  page_size: number;
  order: "asc" | "desc";
  days: number;
  location_id?: string;
  mode?: "model" | "local";
  passed?: boolean;
}) {
  return useQuery({
    queryKey: ["assistant-queries", params],
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/assistant/queries",
        { params: { query: params } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el registro.");
      return data;
    },
  });
}
