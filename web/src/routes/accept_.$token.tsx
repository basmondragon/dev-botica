import { createFileRoute } from "@tanstack/react-router";
import { AuthCard } from "@/auth/auth-card";

/**
 * `/accept/{token}` is the retired shape, and it is a route on purpose: it
 * reaches a screen that **neither previews nor accepts what is in it**.
 *
 * A path segment is written into every proxy and application access log by
 * construction, so a token that arrived this way must be treated as spent. The
 * access-log formatter scrubs the shape out of both the web server's line and
 * Django's own records; this screen is the other half of that pair.
 */
export const Route = createFileRoute("/accept_/$token")({
  component: () => (
    <AuthCard heading="Este enlace no sirve">
      <p className="text-14 text-ink-body">
        Los enlaces de invitación de Botica no tienen esta forma. Por seguridad,
        no abrimos invitaciones que lleguen así.
      </p>
      <p className="mt-2 text-14 text-ink-body">
        Pida un enlace nuevo a la administradora de su droguería.
      </p>
    </AuthCard>
  ),
});
