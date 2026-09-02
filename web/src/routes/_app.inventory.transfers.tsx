import { createFileRoute } from "@tanstack/react-router";
import { useMe } from "@/api/queries";
import {
  TransfersPage,
  type TransfersSearch,
} from "@/inventory/transfers-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";
import { PAGE_SIZES } from "@/ui/table";

const ITEM = NAV.find((item) => item.key === "inventory")!;

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

function validateSearch(search: Record<string, unknown>): TransfersSearch {
  const size = Number(search.pageSize);
  const page = Math.max(1, Number(search.page) || 1);
  return {
    page: page === 1 ? undefined : page,
    pageSize: PAGE_SIZES.includes(size as (typeof PAGE_SIZES)[number])
      ? size
      : undefined,
    traslado: text(search.traslado),
    nuevo: search.nuevo === true || search.nuevo === "true" ? true : undefined,
    settings: text(search.settings),
  };
}

export const Route = createFileRoute("/_app/inventory/transfers")({
  validateSearch,
  component: Traslados,
});

function Traslados() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <TransfersPage me={me.data} search={search} />
    </RoleGate>
  );
}
