import { createFileRoute } from "@tanstack/react-router";
import { useMe } from "@/api/queries";
import { ReceiptPage, type ReceiptSearch } from "@/purchasing/receipt-page";
import { NAV } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";
import { RoleGate } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "purchasing")!;

const text = (value: unknown) =>
  typeof value === "string" && value ? value : undefined;

function validateSearch(search: Record<string, unknown>): ReceiptSearch {
  return {
    orden: text(search.orden),
    recepcion: text(search.recepcion),
    settings: text(search.settings),
  };
}

export const Route = createFileRoute("/_app/purchasing/receipts")({
  validateSearch,
  component: Recepcion,
});

function Recepcion() {
  const me = useMe();
  const search = Route.useSearch();
  if (!me.data) return <ShellSkeleton />;
  return (
    <RoleGate item={ITEM}>
      <ReceiptPage search={search} />
    </RoleGate>
  );
}
