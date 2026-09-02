import { createFileRoute } from "@tanstack/react-router";
import type { ActiveFilter, InvimaStatus, ItemType } from "@/api/catalog";
import { useMe } from "@/api/queries";
import { CatalogPage, type CatalogSearch } from "@/catalog/catalog-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";
import { PAGE_SIZES } from "@/ui/table";

const ITEM = NAV.find((item) => item.key === "inventory")!;

const TYPES = new Set<ItemType>(["product", "service"]);
const STATUSES = new Set<InvimaStatus>([
  "valid",
  "in_process",
  "expired",
  "not_applicable",
]);
const ACTIVE = new Set<ActiveFilter>(["true", "false", "all"]);

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

/**
 * §9 + §B.4 · page, size, sort and every filter live in the URL, so any view is
 * a link and pasting it into another browser reproduces it. The open record
 * panel is a search param for the same reason.
 */
function validateSearch(search: Record<string, unknown>): CatalogSearch {
  const size = Number(search.pageSize);
  const type = text(search.type) as ItemType | undefined;
  const status = text(search.invima_status) as InvimaStatus | undefined;
  const active = text(search.active) as ActiveFilter | undefined;
  const page = Math.max(1, Number(search.page) || 1);
  return {
    q: text(search.q),
    type: type && TYPES.has(type) ? type : undefined,
    manufacturer_id: text(search.manufacturer_id),
    category_id: text(search.category_id),
    invima_status: status && STATUSES.has(status) ? status : undefined,
    // The default is `activos`: a deactivated reference leaves the default grid
    // and the catalog combobox and stays readable by id. A key at its default
    // is dropped, so the URL carries only what differs from it.
    active:
      active && ACTIVE.has(active) && active !== "true" ? active : undefined,
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    sort: text(search.sort),
    order: search.order === "desc" ? "desc" : undefined,
    item: text(search.item),
    settings: text(search.settings),
  };
}

export const Route = createFileRoute("/_app/inventory/catalog")({
  validateSearch,
  component: Catalog,
});

function Catalog() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <CatalogPage me={me.data} search={search} />
    </RoleGate>
  );
}
