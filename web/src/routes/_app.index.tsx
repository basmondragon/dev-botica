import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMe } from "@/api/queries";
import { landingFor } from "@/shell/nav";
import { ShellSkeleton } from "@/shell/shell";

/**
 * §B.8.3 · `/` redirects by role: Panel for `owner` and `admin`, Mostrador for
 * a `cashier`.
 *
 * The decision needs the identity, and the identity is `_app`'s to fetch, so
 * this route waits for it in the component rather than in a `beforeLoad` — an
 * unreachable server then reaches `_app`'s own route-scope error over a shell
 * that has painted, instead of holding `/` on a skeleton that never resolves.
 *
 * The navigation is an effect, not a `throw redirect()`. This router version
 * has no render-time redirect handling: a redirect thrown from render reaches
 * the global catch boundary and paints its own English error card, which is
 * what `/` — the PWA's `start_url`, and the URL people type — would have shown.
 */
export const Route = createFileRoute("/_app/")({
  component: Landing,
});

function Landing() {
  const me = useMe();
  const navigate = useNavigate();
  const role = me.data?.role;

  useEffect(() => {
    if (role) void navigate({ to: landingFor(role), replace: true });
  }, [role, navigate]);

  return <ShellSkeleton />;
}
