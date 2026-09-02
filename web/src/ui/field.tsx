import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";
import { Check, ChevronDown, Minus } from "lucide-react";
import { cn } from "./cn";

/**
 * §B.5.2 · the text input, at the search field's own drawn geometry (§A.15.2).
 * Disabled takes a fill and a colour change and **never `opacity`** -- opacity
 * on an input fades its label and its value with it. Read-only is told apart
 * from disabled by keeping full-strength text.
 */
export const CONTROL_BASE =
  "w-full rounded-control border border-edge bg-surface text-ink " +
  "placeholder:text-ink-soft transition-[border-color] duration-140 ease-out " +
  "hover:border-edge-strong " +
  "disabled:bg-chrome disabled:text-ink-soft disabled:border-hairline " +
  "disabled:cursor-not-allowed disabled:hover:border-hairline " +
  "read-only:bg-chrome read-only:text-ink read-only:border-hairline";

const INVALID = "border-critical hover:border-critical";

export type ControlSize = "sm" | "md";

export const CONTROL_SIZES: Record<
  ControlSize,
  { field: string; trigger: string }
> = {
  sm: { field: "h-7 px-2 text-12", trigger: "h-7 pl-2 pr-7 text-12" },
  md: { field: "h-[34px] px-3 text-14", trigger: "h-[34px] pl-3 pr-8 text-14" },
};

export function ControlChevron() {
  return (
    <ChevronDown
      aria-hidden
      strokeWidth={2}
      className="pointer-events-none absolute right-3 top-1/2 size-3 -translate-y-1/2 text-ink-soft"
    />
  );
}

/**
 * §B.5.7 · label, help and error. Help and error occupy the same slot and the
 * error replaces the help, so validating shifts no layout. Required fields
 * carry the word `Obligatorio` in the help slot, never an asterisk.
 */
export function Field({
  label,
  help,
  error,
  required,
  optional,
  children,
  htmlFor,
  className,
}: {
  label: string;
  help?: string;
  error?: string;
  required?: boolean;
  optional?: boolean;
  children: ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  const hint =
    error ??
    help ??
    (required ? "Obligatorio" : optional ? "Opcional" : undefined);
  return (
    <div className={cn("flex flex-col", className)}>
      <label htmlFor={htmlFor} className="mb-2 block text-12 text-ink-label">
        {label}
      </label>
      {children}
      {hint ? (
        <p
          className={cn(
            "mt-1.5",
            error ? "text-12 text-critical" : "text-11 text-ink-soft",
          )}
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export interface InputProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "size"
> {
  invalid?: boolean;
  size?: ControlSize;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, invalid, size = "md", ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        CONTROL_BASE,
        CONTROL_SIZES[size].field,
        invalid && INVALID,
        className,
      )}
      {...rest}
    />
  );
});

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

/** §B.5.3 · `t-16` because a textarea holds prose. */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea({ className, invalid, rows = 3, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        rows={rows}
        aria-invalid={invalid || undefined}
        className={cn(
          CONTROL_BASE,
          "resize-none px-3 py-2.5 text-16",
          invalid && INVALID,
          className,
        )}
        {...rest}
      />
    );
  },
);

/**
 * §B.5.5 · 18px box at `--radius-check`, which is `--radius-mark`, the brand
 * square's own radius. Nothing new is introduced: the handoff's radius scale
 * already reaches 4. The hit target is 34px square via the wrapping label
 * (44px at counter density), regardless of the 18px box.
 */
export function Checkbox({
  checked,
  indeterminate,
  onChange,
  disabled,
  label,
  className,
  density = "desktop",
  ...rest
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
  className?: string;
  density?: "desktop" | "counter";
} & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "checked" | "onChange" | "type" | "size"
>) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className={cn(
        "inline-flex shrink-0 cursor-pointer items-center justify-center",
        density === "counter" ? "size-control-counter" : "size-[34px]",
        label && "w-auto gap-2.5 pr-2.5",
        disabled && "cursor-not-allowed",
        className,
      )}
    >
      <span className="relative inline-flex">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          aria-checked={indeterminate ? "mixed" : checked}
          onChange={(event) => onChange(event.currentTarget.checked)}
          className={cn(
            "peer size-[18px] shrink-0 appearance-none rounded-check border bg-surface",
            "transition-[background-color,border-color] duration-140 ease-out",
            checked || indeterminate
              ? "border-ink bg-ink"
              : "border-edge-strong hover:border-ink",
            "disabled:border-hairline disabled:bg-chrome",
          )}
          {...rest}
        />
        {indeterminate ? (
          <Minus
            aria-hidden
            className={cn(
              "pointer-events-none absolute inset-0 m-auto h-[1.5px] w-2.5",
              disabled ? "text-ink-disabled" : "text-canvas",
            )}
          />
        ) : checked ? (
          <Check
            aria-hidden
            className={cn(
              "pointer-events-none absolute inset-0 m-auto size-3",
              disabled ? "text-ink-disabled" : "text-canvas",
            )}
          />
        ) : null}
      </span>
      {label ? <span className="text-14 text-ink">{label}</span> : null}
    </label>
  );
}

/**
 * §B.5.5 · a radio group is for two to four mutually exclusive options that
 * must all be visible; anything longer is a select.
 */
export function RadioGroup({
  legend,
  name,
  value,
  options,
  onChange,
  className,
}: {
  legend: string;
  name: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (next: string) => void;
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={legend}
      className={cn("flex flex-col gap-2", className)}
    >
      <span className="text-12 text-ink-label">{legend}</span>
      {options.map((option) => (
        <label
          key={option.value}
          className="inline-flex cursor-pointer items-center gap-2.5"
        >
          <span className="relative inline-flex size-[34px] items-center justify-center">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className={cn(
                "size-[18px] shrink-0 appearance-none rounded-pill border bg-surface",
                "transition-[background-color,border-color] duration-140 ease-out",
                value === option.value
                  ? "border-ink bg-ink"
                  : "border-edge-strong hover:border-ink",
              )}
            />
            {value === option.value ? (
              <span
                aria-hidden
                className="pointer-events-none absolute size-2 rounded-pill bg-canvas"
              />
            ) : null}
          </span>
          <span className="text-14 text-ink">{option.label}</span>
        </label>
      ))}
    </div>
  );
}
