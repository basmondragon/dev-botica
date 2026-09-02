import { createFileRoute } from "@tanstack/react-router";
import { useMe } from "@/api/queries";
import { ReceivePage, type ReceiveSearch } from "@/inventory/receive-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "inventory")!;

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

function validateSearch(search: Record<string, unknown>): ReceiveSearch {
  return { sede: text(search.sede), settings: text(search.settings) };
}

export const Route = createFileRoute("/_app/inventory/receive")({
  validateSearch,
  component: CargarMercancia,
});

function CargarMercancia() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <ReceivePage me={me.data} search={search} />
    </RoleGate>
  );
}
