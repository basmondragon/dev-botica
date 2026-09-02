import { createFileRoute } from "@tanstack/react-router";
import { NAV } from "@/shell/nav";
import { StageRoute } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "counter")!;

/**
 * Mostrador · `Venta`. S0 ships the route and its empty state; S4 fills it.
 */
export const Route = createFileRoute("/_app/counter")({
  component: () => (
    <StageRoute
      item={ITEM}
      breadcrumb={["Mostrador"]}
      emptyTitle="El mostrador todavía no está habilitado"
      title="Venta"
      body="El mostrador se habilita cuando la sede tenga catálogo y existencias."
    />
  ),
});
