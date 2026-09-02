import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

export type Me = components["schemas"]["MeOut"];
export type Location = components["schemas"]["LocationOut"];
export type Invitation = components["schemas"]["InvitationOut"];
export type Person = components["schemas"]["UserOut"];
export type TenantSettings = components["schemas"]["TenantSettingsOut"];
export type AuditRow = components["schemas"]["AuditRowOut"];
export type NavCounters = components["schemas"]["NavCountersOut"];
export type Role = Me["role"];

export function meQuery() {
  return queryOptions({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/me");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar su cuenta.");
      return data;
    },
    staleTime: 30_000,
  });
}

export function useMe() {
  return useQuery(meQuery());
}

/**
 * §B.8.2 · `/api/nav-counters` polls at 30 seconds on office surfaces. A queue
 * count is not urgent, and a 10-second poll on seven counters is seven times
 * the traffic for a number nobody watches change.
 */
export function useNavCounters(enabled: boolean) {
  return useQuery({
    queryKey: ["nav-counters"],
    enabled,
    refetchInterval: 30_000,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/nav-counters");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los contadores.");
      return data;
    },
  });
}

export function useLocations(enabled = true) {
  return useQuery({
    queryKey: ["locations"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/locations");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las sedes.");
      return data;
    },
  });
}

export function useInvitations(enabled = true) {
  return useQuery({
    queryKey: ["invitations"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/invitations");
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar las invitaciones.",
        );
      return data;
    },
  });
}

export interface PageParams {
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

export function usePeople(params: PageParams, enabled = true) {
  return useQuery({
    queryKey: ["users", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/users", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las personas.");
      return data;
    },
  });
}

export function useAuditLog(params: PageParams, enabled = true) {
  return useQuery({
    queryKey: ["audit-log", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/audit-log", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar la actividad.");
      return data;
    },
  });
}

export function useTenantSettings(enabled = true) {
  return useQuery({
    queryKey: ["settings", "tenant"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/settings/tenant");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los ajustes.");
      return data;
    },
  });
}

export function useSaveTenantSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["TenantSettingsIn"]) => {
      const { data, error, response } = await api.PATCH(
        "/api/settings/tenant",
        {
          body,
        },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos guardar los datos de la droguería.",
        );
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["settings", "tenant"] });
      void client.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useInvite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["InvitationIn"]) => {
      const { data, error, response } = await api.POST("/api/invitations", {
        body,
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos enviar la invitación.");
      return data;
    },
    onSuccess: () => invalidateRoster(client),
  });
}

export function useResendInvitation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.POST(
        "/api/invitations/{invitation_id}/resend",
        { params: { path: { invitation_id: id } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos reenviar la invitación.");
      return data;
    },
    onSuccess: () => invalidateRoster(client),
  });
}

export function useInvitationLink() {
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.POST(
        "/api/invitations/{invitation_id}/link",
        { params: { path: { invitation_id: id } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos copiar el enlace.");
      return data;
    },
  });
}

export function useRevokeInvitation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.DELETE(
        "/api/invitations/{invitation_id}",
        { params: { path: { invitation_id: id } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos revocar la invitación.");
      return data;
    },
    onSuccess: () => invalidateRoster(client),
  });
}

export function useUpdatePerson() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      body,
    }: {
      id: string;
      body: components["schemas"]["UserPatchIn"];
    }) => {
      const { data, error, response } = await api.PATCH(
        "/api/users/{user_id}",
        {
          params: { path: { user_id: id } },
          body,
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar los cambios.");
      return data;
    },
    onSuccess: () => invalidateRoster(client),
  });
}

export function useDeletePerson() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE("/api/users/{user_id}", {
        params: { path: { user_id: id } },
      });
      if (error)
        throw toApiError(
          response,
          error,
          "No pudimos eliminar a esta persona.",
        );
      return true;
    },
    onSuccess: () => invalidateRoster(client),
  });
}

function invalidateRoster(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: ["users"] });
  void client.invalidateQueries({ queryKey: ["invitations"] });
  void client.invalidateQueries({ queryKey: ["audit-log"] });
}
