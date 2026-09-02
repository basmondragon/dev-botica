import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { ApiError } from "./api/client";
import "./index.css";
import { routeTree } from "./routeTree.gen";
import { registerShellWorker } from "./offline";

const queryClient = new QueryClient({
  defaultOptions: {
    mutations: { networkMode: "always" },
    queries: {
      // Every office surface in Botica is online-only and **says so in its own
      // words** rather than failing (S0, *Offline*). The library's default is
      // to hold a query in a `paused` state when it believes the browser is
      // offline, which renders as a skeleton that never resolves -- the exact
      // shape §B.10.1 forbids. `always` makes an unreachable server an error
      // the route can name, with the operation, the reason and a retry.
      networkMode: "always",
      // A refusal is an answer, not a transient failure: retrying a 401 or a
      // 403 costs the reader three seconds of blank page and changes nothing.
      retry: (failureCount, error) =>
        failureCount < 2 &&
        !(
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500
        ),
      staleTime: 5_000,
      refetchOnWindowFocus: false,
      // §B.10.1 · nothing here writes an API response anywhere but memory. No
      // localStorage, no sessionStorage, no IndexedDB: S2's local store is the
      // one place tenant data is allowed to live in a browser, and a
      // convenience cache written here would still be there the day a till goes
      // offline holding a stale sede name and an expired role.
      gcTime: 5 * 60_000,
    },
  },
});

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  context: { queryClient },
  // §B.10.1 · the chrome paints immediately. A route that is still resolving
  // renders its own pending component from the first frame rather than leaving
  // the page blank while a request is in flight.
  defaultPendingMs: 0,
  defaultPendingMinMs: 0,
  defaultPendingComponent: () => <div className="min-h-dvh bg-canvas" />,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);

void registerShellWorker();
