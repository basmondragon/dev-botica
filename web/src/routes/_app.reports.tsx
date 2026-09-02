import { createFileRoute } from "@tanstack/react-router";
import { NAV } from "@/shell/nav";
import { StageRoute } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "reports")!;

/**
 * Reportes · `Reportes`. S0 ships the route and its empty state; S9 fills it.
 */
export const Route = createFileRoute("/_app/reports")({
  component: () => (
    <StageRoute
      item={ITEM}
      breadcrumb={["Reportes"]}
      emptyTitle="Todavía no hay reportes"
      title="Reportes"
      body="Los reportes se calculan sobre las métricas diarias de la red. Aparecen con la primera venta."
    />
  ),
});
