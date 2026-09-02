import { createFileRoute } from "@tanstack/react-router";
import { NAV } from "@/shell/nav";
import { StageRoute } from "@/shell/stage-route";

const ITEM = NAV.find((item) => item.key === "locations")!;

/**
 * Sedes · `Red`. S0 ships the route and its empty state; S3 · S9 fills it.
 */
export const Route = createFileRoute("/_app/locations")({
  component: () => (
    <StageRoute
      item={ITEM}
      breadcrumb={["Sedes"]}
      emptyTitle="Todavía no hay nada que comparar entre sedes"
      title="Red"
      body="Las sedes se crean desde la administración de la plataforma. Escriba a soporte para agregar una."
    />
  ),
});
