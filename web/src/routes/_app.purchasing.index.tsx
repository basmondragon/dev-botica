import { createFileRoute } from "@tanstack/react-router";
import type { ConfidenceBand, ForecastBasis } from "@/api/purchasing";
import { useMe } from "@/api/queries";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";
import {
  SuggestedOrderPage,
  type OrderSearch,
} from "@/purchasing/suggested-order";
import { PAGE_SIZES } from "@/ui/table";

const ITEM = NAV.find((item) => item.key === "purchasing")!;

const BASES = new Set<ForecastBasis>(["parametric", "learning", "learned"]);
const BANDS = new Set<ConfidenceBand>(["alta", "media", "baja"]);

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
 * is a link**: the chip set to `Paramétrica` copied into a new tab shows the
 * same lines narrowed the same way, and the open record panel is a search param
 * for the same reason.
 */
function validateSearch(search: Record<string, unknown>): OrderSearch {
  const size = Number(search.pageSize);
  const page = Math.max(1, Number(search.page) || 1);
  const base = list(search.base)?.filter((one) =>
    BASES.has(one as ForecastBasis),
  ) as ForecastBasis[] | undefined;
  const banda = list(search.banda)?.filter((one) =>
    BANDS.has(one as ConfidenceBand),
  ) as ConfidenceBand[] | undefined;
  return {
    orden: text(search.orden),
    proveedor: text(search.proveedor),
    sede: text(search.sede),
    base: base?.length ? base : undefined,
    banda: banda?.length ? banda : undefined,
    categoria: text(search.categoria),
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    sort: text(search.sort),
    order: search.order === "desc" ? "desc" : undefined,
    row: text(search.row),
    settings: text(search.settings),
  };
}

export const Route = createFileRoute("/_app/purchasing/")({
  validateSearch,
  component: OrdenSugerida,
});

function OrdenSugerida() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <SuggestedOrderPage search={search} />
    </RoleGate>
  );
}
