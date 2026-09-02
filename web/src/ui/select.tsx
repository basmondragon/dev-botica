import {
  forwardRef,
  useEffect,
  useId,
  useImperativeHandle,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type KeyboardEvent,
} from "react";
import { Check } from "lucide-react";
import { cn } from "./cn";
import { DropdownPanel, OPTION_ROW } from "./dropdown";
import {
  CONTROL_BASE,
  CONTROL_SIZES,
  ControlChevron,
  type ControlSize,
} from "./field";

export interface SelectOption {
  value: string | number;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "children" | "onChange" | "size" | "value"
> {
  value: string | number;
  options: readonly SelectOption[];
  onValueChange: (value: string) => void;
  invalid?: boolean;
  /** When the value is optional the placeholder is a real option, so a choice
   *  can be cleared without resetting the form (§B.5.4). */
  placeholder?: string;
  size?: ControlSize;
  containerClassName?: string;
}

function enabledIndex(
  options: readonly SelectOption[],
  start: number,
  direction: 1 | -1,
) {
  for (let offset = 0; offset < options.length; offset += 1) {
    const index =
      (start + offset * direction + options.length) % options.length;
    if (!options[index]?.disabled) return index;
  }
  return -1;
}

/** §B.5.4 · arrow keys, Home, End, typeahead, Enter, Escape and focus
 *  restoration are all required. */
export const Select = forwardRef<HTMLButtonElement, SelectProps>(
  function Select(
    {
      id,
      value,
      options,
      onValueChange,
      invalid,
      placeholder,
      size = "md",
      disabled,
      className,
      containerClassName,
      onClick,
      onKeyDown,
      ...rest
    },
    forwardedRef,
  ) {
    const [open, setOpen] = useState(false);
    const [active, setActive] = useState(-1);
    const triggerRef = useRef<HTMLButtonElement | null>(null);
    const typeahead = useRef("");
    const typeaheadTimer = useRef<number | undefined>(undefined);
    const generatedId = useId();
    const triggerId = id ?? `${generatedId}-trigger`;
    const listId = `${generatedId}-listbox`;
    const stringValue = String(value);
    const menuOptions: readonly SelectOption[] = placeholder
      ? [{ value: "", label: placeholder }, ...options]
      : options;
    const selectedIndex = menuOptions.findIndex(
      (option) => String(option.value) === stringValue,
    );
    const selected =
      selectedIndex >= 0 ? menuOptions[selectedIndex] : undefined;
    const display = selected?.label ?? placeholder ?? "Seleccione";
    const optionId = (index: number) => `${generatedId}-option-${index}`;

    useImperativeHandle(forwardedRef, () => triggerRef.current!, []);

    useEffect(() => {
      if (disabled) setOpen(false);
    }, [disabled]);

    useEffect(() => () => window.clearTimeout(typeaheadTimer.current), []);

    useEffect(() => {
      if (!open || active < 0) return;
      document
        .getElementById(optionId(active))
        ?.scrollIntoView({ block: "nearest" });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active, open]);

    function initialIndex(direction: 1 | -1 = 1) {
      if (selectedIndex >= 0 && !menuOptions[selectedIndex]?.disabled)
        return selectedIndex;
      return enabledIndex(
        menuOptions,
        direction === 1 ? 0 : menuOptions.length - 1,
        direction,
      );
    }

    function show(direction: 1 | -1 = 1, fromEdge = false) {
      setActive(
        fromEdge
          ? enabledIndex(
              menuOptions,
              direction === 1 ? 0 : menuOptions.length - 1,
              direction,
            )
          : initialIndex(direction),
      );
      setOpen(true);
    }

    function close(restoreFocus = false) {
      setOpen(false);
      if (restoreFocus) triggerRef.current?.focus();
    }

    function commit(option: SelectOption | undefined) {
      if (!option || option.disabled) return;
      onValueChange(String(option.value));
      close(true);
    }

    function move(direction: 1 | -1) {
      if (menuOptions.length === 0) return;
      const start = active < 0 ? initialIndex(direction) : active + direction;
      setActive(enabledIndex(menuOptions, start, direction));
    }

    function findByTypeahead(key: string) {
      window.clearTimeout(typeaheadTimer.current);
      typeahead.current += key.toLocaleLowerCase();
      typeaheadTimer.current = window.setTimeout(() => {
        typeahead.current = "";
      }, 500);
      const start = active < 0 ? 0 : active + 1;
      for (let offset = 0; offset < menuOptions.length; offset += 1) {
        const index = (start + offset) % menuOptions.length;
        const option = menuOptions[index];
        if (!option || option.disabled) continue;
        if (option.label.toLocaleLowerCase().startsWith(typeahead.current)) {
          setActive(index);
          return;
        }
      }
    }

    function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
      onKeyDown?.(event);
      if (event.defaultPrevented) return;

      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (open) move(event.key === "ArrowDown" ? 1 : -1);
        else show(event.key === "ArrowDown" ? 1 : -1, true);
      } else if (event.key === "Home" && open) {
        event.preventDefault();
        setActive(enabledIndex(menuOptions, 0, 1));
      } else if (event.key === "End" && open) {
        event.preventDefault();
        setActive(enabledIndex(menuOptions, menuOptions.length - 1, -1));
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (open) commit(menuOptions[active]);
        else show();
      } else if (event.key === "Escape" && open) {
        event.preventDefault();
        event.stopPropagation();
        close(true);
      } else if (event.key === "Tab") {
        close();
      } else if (
        event.key.length === 1 &&
        !event.altKey &&
        !event.ctrlKey &&
        !event.metaKey
      ) {
        if (!open) show();
        findByTypeahead(event.key);
      }
    }

    return (
      <div className={cn("relative min-w-0", containerClassName)}>
        <button
          {...rest}
          id={triggerId}
          ref={triggerRef}
          type="button"
          role="combobox"
          data-owns-escape={open ? "" : undefined}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          aria-activedescendant={
            open && active >= 0 ? optionId(active) : undefined
          }
          aria-invalid={invalid || undefined}
          onClick={(event) => {
            onClick?.(event);
            if (event.defaultPrevented) return;
            if (open) close();
            else show();
          }}
          onKeyDown={handleKeyDown}
          className={cn(
            CONTROL_BASE,
            "flex items-center text-left",
            CONTROL_SIZES[size].trigger,
            !selected || stringValue === "" ? "text-ink-soft" : "text-ink",
            invalid && "border-critical hover:border-critical",
            className,
          )}
          title={display}
        >
          <span className="min-w-0 flex-1 truncate whitespace-nowrap">
            {display}
          </span>
          <ControlChevron />
        </button>

        <DropdownPanel
          open={open}
          anchorRef={triggerRef}
          id={listId}
          role="listbox"
          labelledBy={triggerId}
          onClose={() => close()}
          className="overflow-y-auto overscroll-contain"
        >
          <div className="min-w-0">
            {menuOptions.length === 0 ? (
              <div className="px-2.5 py-2 text-12 text-ink-label">
                No hay opciones disponibles.
              </div>
            ) : (
              menuOptions.map((option, index) => {
                const optionValue = String(option.value);
                const chosen = optionValue === stringValue;
                return (
                  <button
                    key={`${optionValue}-${index}`}
                    id={optionId(index)}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    disabled={option.disabled}
                    aria-selected={chosen}
                    title={option.label}
                    onPointerDown={(event) => event.preventDefault()}
                    onMouseEnter={() => {
                      if (!option.disabled) setActive(index);
                    }}
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
                    {chosen ? (
                      <Check
                        aria-hidden
                        className="size-3.5 shrink-0 text-ink"
                      />
                    ) : null}
                  </button>
                );
              })
            )}
          </div>
        </DropdownPanel>
      </div>
    );
  },
);
