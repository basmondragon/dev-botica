import { createFileRoute } from "@tanstack/react-router";
import { NAV } from "@/shell/nav";
import { StageRoute } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "pricing")!;

/**
 * Precios · `Propuestas`. S0 ships the route and its empty state; S7 fills it.
 */
export const Route = createFileRoute("/_app/pricing")({
  component: () => (
    <StageRoute
      item={ITEM}
      breadcrumb={["Precios"]}
      emptyTitle="Todavía no hay propuestas de precio"
      title="Propuestas"
      body="Las propuestas de precio aparecen cuando haya venta suficiente para estimar la elasticidad."
    />
  ),
});
