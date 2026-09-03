import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
import { PanelLeftClose, PanelLeftOpen, Settings } from "lucide-react";
import { useMe, useNavCounters, type Me } from "@/api/queries";
import { useOpenTicketCount } from "@/counter/local";
import { AppVersionStamp } from "@/ui/app-version-stamp";
import { BrandSquare } from "@/ui/brand";
import { Button, type ButtonProps } from "@/ui/button";
import { cn } from "@/ui/cn";
import { isEntryOrSelectionTarget } from "@/ui/keyboard-target";
import { SkeletonBar } from "@/ui/states";
import { useSettingsDialog } from "@/settings/use-settings";
import { NAV, canReach, gotoTargets, reachable, roleLabel } from "./nav";
import { reachableSections } from "@/settings/use-settings";

const CHORD_MS = 1500;

const SidebarState = createContext<{ collapsed: boolean }>({
  collapsed: false,
});

/**
 * §A.13 · the shell: a 280px L0 sidebar, a 64px sticky header and the content
 * region. It repeats identically inside every route.
 */
export function Shell({ me, children }: { me: Me; children: ReactNode }) {
  useKeyboardLayer(me);
  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar me={me} />
      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}

/**
 * §B.13.2 · the keyboard layer: `⌘,` opens the settings dialog, `g` then a
 * letter reaches a route, and **`⌘K` is bound to nothing**. Reserving it costs
 * nothing and means the palette can arrive later without retraining anyone.
 */
function useKeyboardLayer(me: Me) {
  const navigate = useNavigate();
  const settings = useSettingsDialog();
  const armedAt = useRef(0);
  const open = useRef(settings.show);

  useEffect(() => {
    open.current = settings.show;
  }, [settings.show]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;

      if ((event.metaKey || event.ctrlKey) && event.key === ",") {
        event.preventDefault();
        const first = reachableSections(me.role)[0];
        if (first) open.current(first.id);
        return;
      }
      // ⌘K is reserved for the command palette and bound to nothing in v1.
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k")
        return;

      if (
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        isEntryOrSelectionTarget(target) ||
        target?.closest?.('[role="dialog"]')
      ) {
        armedAt.current = 0;
        return;
      }

      const armed =
        armedAt.current > 0 && Date.now() - armedAt.current < CHORD_MS;
      if (!armed) {
        if (event.key !== "g") return;
        armedAt.current = Date.now();
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      armedAt.current = 0;
      const item = gotoTargets(me.role).get(event.key);
      if (item) {
        event.preventDefault();
        event.stopImmediatePropagation();
        void navigate({ to: item.to });
      }
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [me.role, navigate]);
}

function Sidebar({ me }: { me: Me }) {
  const [collapsed, setCollapsed] = useState(false);
  const value = useMemo(() => ({ collapsed }), [collapsed]);

  return (
    <SidebarState value={value}>
      <nav
        aria-label="Navegación principal"
        className={cn(
          "flex h-full shrink-0 flex-col border-r border-hairline bg-chrome",
          collapsed ? "w-16" : "w-70",
        )}
      >
        <OrganisationHeader
          me={me}
          collapsed={collapsed}
          onToggle={() => setCollapsed((current) => !current)}
        />
        <NavList me={me} collapsed={collapsed} />
        <UserFooter me={me} collapsed={collapsed} />
      </nav>
    </SidebarState>
  );
}

/**
 * §A.13.1 · 64px, the brand square and `tenants.name`. **The organisation name
 * is a label, not a control** -- there is no workspace switcher in v1, and an
 * affordance promising one is worse than none.
 */
function OrganisationHeader({
  me,
  collapsed,
  onToggle,
}: {
  me: Me;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className={cn(
        "flex h-16 shrink-0 items-center border-b border-hairline",
        collapsed ? "justify-center px-2" : "gap-2.5 px-5",
      )}
    >
      <BrandSquare />
      {!collapsed ? (
        <span className="min-w-0 flex-1 truncate text-14 font-medium text-ink">
          {me.tenant?.name ?? "Sin droguería seleccionada"}
        </span>
      ) : null}
      <Button
        variant="ghost"
        size="sm"
        iconOnly
        aria-label={collapsed ? "Expandir el menú" : "Contraer el menú"}
        aria-expanded={!collapsed}
        onClick={onToggle}
        className={cn(
          collapsed && "absolute opacity-0 focus-visible:opacity-100",
        )}
      >
        {collapsed ? (
          <PanelLeftOpen aria-hidden strokeWidth={1.5} className="size-4" />
        ) : (
          <PanelLeftClose aria-hidden strokeWidth={1.5} className="size-4" />
        )}
      </Button>
    </div>
  );
}

function NavList({ me, collapsed }: { me: Me; collapsed: boolean }) {
  const location = useLocation();
  const counters = useNavCounters(me.role !== "cashier");
  // §B.8.2 · **`Mostrador 3` is ventas abiertas, and a cashier reads it from
  // their own local store** rather than from the server. A nav counter that
  // needed the network would be the one number on a till surface that stops
  // working when the cable comes out (§4, A4) -- so the office asks
  // `/api/nav-counters` and the till counts its own tickets.
  const local = useOpenTicketCount(me.role === "cashier");
  const visible = reachable(me.role);

  return (
    <div className="flex min-h-0 flex-1 flex-col py-3">
      <div className="flex flex-col gap-0.5">
        {visible.length === 0 ? (
          <p className="px-5 py-2 text-12 text-ink-body">
            Este perfil no alcanza ninguna sección de la droguería. Sus
            superficies están en la administración de la plataforma.
          </p>
        ) : (
          visible.map((item) => {
            const active = location.pathname.startsWith(item.to);
            const count =
              item.key === "counter" && me.role === "cashier"
                ? local
                : (counters.data?.counters[item.key] ?? 0);
            const critical =
              counters.data?.critical.includes(item.key) ?? false;
            return (
              <Link
                key={item.to}
                to={item.to}
                aria-label={collapsed ? item.label : undefined}
                className={cn(
                  "mx-3 flex h-[38px] items-center rounded-control text-14",
                  "transition-[background-color,color] duration-140 ease-out",
                  collapsed ? "justify-center px-0" : "gap-2.5 px-3",
                  active
                    ? "bg-surface font-medium text-ink"
                    : "text-ink-body hover:bg-hover-nav",
                )}
              >
                <item.icon
                  aria-hidden
                  strokeWidth={1.5}
                  className="size-4 shrink-0"
                />
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate",
                    collapsed && "sr-only",
                  )}
                >
                  {item.label}
                </span>
                {/* §B.8.2 · zero renders nothing at all. Not a `0`, not a dot,
                    not a dimmed badge. A module with no work waiting is a
                    module with no counter. */}
                {count > 0 ? (
                  collapsed ? (
                    <span
                      aria-hidden
                      className="absolute right-2 top-2 size-1.5 rounded-pill bg-neutral"
                    />
                  ) : (
                    <span
                      className={cn(
                        "shrink-0 text-11 tabular-nums",
                        critical
                          ? "text-critical"
                          : active
                            ? "font-medium text-ink"
                            : "text-ink-label",
                      )}
                    >
                      {count}
                    </span>
                  )
                ) : null}
              </Link>
            );
          })
        )}
      </div>
      <AppVersionStamp
        className={cn("mt-auto", collapsed && "justify-center px-1")}
      />
    </div>
  );
}

/** §A.13.1 · 64px, the name at 12px and the role and sede at 11px. */
function UserFooter({ me, collapsed }: { me: Me; collapsed: boolean }) {
  const settings = useSettingsDialog();
  const reachesSettings = me.role !== "cashier";
  const subtitle = [roleLabel(me.role), me.location_name]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      aria-label="Cuenta"
      className={cn(
        "flex h-16 shrink-0 items-center border-t border-hairline",
        collapsed ? "justify-center px-2" : "gap-1 px-4",
      )}
    >
      {!collapsed ? (
        <div className="min-w-0 flex-1 pl-1">
          <p className="truncate text-12 text-ink">{me.name}</p>
          <p className="truncate text-11 text-ink-label">{subtitle}</p>
        </div>
      ) : null}
      {/* §B.8.3 · an item a role cannot reach is not rendered, and is never
          rendered disabled. */}
      {reachesSettings ? (
        <Button
          variant="ghost"
          size="sm"
          iconOnly
          aria-label="Ajustes"
          title="Ajustes — ⌘,"
          onClick={() => settings.show("general")}
        >
          <Settings aria-hidden strokeWidth={1.5} className="size-4" />
        </Button>
      ) : null}
    </div>
  );
}

/**
 * §A.13.2 · 64px sticky, `padding:0 40px`, exactly one `t-28` title.
 *
 * `breadcrumb` takes plain segments, or a node where a module puts a **menu**
 * in the first one (§B.8.1): the crumb is already drawn at 12px `#727272` and
 * giving it a job is what lets a module hold four routes behind one nav item
 * without an eighth item in a list a cashier uses all day.
 */
export function TopBar({
  breadcrumb,
  title,
  actions,
}: {
  breadcrumb: string[] | ReactNode;
  title: string;
  actions?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-30 flex h-16 shrink-0 items-center gap-4 border-b border-hairline bg-nav-veil px-10 backdrop-blur-[14px]">
      <div className="flex min-w-0 items-baseline gap-2">
        {Array.isArray(breadcrumb) ? (
          breadcrumb.length > 0 ? (
            <p className="flex shrink-0 items-center gap-1 text-12 text-ink-label">
              {breadcrumb.map((crumb) => (
                <span key={crumb} className="flex items-center gap-1">
                  {crumb}
                  <span className="text-ink-disabled">/</span>
                </span>
              ))}
            </p>
          ) : null
        ) : (
          breadcrumb
        )}
        <h1 className="truncate text-28 tracking-display text-ink">{title}</h1>
      </div>
      <div className="ml-auto flex items-center gap-2">{actions}</div>
    </header>
  );
}

export function TopBarButton(props: Omit<ButtonProps, "size">) {
  return <Button size="sm" {...props} />;
}

/** §B.3 · the content region at a `32px 40px` inset. */
export function Content({
  children,
  className,
  id = "content",
}: {
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <main
      id={id}
      tabIndex={-1}
      className={cn("min-h-0 flex-1 overflow-y-auto px-10 py-8", className)}
    >
      {children}
    </main>
  );
}

/** A route whose `main` is one full-height panel, at `32px 40px` with the
 *  table filling it. */
export function TableContent({
  children,
  id = "content",
}: {
  children: ReactNode;
  id?: string;
}) {
  return (
    <main
      id={id}
      tabIndex={-1}
      className="flex min-h-0 flex-1 flex-col overflow-hidden px-10 py-8"
    >
      {children}
    </main>
  );
}

/** §B.13.1 · every route begins with a skip link, hidden until focused. */
export function SkipLink() {
  return (
    <a
      href="#content"
      className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-control focus:bg-ink focus:px-4 focus:py-2.5 focus:text-14 focus:text-canvas"
    >
      Ir al contenido
    </a>
  );
}

/**
 * §B.10.1 · the chrome paints immediately and the nav list renders as skeleton
 * items until `/api/me` resolves the role -- **never as the seven-item
 * administrator nav that then collapses to two**. A cashier watching five items
 * disappear on every sign-in learns that the application is unsure what they
 * may do, and the flash is a two-frame advertisement of routes they will be
 * refused.
 */
/**
 * The chrome, with nothing behind it yet: the sidebar plane, the brand square,
 * the version stamp and the header rule. §B.10.1 · **the chrome paints
 * immediately**, and whatever the content region has to say is said inside it
 * -- which is the difference between "the app is down" and "this screen needs
 * the network" (S0, *Offline*).
 */
export function ShellFrame({ children }: { children?: ReactNode }) {
  return (
    <div className="flex h-dvh overflow-hidden">
      <nav
        aria-label="Navegación principal"
        className="flex h-full w-70 shrink-0 flex-col border-r border-hairline bg-chrome"
      >
        <div className="flex h-16 shrink-0 items-center gap-2.5 border-b border-hairline px-5">
          <BrandSquare />
          <SkeletonBar className="h-3.5 w-36" />
        </div>
        <div className="flex min-h-0 flex-1 flex-col py-3">
          <div className="flex flex-col gap-0.5">
            {/* Never more than two placeholders: a cashier watching five items
                disappear on every sign-in learns that the application is unsure
                what they may do, and the flash is a two-frame advertisement of
                routes they will be refused (§B.10.1). */}
            {Array.from({ length: 2 }, (_, index) => (
              <div key={index} className="mx-3 flex h-[38px] items-center px-3">
                <SkeletonBar className="h-3.5 w-28" />
              </div>
            ))}
          </div>
          <AppVersionStamp className="mt-auto" />
        </div>
        <div className="flex h-16 shrink-0 items-center border-t border-hairline px-5">
          <div className="min-w-0 flex-1">
            <SkeletonBar className="h-3 w-28" />
            <SkeletonBar className="mt-1.5 h-2.5 w-20" />
          </div>
        </div>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="h-16 shrink-0 border-b border-hairline" />
        {children ?? (
          <div className="px-10 py-8">
            <SkeletonBar className="h-5 w-48" />
            <SkeletonBar className="mt-4 h-72 w-full" />
          </div>
        )}
      </div>
    </div>
  );
}

/** The frame with a geometry-matched skeleton in the content region. */
export function ShellSkeleton() {
  return <ShellFrame />;
}

export function useSidebar() {
  return useContext(SidebarState);
}

export { NAV, canReach, useMe };
