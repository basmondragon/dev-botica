import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

/**
 * The office's four sync reads and four writes.
 *
 * **These are server-authoritative and online-only, and they say so.** An
 * office browser never replicates (A4), so with no network they render
 * §B.10.3's route-scope error rather than a stale table: showing an owner
 * yesterday's device list without saying so is exactly the
 * confident-figure-that-is-wrong failure §5 rule 1 exists to prevent.
 */

export type Device = components["schemas"]["DeviceOut"];
export type SyncConflict = components["schemas"]["ConflictOut"];
export type SyncSettings = components["schemas"]["SyncSettingsOut"];

export interface DeviceQuery {
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
  location_id?: string;
  status?: "active" | "revoked";
}

export function devicesQuery(params: DeviceQuery) {
  return queryOptions({
    queryKey: ["devices", params],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/devices", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los equipos.");
      return data;
    },
  });
}

export function useDevices(params: DeviceQuery, enabled = true) {
  return useQuery({ ...devicesQuery(params), enabled });
}

export function useSyncConflicts(
  params: {
    page: number;
    page_size: number;
    device_id?: string;
    status?: "open";
  },
  enabled = true,
) {
  return useQuery({
    queryKey: ["sync-conflicts", params],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/sync/conflicts", {
        params: { query: { ...params, order: "desc" } },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los conflictos.");
      return data;
    },
  });
}

function invalidateDevices(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: ["devices"] });
  void client.invalidateQueries({ queryKey: ["sync-conflicts"] });
}

export function useUpdateDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: string;
      label?: string;
      location_id?: string;
    }) => {
      const { id, ...body } = input;
      const { data, error, response } = await api.PATCH(
        "/api/devices/{device_id}",
        { params: { path: { device_id: id } }, body },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar este equipo.");
      return data;
    },
    onSuccess: () => invalidateDevices(client),
  });
}

export function useRevokeDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.POST(
        "/api/devices/{device_id}/revoke",
        { params: { path: { device_id: id } } },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos dar de baja este equipo.",
        );
      return data;
    },
    onSuccess: () => invalidateDevices(client),
  });
}

/**
 * Scope 2 · **an `owner` or `admin` may claim the browser they are sitting at,
 * explicitly, from the device list — but is never asked to** (A4).
 *
 * The claim writes a device record into *this* browser's local storage, so the
 * cashier who signs in next finds the till already enrolled. It does **not**
 * start replicating for the office identity that made it: the provider is
 * mounted for a till and for nobody else.
 */
export function useClaimDevice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      label: string;
      location_id: string;
      persisted: boolean | null;
    }) => {
      const { data, error, response } = await api.POST("/api/devices/claim", {
        body: { label: input.label, location_id: input.location_id },
        headers:
          input.persisted === null
            ? undefined
            : { "X-Botica-Storage-Persisted": String(input.persisted) },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos registrar este equipo.");
      return data;
    },
    onSuccess: () => invalidateDevices(client),
  });
}

export function useSyncSettings(enabled = true) {
  return useQuery({
    queryKey: ["settings", "sync"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/settings/sync");
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar los ajustes de sincronización.",
        );
      return data;
    },
  });
}

export function useSaveSyncSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: SyncSettings) => {
      const { data, error, response } = await api.PATCH("/api/settings/sync", {
        body,
      });
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos guardar los ajustes de sincronización.",
        );
      return data;
    },
    onSuccess: (data) => {
      client.setQueryData(["settings", "sync"], data);
    },
  });
}
