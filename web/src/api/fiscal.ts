import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

/**
 * S5's typed client, for the **office only**.
 *
 * The counter calls none of it. Nothing in this stage renders on a till: there
 * is no numbering read-out in the sync panel, no `blocked` banner and no
 * contingency line on the receipt — the receipt at `Cobrar` carries
 * `sales.number` and claims nothing about any fiscal document, exactly as S4
 * already draws it (S5, *UI*).
 *
 * Every shape is generated from `/api/openapi.json`, so a response that moved
 * under a consumer is a type error at `make check` rather than a blank cell at
 * a pilot. **The one that matters is `FiscalSummary`**: unconfigured it carries
 * no counts at all, and its generated type says so — a body that cannot hold a
 * zero cannot be rendered as one (§8).
 */

export type FiscalDocumentRow = components["schemas"]["DocumentRow"];
export type FiscalDocumentDetail = components["schemas"]["DocumentDetail"];
export type UnsentSaleRow = components["schemas"]["OrphanRow"];
export type FiscalSummary = components["schemas"]["SummaryOut"];
export type FiscalExportRow = components["schemas"]["ExportRow"];
export type SaleFiscal = components["schemas"]["SaleFiscalOut"];
export type InvoicingSettings = components["schemas"]["InvoicingSettingsOut"];
export type InvoicingSettingsPatch =
  components["schemas"]["InvoicingSettingsIn"];
export type FiscalStatus = FiscalDocumentRow["status"];

export interface FiscalDocumentQuery {
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
  /** Several values, because `Pendientes` is `pending` **and** `sent` — and
   *  the filter has to be where the pagination is (see the endpoint). */
  status?: FiscalStatus[];
  location_id?: string[];
  target?: string;
  since?: string;
  until?: string;
}

export function useFiscalDocuments(
  params: FiscalDocumentQuery,
  enabled = true,
) {
  return useQuery({
    queryKey: ["fiscal-documents", params],
    enabled,
    // §B.10.1 · a re-fetch keeps the previous rows and dims them.
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/fiscal-documents", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los envíos.");
      return data;
    },
  });
}

export function useUnsentSales(
  params: { page: number; page_size: number },
  enabled = true,
) {
  return useQuery({
    queryKey: ["fiscal-unsent-sales", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/fiscal-documents/unsent-sales",
        { params: { query: params } },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos revisar las ventas sin enviar.",
        );
      return data;
    },
  });
}

export function useFiscalDocument(documentId: string | null) {
  return useQuery({
    queryKey: ["fiscal-document", documentId],
    enabled: !!documentId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/fiscal-documents/{document_id}",
        { params: { path: { document_id: documentId! } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar este envío.");
      return data;
    },
  });
}

/**
 * `configured: false` means **render nothing at all** — no strip, no tile, no
 * clause appended to a freshness line (§8). The body carries no counts, so
 * there is no zero for a surface to draw by mistake.
 */
export function useFiscalSummary(enabled = true) {
  return useQuery({
    queryKey: ["fiscal-summary"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/fiscal-documents/summary",
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos leer el estado de los envíos.",
        );
      return data;
    },
  });
}

export function useFiscalExports(enabled = true) {
  return useQuery({
    queryKey: ["fiscal-exports"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/fiscal-documents/exports",
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos listar los archivos.");
      return data;
    },
  });
}

/**
 * `Reintentar` — **rebuilds the payload from the sale as it now stands**, so
 * correcting the cause where the cause lives is the whole of the correction.
 * Idempotent against the job's own lock: pressing it twice enqueues one attempt.
 */
export function useRetryFiscalDocument() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: string) => {
      const { data, error, response } = await api.POST(
        "/api/fiscal-documents/{document_id}/retry",
        { params: { path: { document_id: documentId } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos reintentar este envío.");
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["fiscal-documents"] });
      void client.invalidateQueries({ queryKey: ["fiscal-document"] });
      void client.invalidateQueries({ queryKey: ["fiscal-summary"] });
    },
  });
}

/**
 * The canonical payload for one sale, **rendered without sending it**.
 *
 * This is how a mapping gets written: the first hour of integrating a client's
 * system is spent answering *"what exactly do you send?"*, and a screen that
 * answers it turns a week of emails into an afternoon (§8).
 */
export function useCanonicalDocument(saleId: string | null) {
  return useQuery({
    queryKey: ["canonical-document", saleId],
    enabled: !!saleId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/sales/{sale_id}/canonical-document",
        { params: { path: { sale_id: saleId! } } },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos construir el documento de esta venta.",
        );
      return data;
    },
  });
}

/**
 * One sale's handoff. Answers `{configured: false}` where nothing is connected,
 * and the read-out renders nothing at all for it — not a placeholder, not a
 * status, and never a skeleton that will not resolve (§B.9.2 tier 3).
 */
export function useSaleFiscalDocument(saleId: string | null) {
  return useQuery({
    queryKey: ["sale-fiscal-document", saleId],
    enabled: !!saleId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/sales/{sale_id}/fiscal-document",
        { params: { path: { sale_id: saleId! } } },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos leer la facturación de esta venta.",
        );
      return data;
    },
  });
}

export function useInvoicingSettings() {
  return useQuery({
    queryKey: ["settings", "invoicing"],
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/settings/invoicing",
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar la configuración de facturación.",
        );
      return data;
    },
  });
}

export function useSaveInvoicingSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: InvoicingSettingsPatch) => {
      const { data, error, response } = await api.PATCH(
        "/api/settings/invoicing",
        { body },
      );
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos guardar la configuración de facturación.",
        );
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["settings", "invoicing"] });
      void client.invalidateQueries({ queryKey: ["fiscal-summary"] });
      void client.invalidateQueries({ queryKey: ["fiscal-documents"] });
    },
  });
}
