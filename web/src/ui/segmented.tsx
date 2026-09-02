import { useRef } from "react";
import { cn } from "./cn";

export interface Segment<T extends string | number> {
  value: T;
  label: string;
}

/**
 * §A.15.3 · a 34px track at `--radius-control` on the chrome plane, holding
 * 28px segments at `--radius-segment`. The active segment lifts to the surface
 * plane with `--shadow-segment`.
 */
export function Segmented<T extends string | number>({
  value,
  segments,
  onChange,
  label,
  className,
}: {
  value: T;
  segments: readonly Segment<T>[];
  onChange: (next: T) => void;
  label: string;
  className?: string;
}) {
  const track = useRef<HTMLDivElement | null>(null);

  function onKeyDown(event: React.KeyboardEvent) {
    const step =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (!step) return;
    event.preventDefault();
    const index = segments.findIndex((segment) => segment.value === value);
    const next = segments[(index + step + segments.length) % segments.length];
    if (!next) return;
    onChange(next.value);
    requestAnimationFrame(() => {
      track.current
        ?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
        ?.focus();
    });
  }

  return (
    <div
      ref={track}
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cn(
        "inline-flex h-[34px] items-center gap-0.5 rounded-control bg-chrome p-[3px]",
        className,
      )}
    >
      {segments.map((segment) => {
        const active = segment.value === value;
        return (
          <button
            key={segment.value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(segment.value)}
            className={cn(
              "h-7 rounded-segment px-3.5 text-12",
              "transition-[background-color,color] duration-140 ease-out",
              active
                ? "bg-surface font-medium text-ink shadow-segment"
                : "text-ink-body hover:text-ink",
            )}
          >
            {segment.label}
          </button>
        );
      })}
    </div>
  );
}
