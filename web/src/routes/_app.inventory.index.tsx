import { createFileRoute } from "@tanstack/react-router";
import type { StockState } from "@/api/inventory";
import { useMe } from "@/api/queries";
import { StockPage, type StockSearch } from "@/inventory/stock-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";
import { PAGE_SIZES } from "@/ui/table";

const ITEM = NAV.find((item) => item.key === "inventory")!;

const STATES = new Set<StockState>([
  "expired",
  "stockout",
  "expiring_urgent",
  "expiring",
  "reorder_point",
  "overstock",
  "sufficient",
]);
const EXPIRY = new Set(["expired", "valuation", "alert", "notice", "none"]);

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

const list = (value: unknown) =>
  Array.isArray(value)
    ? (value.filter((one) => typeof one === "string" && one) as string[])
    : typeof value === "string" && value
      ? [value]
      : undefined;

/**
 * §9 + §B.4 · page, size, sort and every filter live in the URL, so **any view
 * is a link**: acceptance 12 copies the address bar into a new tab and expects
 * the same 4.284 rows narrowed the same way. The open record panel is a search
 * param for the same reason.
 */
function validateSearch(search: Record<string, unknown>): StockSearch {
  const size = Number(search.pageSize);
  const state = text(search.estado) as StockState | undefined;
  const expiry = text(search.vencimiento);
  const page = Math.max(1, Number(search.page) || 1);
  const sedes = list(search.sedes);
  return {
    q: text(search.q),
    sedes: sedes?.length ? sedes : undefined,
    categoria: text(search.categoria),
    estado: state && STATES.has(state) ? state : undefined,
    accion:
      search.accion === true || search.accion === "true" ? true : undefined,
    vencimiento:
      expiry && EXPIRY.has(expiry)
        ? (expiry as StockSearch["vencimiento"])
        : undefined,
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    sort: text(search.sort),
    order: search.order === "desc" ? "desc" : undefined,
    row: text(search.row),
    lote: text(search.lote),
    settings: text(search.settings),
  };
}

/**
 * **`/inventory` is Existencias**, and S1's redirect to Catálogo is the diff
 * this stage applied: a `/inventory` link saved before this stage still works,
 * and now lands on the screen the module is about.
 */
export const Route = createFileRoute("/_app/inventory/")({
  validateSearch,
  component: Existencias,
});

function Existencias() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <StockPage me={me.data} search={search} />
    </RoleGate>
  );
}
