import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ApiError, api, toApiError } from "@/api/client";
import { AuthCard } from "@/auth/auth-card";
import { Button } from "@/ui/button";
import { Field, Input } from "@/ui/field";
import { RequestId, SkeletonBar } from "@/ui/states";
import { roleLabel } from "@/shell/nav";
import type { components } from "@/api/schema.gen";

type Preview = components["schemas"]["InvitationPreviewOut"];

export const Route = createFileRoute("/accept")({
  component: Accept,
});

/**
 * The token belongs to the page load, not to a component instance: it is read
 * out of `location.hash` once, and the hash is cleared **before anything else
 * can read it back out of the address bar**. Doing this at module scope rather
 * than in an effect is what makes "read once, then erase" true -- a remount
 * would otherwise find an address bar this code had already emptied.
 */
const TOKEN = readAndClearHash();

function readAndClearHash(): string | null {
  if (typeof window === "undefined") return null;
  const raw = window.location.hash.replace(/^#/, "");
  if (!raw) return null;
  window.history.replaceState(null, "", window.location.pathname);
  return raw;
}

/**
 * §B.8.4·5 · **Aceptar invitación**. The token is read from `location.hash`,
 * the hash is cleared, and the preview renders the droguería's name, the
 * address and the role before anything is typed.
 *
 * The token travels in a fragment and in a request body, never in a path: a
 * path segment is written into every proxy and application access log by
 * construction, including the log line for the HTML request that merely loads
 * the shell.
 */
function Accept() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitFailure, setSubmitFailure] = useState<ApiError | null>(null);

  const preview = useQuery<Preview, ApiError>({
    queryKey: ["invitation-preview"],
    retry: false,
    queryFn: async () => {
      if (!TOKEN) throw new ApiError("No reconocemos esta invitación.", 404);
      const { data, error, response } = await api.POST(
        "/api/invitations/preview",
        { body: { token: TOKEN } },
      );
      if (error || !data)
        throw toApiError(response, error, "No reconocemos esta invitación.");
      return data;
    },
  });

  const failure = submitFailure ?? (preview.isError ? preview.error : null);

  if (failure) {
    return (
      <AuthCard heading="No pudimos abrir esta invitación">
        <p role="alert" className="text-14 text-ink-body">
          {failure.message}
        </p>
        <p className="mt-2 text-14 text-ink-body">
          Pida un enlace nuevo a la administradora de su droguería.
        </p>
        {failure.requestId ? (
          <p className="mt-4">
            <RequestId value={failure.requestId} />
          </p>
        ) : null}
      </AuthCard>
    );
  }

  if (preview.isPending || !preview.data) {
    // §B.10.1 · a skeleton of the real field stack.
    return (
      <AuthCard heading="Aceptar invitación">
        <div className="flex flex-col gap-4">
          <SkeletonBar className="h-3 w-40" />
          {Array.from({ length: 2 }, (_, index) => (
            <div key={index}>
              <SkeletonBar className="h-3 w-24" />
              <SkeletonBar className="mt-2 h-[34px] w-full" />
            </div>
          ))}
          <SkeletonBar className="h-10 w-full" />
        </div>
      </AuthCard>
    );
  }

  return (
    <AuthCard heading="Aceptar invitación">
      <p className="mb-5 text-12 text-ink-label">
        {preview.data.tenant_name} · {roleLabel(preview.data.role)}
        {preview.data.location_name ? ` · ${preview.data.location_name}` : ""}
      </p>

      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!TOKEN) return;
          setBusy(true);
          const { data, error, response } = await api.POST(
            "/api/invitations/accept",
            { body: { token: TOKEN, name: name.trim(), password } },
          );
          setBusy(false);
          if (error || !data) {
            setSubmitFailure(
              toApiError(response, error, "No pudimos crear su cuenta."),
            );
            return;
          }
          await navigate({ to: data.landing });
        }}
      >
        <Field label="Correo electrónico" htmlFor="accept-email">
          <Input id="accept-email" value={preview.data.email} readOnly />
        </Field>

        <Field label="Su nombre" htmlFor="accept-name" required>
          <Input
            id="accept-name"
            autoFocus
            autoComplete="name"
            value={name}
            onChange={(event) => setName(event.currentTarget.value)}
          />
        </Field>

        <Field
          label="Contraseña"
          htmlFor="accept-password"
          help="Mínimo doce caracteres."
        >
          <Input
            id="accept-password"
            type="password"
            autoComplete="new-password"
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
          Crear mi cuenta
        </Button>
      </form>
    </AuthCard>
  );
}
