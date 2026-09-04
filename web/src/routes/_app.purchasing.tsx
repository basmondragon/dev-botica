import { createFileRoute, Outlet } from "@tanstack/react-router";

/**
 * The Compras module. §B.8.5 · one nav item and three routes, switched by the
 * breadcrumb menu: `Orden sugerida`, `Órdenes de compra` and `Recepción`. This
 * layout holds nothing of its own, because §B.8.5 allows exactly one `t-28`
 * title per route and each child draws its own.
 */
export const Route = createFileRoute("/_app/purchasing")({
  component: () => <Outlet />,
});
