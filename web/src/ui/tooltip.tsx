import { useEffect, useId, useRef, useState } from "react";
import { cn } from "./cn";

const OPEN_DELAY = 120;

/**
 * A tooltip on a control that already has an accessible name. It is a hint, not
 * a place to put information a reader needs: §B.4.1 says a truncated cell's
 * full value is in the row's detail panel, never in a `title` attribute alone.
 */
export function Tooltip({
  label,
  text,
  children,
  className,
}: {
  label: string;
  text: string;
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tipId = useId();

  function clear() {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }

  useEffect(() => clear, []);

  return (
    <span className={cn("relative inline-flex", className)}>
      <span
        aria-describedby={open ? tipId : undefined}
        aria-label={label}
        onPointerEnter={(event) => {
          if (event.pointerType !== "mouse") return;
          clear();
          timer.current = setTimeout(() => setOpen(true), OPEN_DELAY);
        }}
        onPointerLeave={() => {
          clear();
          setOpen(false);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {children}
      </span>
      {open ? (
        <span
          id={tipId}
          role="tooltip"
          className={cn(
            "absolute left-0 top-[calc(100%+6px)] z-40 w-max max-w-[260px]",
            "rounded-control border border-edge-soft bg-surface px-2.5 py-1.5",
            "text-12 text-ink-body shadow-overlay",
          )}
        >
          {text}
        </span>
      ) : null}
    </span>
  );
}
