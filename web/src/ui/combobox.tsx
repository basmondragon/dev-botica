import { useEffect, useId, useRef, useState } from "react";
import { Check } from "lucide-react";
import { cn } from "./cn";
import { DropdownPanel, OPTION_ROW } from "./dropdown";
import {
  CONTROL_BASE,
  CONTROL_SIZES,
  ControlChevron,
  type ControlSize,
} from "./field";

export interface ComboboxOption {
  value: string;
  label: string;
  hint?: string;
}

/**
 * §B.5.4 · a searchable combobox, used wherever the collection is a catalog --
 * `items`, `manufacturers`, `suppliers`, `customers` -- because a droguería's
 * catalog is thousands of rows and a select is not a search. S1 is its first
 * consumer; it ships here so S1 does not author a second one.
 */
export function Combobox({
  id,
  ariaLabel,
  value,
  label,
  options,
  placeholder,
  searchPlaceholder = "Escriba para buscar",
  emptyLabel = "Nada coincide con esa búsqueda.",
  invalid,
  loading,
  onChange,
  onSearch,
  size = "md",
  className,
}: {
  id?: string;
  ariaLabel?: string;
  value: string;
  label?: string;
  options: readonly ComboboxOption[];
  placeholder: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
  invalid?: boolean;
  loading?: boolean;
  onChange: (next: string) => void;
  onSearch?: (term: string) => void;
  size?: ControlSize;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [active, setActive] = useState(0);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const listId = useId();
  const searchId = `${listId}-search`;
  const selectedOption = options.find((option) => option.value === value);
  const display = value ? (label ?? selectedOption?.label) : label;
  const needle = term.trim().toLocaleLowerCase();
  const visible = onSearch
    ? options
    : options.filter(
        (option) =>
          !needle ||
          option.label.toLocaleLowerCase().includes(needle) ||
          option.hint?.toLocaleLowerCase().includes(needle),
      );
  const optionId = (index: number) => `${listId}-option-${index}`;

  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    document
      .getElementById(optionId(active))
      ?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, active]);

  function close(restoreFocus = true) {
    setOpen(false);
    setTerm("");
    onSearch?.("");
    if (restoreFocus) triggerRef.current?.focus();
  }

  function show(fromEnd = false) {
    const selected = visible.findIndex((option) => option.value === value);
    setActive(
      selected >= 0 ? selected : fromEnd ? Math.max(0, visible.length - 1) : 0,
    );
    setOpen(true);
  }

  function commit(option: ComboboxOption) {
    onChange(option.value);
    close();
  }

  return (
    <div className={cn("relative min-w-0", className)}>
      <button
        id={id}
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-invalid={invalid || undefined}
        title={display ?? placeholder}
        onClick={() => (open ? close(false) : show())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (open) {
              searchRef.current?.focus();
              setActive((current) =>
                event.key === "ArrowDown"
                  ? Math.min(current + 1, visible.length - 1)
                  : Math.max(current - 1, 0),
              );
            } else {
              show(event.key === "ArrowUp");
            }
          }
        }}
        className={cn(
          CONTROL_BASE,
          "flex items-center text-left",
          CONTROL_SIZES[size].trigger,
          display ? "text-ink" : "text-ink-soft",
          invalid && "border-critical hover:border-critical",
        )}
      >
        <span className="min-w-0 truncate whitespace-nowrap">
          {display ?? placeholder}
        </span>
        <ControlChevron />
      </button>

      <DropdownPanel
        open={open}
        anchorRef={triggerRef}
        onClose={() => close(false)}
        className="min-h-0"
        maxHeight={320}
      >
        <div
          data-owns-escape=""
          className="flex min-h-0 flex-1 flex-col"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              event.preventDefault();
              close();
            } else if (event.key === "ArrowDown") {
              event.preventDefault();
              setActive((current) => Math.min(current + 1, visible.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActive((current) => Math.max(current - 1, 0));
            } else if (event.key === "Home") {
              event.preventDefault();
              setActive(0);
            } else if (event.key === "End") {
              event.preventDefault();
              setActive(Math.max(0, visible.length - 1));
            } else if (event.key === "Enter") {
              event.preventDefault();
              const option = visible[active];
              if (option) commit(option);
            }
          }}
        >
          <input
            id={searchId}
            ref={searchRef}
            value={term}
            placeholder={searchPlaceholder}
            role="combobox"
            aria-label={searchPlaceholder}
            aria-expanded
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={
              visible[active] ? optionId(active) : undefined
            }
            onChange={(event) => {
              setTerm(event.currentTarget.value);
              setActive(0);
              onSearch?.(event.currentTarget.value);
            }}
            className={cn(
              "mb-1.5 h-[34px] w-full rounded-control border border-edge bg-surface",
              "px-2.5 text-14 text-ink placeholder:text-ink-soft",
              "transition-[border-color] duration-140 ease-out",
            )}
          />
          <ul
            id={listId}
            role="listbox"
            aria-labelledby={searchId}
            className="min-h-0 overflow-y-auto overscroll-contain"
          >
            {visible.length === 0 ? (
              <li className="px-2.5 py-2.5 text-12 text-ink-label">
                {loading ? "Buscando…" : emptyLabel}
              </li>
            ) : (
              visible.map((option, index) => (
                <li key={option.value} role="none">
                  <button
                    id={optionId(index)}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={option.value === value}
                    title={option.label}
                    onPointerDown={(event) => event.preventDefault()}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => commit(option)}
                    className={cn(
                      OPTION_ROW,
                      index === active
                        ? "bg-hover-row text-ink"
                        : "text-ink-body",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate whitespace-nowrap">
                      {option.label}
                    </span>
                    {option.hint ? (
                      <span className="shrink-0 whitespace-nowrap text-11 text-ink-label">
                        {option.hint}
                      </span>
                    ) : null}
                    {option.value === value ? (
                      <Check
                        aria-hidden
                        className="size-3.5 shrink-0 text-ink"
                      />
                    ) : null}
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      </DropdownPanel>
    </div>
  );
}
