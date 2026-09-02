import { createFileRoute } from "@tanstack/react-router";
import { NAV } from "@/shell/nav";
import { StageRoute } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "dashboard")!;

/**
 * Panel · `Resumen de red`. S0 ships the route and its empty state; S9 fills it.
 */
export const Route = createFileRoute("/_app/dashboard")({
  component: () => (
    <StageRoute
      item={ITEM}
      breadcrumb={["Panel"]}
      emptyTitle="Todavía no hay métricas de la red"
      title="Resumen de red"
      body="El panel se arma con la venta de las sedes. Aparece cuando el mostrador registre las primeras ventas."
    />
  ),
});
