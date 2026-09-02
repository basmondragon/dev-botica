import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  createFileRoute,
  Outlet,
  useNavigate,
  useRouter,
} from "@tanstack/react-router";
import { ApiError } from "@/api/client";
import { useMe } from "@/api/queries";
import { SettingsDialog } from "@/settings/settings-dialog";
import {
  Content,
  Shell,
  ShellFrame,
  ShellSkeleton,
  SkipLink,
} from "@/shell/shell";
import { KeyboardSheet } from "@/ui/keyboard-sheet";
import { EmptyState, RouteError } from "@/ui/states";
import { useToast } from "@/ui/toast";

export interface AppSearch {
  /** §B.8.4·4 · the open settings section is a search param on whatever route
   *  is showing, so a section is a link and `Escape` returns exactly where you
   *  were. */
  settings?: string;
}

export const Route = createFileRoute("/_app")({
  validateSearch: (search: Record<string, unknown>): AppSearch =>
    typeof search.settings === "string" && search.settings
      ? { settings: search.settings }
      : {},
  component: AppLayout,
});

/*
 * There is deliberately no `beforeLoad` here.
 *
 * §B.10.1 · **the chrome paints immediately.** A `beforeLoad` that waits on
 * `/api/me` blocks the first paint on a network round trip, and against a
 * server that cannot be reached it blocks it for good -- the shell would sit on
 * a skeleton that never resolves, which is the one loading state the design
 * system forbids outright. So the shell renders first and reports what it
 * found: `ShellSkeleton` while the identity is in flight, §B.10.3's route scope
 * when the server did not answer, and the sign-in form when the server refused
 * the session, which `useSessionEnd` below bounces to.
 */

/**
 * A session can end while a page sits open. Nothing polls for that; the first
 * query the server refuses is what says so, and the latch makes it say so once.
 */
function useSessionEnd() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const router = useRouter();
  const toast = useToast();
  const ended = useRef(false);

  useEffect(
    () =>
      client.getQueryCache().subscribe((event) => {
        if (ended.current) return;
        if (event.type !== "updated" || event.action.type !== "error") return;
        const failure: unknown = event.action.error;
        if (!(failure instanceof ApiError) || failure.status !== 401) return;

        ended.current = true;
        const next = router.state.location.href;
        client.clear();
        toast("Su sesión terminó. Entre de nuevo para seguir donde estaba.");
        void navigate({ to: "/login", search: { next } });
      }),
    [client, navigate, router, toast],
  );
}

function AppLayout() {
  const me = useMe();
  useSessionEnd();

  // A refused session is the sign-in form, not an error. `useSessionEnd` does
  // the navigating; this keeps the skeleton up for the frame in between rather
  // than flashing an error the reader cannot act on.
  if (me.isError && me.error instanceof ApiError && me.error.status === 401) {
    return <ShellSkeleton />;
  }

  // A **paused** query is the browser saying it cannot reach anything. Every
  // office surface in Botica is online-only and says so in its own words rather
  // than failing (S0, *Offline*) -- and a skeleton that never resolves is the
  // one loading state §B.10.1 forbids outright.
  if (me.isError || me.isPaused) {
    return (
      <ShellFrame>
        <Content>
          <RouteError
            title="No pudimos cargar su cuenta."
            detail={
              me.error instanceof ApiError && me.error.status > 0
                ? me.error.message
                : "Botica necesita conexión para esta pantalla. Revise la " +
                  "conexión de este equipo e intente de nuevo."
            }
            requestId={
              me.error instanceof ApiError ? me.error.requestId : undefined
            }
            onRetry={() => void me.refetch()}
          />
        </Content>
      </ShellFrame>
    );
  }

  if (me.isPending || !me.data) return <ShellSkeleton />;

  if (me.data.role === "platform_admin" && !me.data.tenant) {
    return (
      <div className="flex h-dvh items-center justify-center bg-canvas">
        <EmptyState
          kind="deliberate"
          title="No hay una droguería seleccionada."
          body={
            "El perfil Plataforma no pertenece a ninguna droguería y llega a una " +
            "seleccionándola en la administración de la plataforma, que no se " +
            "sirve por la dirección pública."
          }
        />
      </div>
    );
  }

  return (
    <>
      <SkipLink />
      <Shell me={me.data}>
        <Outlet />
      </Shell>
      <SettingsDialog me={me.data} />
      <KeyboardSheet />
    </>
  );
}
