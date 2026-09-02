import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Search } from "lucide-react";
import { cn } from "./cn";

/**
 * §A.13.3 · the 52px filter bar. Sticky under the header, and it renders the
 * URL's typed search params rather than local state: a filter the URL does not
 * carry is not set, and clearing the bar clears the params.
 *
 * Its right slot is the provenance line. §B.9's sync state goes there on any
 * surface a till touches -- **S0 ships the slot and renders nothing in it**. A
 * hardcoded `Sincronizado` on a build with no sync is the worst string in the
 * product.
 */
export function FilterBar({
  children,
  provenance,
  replacedBy,
}: {
  children: ReactNode;
  provenance?: ReactNode;
  replacedBy?: ReactNode;
}) {
  return (
    <div className="sticky top-16 z-20 h-13 shrink-0 border-b border-hairline bg-canvas">
      <div
        role="group"
        aria-label="Filtros"
        className={cn(
          "surface-scroll h-full overflow-x-auto",
          replacedBy && "invisible",
        )}
      >
        <div className="flex h-full min-w-max items-center gap-2.5 px-10">
          {children}
          <span
            data-provenance-slot
            className="ml-auto shrink-0 pl-4 text-11 text-ink-label"
          >
            {provenance}
          </span>
        </div>
      </div>
      {replacedBy}
    </div>
  );
}

/** §A.15.2 · the 34px search field with its 15px glass at `#909090`. */
export function SearchField({
  value,
  placeholder,
  onChange,
  inputRef,
}: {
  value: string;
  placeholder: string;
  onChange: (next: string) => void;
  inputRef?: React.RefObject<HTMLInputElement | null>;
}) {
  return (
    <div className="flex h-[34px] min-w-[250px] items-center gap-2 rounded-control border border-edge bg-surface pl-3 pr-2.5">
      <Search
        aria-hidden
        strokeWidth={1.5}
        className="size-[15px] shrink-0 text-ink-soft"
      />
      <input
        ref={inputRef}
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(event) => onChange(event.currentTarget.value)}
        className="min-w-0 flex-1 bg-transparent text-12 text-ink outline-none placeholder:text-ink-soft"
      />
    </div>
  );
}

/**
 * §A.15.1 · a filter chip. Active carries a border and a value pill; inactive
 * carries neither border nor fill. `Sede · Todas · 6`.
 */
export function FilterChip({
  label,
  value,
  children,
}: {
  label: string;
  value?: string;
  children?: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelId = useId();
  const set = value !== undefined && value !== "";

  function close(restoreFocus = true) {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          "inline-flex h-[34px] items-center rounded-control text-12 font-medium",
          "transition-[background-color,border-color,color] duration-140 ease-out",
          set
            ? "border border-edge-strong pl-3.5 pr-1.5 text-ink gap-2"
            : "gap-1.5 px-3.5 text-ink-body hover:text-ink",
        )}
      >
        {label}
        {set ? (
          <span className="flex h-5 items-center rounded-pill bg-chrome px-2 text-11 font-normal text-ink-body">
            {value}
          </span>
        ) : null}
      </button>
      {open && children ? (
        <div
          id={panelId}
          data-owns-escape=""
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              event.preventDefault();
              close();
            }
          }}
          className="absolute left-0 top-[calc(100%+4px)] z-40 w-64 rounded-card border border-edge-soft bg-surface p-4 shadow-overlay"
        >
          {/* eslint-disable-next-line react-hooks/refs */}
          {children(close)}
        </div>
      ) : null}
    </div>
  );
}
