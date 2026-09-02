import {
  useEffect,
  useLayoutEffect,
  useRef,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "./cn";

const VIEWPORT_GUTTER = 8;
const ANCHOR_GAP = 4;
const CONTENT_WIDTH_LIMIT = 512;

/** §B.5.4 · option rows are 34px at `--radius-control`, `t-14`. */
export const OPTION_ROW =
  "flex min-h-[34px] w-full min-w-0 items-center gap-2.5 rounded-control " +
  "px-2.5 text-left text-14 transition-colors duration-140 ease-out " +
  "disabled:cursor-not-allowed disabled:text-ink-disabled";

/**
 * §B.2 · L3. Portalled above clipping containers, at least as wide as its
 * trigger, flipping above when there is more room there. Nothing translates and
 * nothing animates on entrance (§B.14).
 */
export function DropdownPanel({
  open,
  anchorRef,
  id,
  role,
  labelledBy,
  className,
  maxHeight = 320,
  onClose,
  children,
}: {
  open: boolean;
  anchorRef: RefObject<HTMLElement | null>;
  id?: string;
  role?: "listbox" | "menu";
  labelledBy?: string;
  className?: string;
  maxHeight?: number;
  onClose: () => void;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);

  function place() {
    const anchor = anchorRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;

    const rect = anchor.getBoundingClientRect();
    const availableWidth = Math.max(0, window.innerWidth - VIEWPORT_GUTTER * 2);

    panel.style.width = "max-content";
    panel.style.maxWidth = `${availableWidth}px`;
    panel.style.maxHeight = "none";

    const width = Math.min(
      availableWidth,
      Math.max(rect.width, Math.min(panel.scrollWidth, CONTENT_WIDTH_LIMIT)),
    );
    const left = Math.min(
      Math.max(VIEWPORT_GUTTER, rect.left),
      Math.max(VIEWPORT_GUTTER, window.innerWidth - VIEWPORT_GUTTER - width),
    );
    const below = Math.max(
      0,
      window.innerHeight - rect.bottom - ANCHOR_GAP - VIEWPORT_GUTTER,
    );
    const above = Math.max(0, rect.top - ANCHOR_GAP - VIEWPORT_GUTTER);
    const wanted = Math.min(panel.scrollHeight, maxHeight);
    const opensAbove = below < wanted && above > below;

    panel.style.left = `${left}px`;
    panel.style.width = `${width}px`;
    panel.style.maxHeight = `${Math.min(maxHeight, opensAbove ? above : below)}px`;
    panel.style.top = opensAbove ? "auto" : `${rect.bottom + ANCHOR_GAP}px`;
    panel.style.bottom = opensAbove
      ? `${window.innerHeight - rect.top + ANCHOR_GAP}px`
      : "auto";
    panel.style.visibility = "visible";
  }

  useLayoutEffect(() => {
    if (open) place();
  });

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node;
      if (
        !anchorRef.current?.contains(target) &&
        !panelRef.current?.contains(target)
      ) {
        onClose();
      }
    }

    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", onPointerDown);
    const observer =
      typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    if (anchorRef.current) observer?.observe(anchorRef.current);
    if (panelRef.current) observer?.observe(panelRef.current);

    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", onPointerDown);
      observer?.disconnect();
    };
  });

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={panelRef}
      id={id}
      role={role}
      aria-labelledby={labelledBy}
      className={cn(
        "fixed z-60 flex min-w-0 flex-col overflow-hidden rounded-card",
        "border border-edge-soft bg-surface p-1.5 shadow-overlay",
        className,
      )}
      style={{ visibility: "hidden" }}
      onClick={(event) => event.stopPropagation()}
    >
      {children}
    </div>,
    document.body,
  );
}
