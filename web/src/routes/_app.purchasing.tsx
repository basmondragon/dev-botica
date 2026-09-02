import { createFileRoute } from "@tanstack/react-router";
import { NAV } from "@/shell/nav";
import { StageRoute } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "purchasing")!;

/**
 * Compras · `Órdenes`. S0 ships the route and its empty state; S6 fills it.
 */
export const Route = createFileRoute("/_app/purchasing")({
  component: () => (
    <StageRoute
      item={ITEM}
      breadcrumb={["Compras"]}
      emptyTitle="Todavía no hay órdenes sugeridas"
      title="Órdenes"
      body="Las órdenes sugeridas aparecen cuando el modelo tenga historia de venta para aprender."
    />
  ),
});
