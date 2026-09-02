import { useState } from "react";
import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, signIn } from "@/api/client";
import { meQuery, useMe } from "@/api/queries";
import { returnPath } from "@/auth/session";
import { AuthCard } from "@/auth/auth-card";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { RegionError } from "@/ui/states";
import { landingFor } from "@/shell/nav";

interface LoginSearch {
  next?: string;
}

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): LoginSearch => {
    const next = returnPath(search.next);
    return next ? { next } : {};
  },
  component: SignIn,
});

/**
 * §B.8.4·5 · **Ingreso** -- the only unauthenticated entry point. No shell. A
 * 380px L2 card centred on the canvas.
 *
 * **Access is invite-only**, so there is no sign-up link and no password reset
 * that creates an account. Errors are §B.10.3's field or region treatment and
 * name the failure: a generic `Credenciales inválidas` tells an attacker
 * nothing and tells a cashier nothing either.
 */
function SignIn() {
  const navigate = useNavigate();
  const search = Route.useSearch();
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const me = useMe();

  // A session that is already open belongs inside the application. Asked here,
  // after the card has painted, rather than in a `beforeLoad` that would hold
  // the first paint on a request -- and hold it for good against a server that
  // cannot be reached.
  useEffect(() => {
    if (me.data) void navigate({ to: search.next ?? landingFor(me.data.role) });
  }, [me.data, navigate, search.next]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setFailure(null);
    setBusy(true);
    try {
      await signIn(email.trim(), password);
      const me = await client.fetchQuery(meQuery());
      await navigate({ to: search.next ?? landingFor(me.role) });
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? error
          : new ApiError("No pudimos conectar con el servidor.", 0),
      );
    } finally {
      setBusy(false);
    }
  }

  const unreachable = failure?.status === 0;

  return (
    <AuthCard heading="Iniciar sesión">
      <form noValidate onSubmit={submit} className="flex flex-col gap-4">
        {failure ? (
          unreachable ? (
            <RegionError
              title="No pudimos conectar con el servidor."
              detail="Revise la conexión de este equipo e intente de nuevo."
              onRetry={() => setFailure(null)}
            />
          ) : (
            <p role="alert" className="text-12 text-critical">
              {failure.message}
            </p>
          )
        ) : null}

        <Field label="Correo electrónico" htmlFor="sign-in-email">
          <Input
            id="sign-in-email"
            type="email"
            autoComplete="email"
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.currentTarget.value)}
          />
        </Field>

        <Field label="Contraseña" htmlFor="sign-in-password">
          <Input
            id="sign-in-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.currentTarget.value)}
          />
        </Field>

        <Button
          type="submit"
          variant="primary"
          size="md"
          className="w-full"
          busy={busy}
          busyLabel="Entrando…"
        >
          Continuar
        </Button>
      </form>
    </AuthCard>
  );
}
