import { createFileRoute } from "@tanstack/react-router";
import { NAV } from "@/shell/nav";
import { StageRoute } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "inventory")!;

/**
 * Inventario · `Existencias`. S0 ships the route and its empty state; S3 fills it.
 */
export const Route = createFileRoute("/_app/inventory")({
  component: () => (
    <StageRoute
      item={ITEM}
      breadcrumb={["Inventario"]}
      emptyTitle="Todavía no hay existencias"
      title="Existencias"
      body="Las existencias aparecen cuando se cargue el catálogo y se reciba la primera mercancía."
    />
  ),
});
