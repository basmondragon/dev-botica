import { createFileRoute } from "@tanstack/react-router";
import { useMe } from "@/api/queries";
import { CountsPage, type CountsSearch } from "@/inventory/counts-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";
import { PAGE_SIZES } from "@/ui/table";

const ITEM = NAV.find((item) => item.key === "inventory")!;

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

function validateSearch(search: Record<string, unknown>): CountsSearch {
  const size = Number(search.pageSize);
  const page = Math.max(1, Number(search.page) || 1);
  return {
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    conteo: text(search.conteo),
    nuevo: search.nuevo === true || search.nuevo === "true" ? true : undefined,
    lote: text(search.lote),
    settings: text(search.settings),
  };
}

export const Route = createFileRoute("/_app/inventory/counts")({
  validateSearch,
  component: Conteos,
});

function Conteos() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <CountsPage me={me.data} search={search} />
    </RoleGate>
  );
}
