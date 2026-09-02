import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "./cn";

/** §B.6.1 · four sizes at 30 · 34 · 40 · 52px. `sm` is the default. */
export type ButtonSize = "xs" | "sm" | "md" | "lg";
export type ButtonVariant = "primary" | "secondary" | "ghost" | "destructive";

const SIZES: Record<ButtonSize, string> = {
  xs: "h-[30px] px-3 text-12",
  sm: "h-[34px] px-4 text-12 font-medium",
  md: "h-10 px-5 text-14 font-medium",
  lg: "h-[52px] px-6 text-16 font-medium",
};

const ICON_ONLY: Record<ButtonSize, string> = {
  xs: "w-[30px] px-0",
  sm: "w-[34px] px-0",
  md: "w-10 px-0",
  lg: "w-[52px] px-0",
};

/**
 * §B.6.2 · the press returns to rest. Primary hovers *up* to `#000000` and
 * presses back down to `#171717`, so the direction of travel reverses under
 * the finger. Nothing translates: the plane holds its position, always.
 *
 * Destructive is a **bordered** variant, not a filled one -- a filled red
 * button beside `Cobrar` on a till is a mis-tap that voids a sale. The one
 * place it is filled is the confirm inside a confirmation dialog, where it is
 * the thing being confirmed and there is nothing beside it to hit by accident.
 */
const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-ink text-canvas hover:bg-hover-primary active:bg-ink " +
    "disabled:opacity-45 disabled:pointer-events-none",
  secondary:
    "border border-edge-strong bg-transparent text-ink " +
    "hover:border-hover-secondary active:bg-chrome " +
    "disabled:opacity-45 disabled:pointer-events-none",
  ghost:
    "bg-transparent text-ink-body hover:bg-hover-nav hover:text-ink " +
    "active:bg-active disabled:text-ink-disabled disabled:pointer-events-none",
  destructive:
    "border border-edge-critical bg-transparent text-critical " +
    "hover:border-critical hover:bg-tint-critical active:bg-tint-critical " +
    "disabled:opacity-45 disabled:pointer-events-none",
};

/** The one filled destructive in the product (§B.6.2). */
const CONFIRM =
  "bg-critical text-canvas border-critical hover:bg-critical " +
  "hover:border-critical active:bg-critical";

const BASE =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap " +
  "rounded-control font-[inherit] " +
  "transition-[background-color,border-color,color] duration-140 ease-out";

export function buttonClass({
  variant = "secondary",
  size = "sm",
  iconOnly,
  confirming,
  className,
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconOnly?: boolean;
  confirming?: boolean;
  className?: string;
} = {}) {
  return cn(
    BASE,
    SIZES[size],
    iconOnly && ICON_ONLY[size],
    VARIANTS[variant],
    variant === "destructive" && confirming && CONFIRM,
    className,
  );
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  iconOnly?: boolean;
  /** Marks this as the confirm inside a confirmation dialog. */
  confirming?: boolean;
  busy?: boolean;
  /** The present participle: `Guardando…`, `Entrando…`, `Enviando…`. */
  busyLabel?: string;
}

/**
 * §B.6.2 · busy. The label goes to its present participle with
 * `aria-busy="true"`, opacity unchanged. **This is the only spinner in the
 * entire product**, and it exists only inside a control the user has pressed.
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "secondary",
      size = "sm",
      iconOnly,
      confirming,
      busy,
      busyLabel,
      className,
      children,
      disabled,
      type = "button",
      ...rest
    },
    ref,
  ) {
    return (
      <button
        ref={ref}
        type={type}
        disabled={disabled || busy}
        aria-busy={busy || undefined}
        className={buttonClass({
          variant,
          size,
          iconOnly,
          confirming,
          className: cn(busy && "disabled:opacity-100", className),
        })}
        {...rest}
      >
        {busy ? (
          <>
            <span
              aria-hidden
              className="size-3.5 shrink-0 animate-spin rounded-pill border border-current border-t-transparent"
            />
            {busyLabel ?? children}
          </>
        ) : (
          children
        )}
      </button>
    );
  },
);
