import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * **At S1 the module has one route and Catálogo is its landing.** S3 adds
 * `Existencias` and takes it, and this redirect is the diff to apply -- a
 * `/inventory` link saved today keeps working either way.
 */
export const Route = createFileRoute("/_app/inventory/")({
  beforeLoad: () => {
    throw redirect({ to: "/inventory/catalog", search: {} });
  },
});
