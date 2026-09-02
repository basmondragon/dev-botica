import { createFileRoute, Outlet } from "@tanstack/react-router";

/**
 * The Inventario module. §B.8.5 · one nav item, two routes switched by the
 * drawn segmented control: `Catálogo` (S1) and `Existencias` (S3). This layout
 * holds nothing of its own; each child draws its own header and title, because
 * §B.8.5 allows exactly one `t-28` title per route.
 */
export const Route = createFileRoute("/_app/inventory")({
  component: () => <Outlet />,
});
