import { useRef, useState } from "react";
import { Link, useLocation } from "@tanstack/react-router";
import { ChevronDown } from "lucide-react";
import { cn } from "@/ui/cn";
import { DropdownPanel, OPTION_ROW } from "@/ui/dropdown";

/**
 * §B.8.1 · **the `Inventario` module is one nav item and five routes**, and this
 * is how a person moves between them.
 *
 * The nav is a flat list at its seven-item ceiling, and an eighth item in a list
 * a cashier uses all day costs attention every day to save an administrator a
 * click a month. The header breadcrumb's first segment is already drawn at 12px
 * `#727272` (§A.13.2); this gives it a job.
 *
 * The alternative -- a segmented control in the header's right slot -- was
 * considered and not taken: the drawn header on Existencias already carries two
 * buttons, and a third control would crowd them.
 */
export const ROUTES = [
  { to: "/inventory", label: "Existencias" },
  { to: "/inventory/catalog", label: "Catálogo" },
  { to: "/inventory/receive", label: "Cargar mercancía" },
  { to: "/inventory/transfers", label: "Traslados" },
  { to: "/inventory/counts", label: "Conteos" },
] as const;

export function InventoryBreadcrumb({ trail }: { trail?: string }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const location = useLocation();
  const current =
    [...ROUTES]
      .sort((a, b) => b.to.length - a.to.length)
      .find((route) => location.pathname.startsWith(route.to)) ?? ROUTES[0];

  return (
    <p className="flex shrink-0 items-center gap-1 text-12 text-ink-label">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((current) => !current)}
        className={cn(
          "inline-flex items-center gap-1 rounded-control px-1 py-0.5",
          "transition-colors duration-140 ease-out hover:text-ink",
        )}
      >
        Inventario
        <ChevronDown aria-hidden strokeWidth={2} className="size-3" />
      </button>
      <span className="text-ink-disabled">/</span>
      {trail ? (
        <>
          <span>{trail}</span>
          <span className="text-ink-disabled">/</span>
        </>
      ) : null}
      <DropdownPanel
        open={open}
        anchorRef={triggerRef}
        role="menu"
        onClose={() => setOpen(false)}
      >
        {ROUTES.map((route) => (
          <Link
            key={route.to}
            to={route.to}
            search={{}}
            role="menuitem"
            onClick={() => setOpen(false)}
            className={cn(
              OPTION_ROW,
              "hover:bg-hover-row",
              route.to === current.to
                ? "font-medium text-ink"
                : "text-ink-body",
            )}
          >
            {route.label}
          </Link>
        ))}
      </DropdownPanel>
    </p>
  );
}
