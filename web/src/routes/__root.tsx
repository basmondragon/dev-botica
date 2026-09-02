import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import { RouteError } from "@/ui/states";
import { ToastProvider } from "@/ui/toast";

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => (
    <ToastProvider>
      <Outlet />
    </ToastProvider>
  ),
  notFoundComponent: () => (
    <div className="flex h-dvh items-center justify-center">
      <RouteError
        title="Esta página no existe."
        detail="El enlace puede estar desactualizado, o la pantalla puede pertenecer a una etapa que todavía no se ha construido."
      />
    </div>
  ),
});
