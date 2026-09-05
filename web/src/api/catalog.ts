import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { api, toApiError } from "./client";
import type { components } from "./schema.gen";

export type ItemRow = components["schemas"]["ItemRow"];
export type ItemDetail = components["schemas"]["ItemDetail"];
export type ItemIn = components["schemas"]["ItemIn"];
export type ItemPatch = components["schemas"]["ItemPatch"];
export type PriceRow = components["schemas"]["PriceOut"];
export type PriceIn = components["schemas"]["PriceIn"];
export type Manufacturer = components["schemas"]["ManufacturerOut"];
export type Category = components["schemas"]["CategoryOut"];
export type Supplier = components["schemas"]["SupplierOut"];
export type SupplierItem = components["schemas"]["SupplierItemOut"];
export type Customer = components["schemas"]["CustomerOut"];
export type CatalogSummary = components["schemas"]["CatalogSummaryOut"];
export type ItemType = ItemRow["type"];
export type VatClass = ItemRow["vat_class"];
export type InvimaStatus = ItemRow["invima_status"];

/** Mirrors the server's own `ActiveFilter`: three states, not a nullable bool. */
export type ActiveFilter = "true" | "false" | "all";

export interface ItemQuery {
  q?: string;
  type?: ItemType;
  manufacturer_id?: string;
  category_id?: string;
  invima_status?: InvimaStatus;
  active: ActiveFilter;
  barcode?: string;
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

const CATALOG = ["catalog"] as const;

export function useItems(params: ItemQuery, enabled = true) {
  return useQuery({
    queryKey: [...CATALOG, "items", params],
    enabled,
    // §B.10.1 · a re-fetch dims the previous rows rather than blanking them.
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/items", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar el catálogo.");
      return data;
    },
  });
}

/**
 * The filter bar's provenance line and the footer's annotation. Both describe
 * the catalog rather than the view, so this is its own request and does not
 * move when a chip does.
 */
export function useCatalogSummary(enabled = true) {
  return useQuery({
    queryKey: [...CATALOG, "summary"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/items/summary");
      if (error || !data)
        throw toApiError(response, error, "No pudimos contar el catálogo.");
      return data;
    },
  });
}

export function useItem(id: string | undefined) {
  return useQuery({
    queryKey: [...CATALOG, "item", id],
    enabled: !!id,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/items/{item_id}", {
        params: { path: { item_id: id! } },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar este producto.");
      return data;
    },
  });
}

export function useManufacturers(enabled = true) {
  return useQuery({
    queryKey: [...CATALOG, "manufacturers"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/manufacturers");
      if (error || !data)
        throw toApiError(
          response,
          error,
          "No pudimos cargar los laboratorios.",
        );
      return data;
    },
  });
}

export function useCategories(enabled = true) {
  return useQuery({
    queryKey: [...CATALOG, "categories"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/categories");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las categorías.");
      return data;
    },
  });
}

export function useSuppliers(enabled = true) {
  return useQuery({
    queryKey: [...CATALOG, "suppliers"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/suppliers");
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los proveedores.");
      return data;
    },
  });
}

export function useSupplierItems(
  params: { supplier_id?: string; item_id?: string },
  enabled = true,
) {
  return useQuery({
    queryKey: [...CATALOG, "supplier-items", params],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/supplier-items", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar las referencias.");
      return data;
    },
  });
}

export interface CustomerQuery {
  q?: string;
  page: number;
  page_size: number;
  sort?: string;
  order: "asc" | "desc";
}

export function useCustomers(params: CustomerQuery, enabled = true) {
  return useQuery({
    queryKey: [...CATALOG, "customers", params],
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/customers", {
        params: { query: params },
      });
      if (error || !data)
        throw toApiError(response, error, "No pudimos cargar los clientes.");
      return data;
    },
  });
}

function invalidate(client: ReturnType<typeof useQueryClient>) {
  void client.invalidateQueries({ queryKey: CATALOG });
  void client.invalidateQueries({ queryKey: ["audit-log"] });
}

export function useCreateItem() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: ItemIn) => {
      const { data, error, response } = await api.POST("/api/items", { body });
      if (error || !data)
        throw toApiError(response, error, "No pudimos crear el producto.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useUpdateItem() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: ItemPatch }) => {
      const { data, error, response } = await api.PATCH(
        "/api/items/{item_id}",
        {
          params: { path: { item_id: id } },
          body,
        },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el producto.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

/**
 * A11 · the **only** call in this client that writes a price. S7 extends the
 * endpoint behind it; it does not gain a second one.
 */
export function useSetPrice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: PriceIn }) => {
      const { data, error, response } = await api.POST(
        "/api/items/{item_id}/prices",
        { params: { path: { item_id: id } }, body },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el precio.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

/**
 * **The other half of a person's decision** (A11): declining a suggestion.
 *
 * It lives here, on S1's own client, because the write is S1's -- `taken`,
 * `modified` and `dismissed` are the three states a decision reaches and all
 * three are stamped by the stage that carried the decision out. Precios has no
 * write path to a suggestion's outcome at all, which is what makes the
 * amendment a property of the routes rather than a policy.
 */
export function useDismissProposal() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (proposalId: string) => {
      const { data, error, response } = await api.POST(
        "/api/price-proposals/{proposal_id}/dismiss",
        { params: { path: { proposal_id: proposalId } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos descartar la propuesta.");
      return data;
    },
    onSuccess: () => {
      invalidate(client);
      void client.invalidateQueries({ queryKey: ["pricing-items"] });
      void client.invalidateQueries({ queryKey: ["pricing-summary"] });
    },
  });
}

export function useWithdrawPrice() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.DELETE(
        "/api/item-prices/{price_id}",
        { params: { path: { price_id: id } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos quitar este precio.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useSaveManufacturer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      body,
    }: {
      id?: string;
      body: components["schemas"]["ManufacturerIn"];
    }) => {
      const call = id
        ? api.PATCH("/api/manufacturers/{manufacturer_id}", {
            params: { path: { manufacturer_id: id } },
            body,
          })
        : api.POST("/api/manufacturers", { body });
      const { data, error, response } = await call;
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el laboratorio.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useDeleteManufacturer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE(
        "/api/manufacturers/{manufacturer_id}",
        { params: { path: { manufacturer_id: id } } },
      );
      if (error)
        throw toApiError(
          response,
          error,
          "No pudimos eliminar el laboratorio.",
        );
      return true;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useSaveCategory() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      body,
    }: {
      id?: string;
      body: components["schemas"]["CategoryIn"];
    }) => {
      const call = id
        ? api.PATCH("/api/categories/{category_id}", {
            params: { path: { category_id: id } },
            body,
          })
        : api.POST("/api/categories", { body });
      const { data, error, response } = await call;
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar la categoría.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useDeleteCategory() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE(
        "/api/categories/{category_id}",
        { params: { path: { category_id: id } } },
      );
      if (error)
        throw toApiError(response, error, "No pudimos eliminar la categoría.");
      return true;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useSaveSupplier() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      body,
    }: {
      id?: string;
      body: components["schemas"]["SupplierIn"];
    }) => {
      const call = id
        ? api.PATCH("/api/suppliers/{supplier_id}", {
            params: { path: { supplier_id: id } },
            body,
          })
        : api.POST("/api/suppliers", { body });
      const { data, error, response } = await call;
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el proveedor.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useDeleteSupplier() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE(
        "/api/suppliers/{supplier_id}",
        { params: { path: { supplier_id: id } } },
      );
      if (error)
        throw toApiError(response, error, "No pudimos eliminar el proveedor.");
      return true;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useSaveSupplierItem() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      body,
    }: {
      id?: string;
      body:
        | components["schemas"]["SupplierItemIn"]
        | components["schemas"]["SupplierItemPatch"];
    }) => {
      const call = id
        ? api.PATCH("/api/supplier-items/{link_id}", {
            params: { path: { link_id: id } },
            body: body as components["schemas"]["SupplierItemPatch"],
          })
        : api.POST("/api/supplier-items", {
            body: body as components["schemas"]["SupplierItemIn"],
          });
      const { data, error, response } = await call;
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el proveedor.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useDeleteSupplierItem() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await api.DELETE(
        "/api/supplier-items/{link_id}",
        { params: { path: { link_id: id } } },
      );
      if (error)
        throw toApiError(response, error, "No pudimos quitar el proveedor.");
      return true;
    },
    onSuccess: () => invalidate(client),
  });
}

export function useSaveCustomer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      body,
    }: {
      id?: string;
      body:
        | components["schemas"]["CustomerIn"]
        | components["schemas"]["CustomerPatch"];
    }) => {
      const call = id
        ? api.PATCH("/api/customers/{customer_id}", {
            params: { path: { customer_id: id } },
            body: body as components["schemas"]["CustomerPatch"],
          })
        : api.POST("/api/customers", {
            body: body as components["schemas"]["CustomerIn"],
          });
      const { data, error, response } = await call;
      if (error || !data)
        throw toApiError(response, error, "No pudimos guardar el cliente.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

/**
 * The Ley 1581 deletion. The response names which of the two branches it took
 * and the sale count, because an administrator who pressed one button is owed
 * the difference.
 */
export function useDeleteCustomer() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await api.DELETE(
        "/api/customers/{customer_id}",
        { params: { path: { customer_id: id } } },
      );
      if (error || !data)
        throw toApiError(response, error, "No pudimos eliminar este cliente.");
      return data;
    },
    onSuccess: () => invalidate(client),
  });
}

/** Narrowing helper: a query is "settled and empty" only once it has answered. */
export function isEmpty(query: UseQueryResult<{ row_count: number }>) {
  return !query.isPending && (query.data?.row_count ?? 0) === 0;
}
