import { createFileRoute } from "@tanstack/react-router";
import type { PurchaseOrderStatus } from "@/api/purchasing";
import { useMe } from "@/api/queries";
import { OrdersPage, type OrdersSearch } from "@/purchasing/orders-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";
import { PAGE_SIZES } from "@/ui/table";

const ITEM = NAV.find((item) => item.key === "purchasing")!;

const STATUSES = new Set<PurchaseOrderStatus>([
  "suggested",
  "approved",
  "sent",
  "partially_received",
  "received",
  "discarded",
]);

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

const list = (value: unknown) =>
  Array.isArray(value)
    ? (value.filter((one) => typeof one === "string" && one) as string[])
    : typeof value === "string" && value
      ? [value]
      : undefined;

function validateSearch(search: Record<string, unknown>): OrdersSearch {
  const size = Number(search.pageSize);
  const page = Math.max(1, Number(search.page) || 1);
  const estado = text(search.estadoOrden) as PurchaseOrderStatus | undefined;
  const sedes = list(search.sedes);
  const origen = text(search.origen);
  return {
    estadoOrden: estado && STATUSES.has(estado) ? estado : undefined,
    sedes: sedes?.length ? sedes : undefined,
    origen: origen === "model" || origen === "manual" ? origen : undefined,
    orden: text(search.orden),
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    sort: text(search.sort),
    order: search.order === "asc" ? "asc" : undefined,
    settings: text(search.settings),
  };
}

export const Route = createFileRoute("/_app/purchasing/orders")({
  validateSearch,
  component: OrdenesDeCompra,
});

function OrdenesDeCompra() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <OrdersPage search={search} />
    </RoleGate>
  );
}
