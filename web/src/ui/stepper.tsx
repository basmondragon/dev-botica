import { useState } from "react";
import { Minus, Plus } from "lucide-react";
import { cn } from "./cn";

/**
 * §B.5.6 · the quantity stepper. Botica has two places a number is edited
 * against a proposal -- the `Sugerido` cell in Compras and a ticket line's
 * quantity at the counter -- and they are one control.
 *
 * Commit on `Enter` or blur; revert on `Esc`. A pending write shows its value
 * at `#909090` until the server confirms.
 *
 * §A.18.2 · at zero the cell recedes to the canvas fill and soft ink, which is
 * what the handoff draws on the two lines the model says not to order.
 */
export function QuantityStepper({
  value,
  onCommit,
  density = "desktop",
  pending,
  disabled,
  label,
  min = 0,
  max,
}: {
  value: number;
  onCommit: (next: number) => void;
  density?: "desktop" | "counter";
  pending?: boolean;
  disabled?: boolean;
  label: string;
  min?: number;
  max?: number;
}) {
  // A committed value that changes underneath the field replaces the draft.
  // This is React's own escape hatch for deriving state from props, and it is
  // one render rather than the two an effect would cost.
  const [draft, setDraft] = useState(String(value));
  const [committed, setCommitted] = useState(value);
  if (committed !== value) {
    setCommitted(value);
    setDraft(String(value));
  }

  function clamp(next: number) {
    if (Number.isNaN(next)) return value;
    if (next < min) return min;
    if (max !== undefined && next > max) return max;
    return next;
  }

  function commit(raw: string) {
    const next = clamp(Number.parseInt(raw, 10));
    setDraft(String(next));
    if (next !== value) onCommit(next);
  }

  const counter = density === "counter";
  const zero = value === 0;

  const step = (by: number) => commit(String(value + by));

  return (
    // Desktop: the `−` and `+` sit inside the field's own padding and appear on
    // hover or focus. `group` and `focus-within` are what reveal them, so the
    // cell is a bare figure at rest -- §A.18.2 draws no control on the resting
    // state, and a column of steppers is a picket fence.
    <span
      className={cn(
        "group relative inline-flex items-center",
        counter && "gap-1",
      )}
    >
      {counter ? (
        <StepButton
          label={`Restar una unidad de ${label}`}
          disabled={disabled || value <= min}
          onClick={() => step(-1)}
        >
          <Minus aria-hidden className="size-4" />
        </StepButton>
      ) : null}
      <input
        type="text"
        inputMode="numeric"
        aria-label={label}
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.currentTarget.value)}
        onBlur={(event) => commit(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            commit(event.currentTarget.value);
          } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            setDraft(String(value));
          }
        }}
        className={cn(
          "rounded-control border text-right tabular-nums",
          "transition-[border-color,background-color,color] duration-140 ease-out",
          counter
            ? "h-control-counter w-[60px] px-3 text-16"
            : "h-[34px] w-24 pl-8 pr-8 text-14",
          zero
            ? "border-edge-strong bg-canvas text-ink-soft"
            : "border-edge-strong bg-surface text-ink",
          pending && "text-ink-soft",
          disabled && "cursor-not-allowed bg-chrome text-ink-soft",
        )}
      />
      {counter ? (
        <StepButton
          label={`Sumar una unidad de ${label}`}
          disabled={disabled || (max !== undefined && value >= max)}
          onClick={() => step(1)}
        >
          <Plus aria-hidden className="size-4" />
        </StepButton>
      ) : (
        <>
          <InsetStep
            side="left"
            label={`Restar una unidad de ${label}`}
            disabled={disabled || value <= min}
            onClick={() => step(-1)}
          >
            <Minus aria-hidden className="size-3.5" />
          </InsetStep>
          <InsetStep
            side="right"
            label={`Sumar una unidad de ${label}`}
            disabled={disabled || (max !== undefined && value >= max)}
            onClick={() => step(1)}
          >
            <Plus aria-hidden className="size-3.5" />
          </InsetStep>
        </>
      )}
    </span>
  );
}

/**
 * §B.5.6 · the desktop pair: 24px ghost glyphs inside the field's own padding,
 * revealed on hover or focus. `opacity` rather than mounting, so nothing
 * reflows when the pointer arrives.
 */
function InsetStep({
  side,
  label,
  disabled,
  onClick,
  children,
}: {
  side: "left" | "right";
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      // The field commits on blur, and a mouse-down would blur it first.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      className={cn(
        "absolute top-1/2 flex size-6 -translate-y-1/2 items-center justify-center",
        "rounded-control text-ink-body opacity-0",
        "transition-[opacity,background-color,color] duration-140 ease-out",
        "hover:bg-hover-nav hover:text-ink",
        "group-hover:opacity-100 group-focus-within:opacity-100",
        "focus-visible:opacity-100",
        "disabled:pointer-events-none disabled:text-ink-disabled",
        side === "left" ? "left-1" : "right-1",
      )}
    >
      {children}
    </button>
  );
}

function StepButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex size-control-counter shrink-0 items-center justify-center rounded-control",
        "text-ink-body transition-colors duration-140 ease-out",
        "hover:bg-hover-nav hover:text-ink disabled:pointer-events-none disabled:text-ink-disabled",
      )}
    >
      {children}
    </button>
  );
}

/**
 * §A.18.2 + §B.5.6 · the read-only suggested-price cell S7 needs beside the
 * editable `Sugerido` cell. The suggested price is an analysis and the only
 * action on its row opens S1's price editor -- so this is not a stepper, and it
 * has no write path (A11).
 */
export function ReadOnlyCell({
  value,
  muted,
}: {
  value: string;
  muted?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-block rounded-control border border-hairline bg-chrome px-3 py-[5px]",
        "text-14 tabular-nums",
        muted ? "text-ink-soft" : "text-ink",
      )}
    >
      {value}
    </span>
  );
}
