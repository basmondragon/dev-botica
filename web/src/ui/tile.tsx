import type { ReactNode } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { cn } from "./cn";
import { SkeletonBar } from "./states";

/**
 * §A.19.1 · the KPI card. The label carries `min-height:32px` so a two-line
 * label and a one-line label align their figures.
 *
 * **The delta is never coloured.** `▲ 6,4%` on rising sales and `▼ 41%` on
 * falling stock-outs are both 11px `#6b6b6b`: a direction is not a status, and
 * the arrow says which way it went (§B.12.3).
 */
export function Tile({
  label,
  figure,
  reference,
  footnote,
  delta,
  badge,
  progress,
}: {
  label: string;
  figure: ReactNode;
  reference?: string;
  footnote?: string;
  delta?: { direction: "up" | "down"; label: string };
  badge?: ReactNode;
  progress?: { fill: number; target?: number };
}) {
  const Arrow = delta?.direction === "down" ? ArrowDown : ArrowUp;
  return (
    <div className="rounded-card border border-edge-soft bg-surface p-4 shadow-plane">
      <p className="min-h-8 text-12 text-ink-label">{label}</p>
      <p className="mt-3 flex items-baseline gap-2">
        <span className="text-36 tracking-display tabular-nums text-ink">
          {figure}
        </span>
        {reference ? (
          <span className="text-12 text-ink-label">{reference}</span>
        ) : null}
        {badge}
      </p>
      {progress ? <ProgressBar {...progress} /> : null}
      {footnote || delta ? (
        <p className="mt-1.5 flex items-center gap-[3px] text-11/[18px] text-ink-note">
          {delta ? (
            <Arrow aria-hidden strokeWidth={2} className="size-3 shrink-0" />
          ) : null}
          {delta?.label}
          {delta && footnote ? " " : null}
          {footnote}
        </p>
      ) : null}
    </div>
  );
}

/** §A.19.1 · the reference-and-progress variant: a 6px rail with an optional
 *  2px target marker at `#909090`. */
export function ProgressBar({
  fill,
  target,
}: {
  fill: number;
  target?: number;
}) {
  return (
    <div className="relative mt-3.5 h-1.5 w-full rounded-pill bg-data-track">
      <div
        className="h-1.5 rounded-pill bg-data-90"
        style={{ width: `${Math.max(0, Math.min(100, fill))}%` }}
      />
      {target !== undefined ? (
        <span
          aria-hidden
          className="absolute -top-1 -bottom-1 w-0.5 bg-ink-soft"
          style={{ left: `calc(${target}% - 1px)` }}
        />
      ) : null}
    </div>
  );
}

export function TileSkeleton() {
  return (
    <div className="rounded-card border border-edge-soft bg-surface p-4 shadow-plane">
      <SkeletonBar className="h-3 w-[40%]" />
      <SkeletonBar className="mt-3 h-9 w-[55%]" />
    </div>
  );
}

/**
 * §A.18.1 · the in-cell stock bar: a 56 × 4 rail on `--data-track` with its
 * fill coloured from the series' own normalisation, and **its mandatory
 * figure** at `min-width:44px`, right-aligned. The bar never appears without
 * its number, and a zero draws no fill at all (§B.12.3).
 */
export function StockBar({
  fill,
  figure,
  label,
}: {
  /** 0–100, already normalised within this column's own series (§B.12.2). */
  fill: number;
  figure: string;
  label?: string;
}) {
  const clamped = Math.max(0, Math.min(100, fill));
  return (
    <span className="inline-flex items-center gap-2" aria-label={label}>
      <span
        aria-hidden
        className="inline-block h-1 w-14 shrink-0 rounded-pill bg-data-track align-[3px]"
      >
        {clamped > 0 ? (
          <span
            className="block h-1 rounded-pill"
            style={{ width: `${clamped}%`, backgroundColor: rampStep(clamped) }}
          />
        ) : null}
      </span>
      <span className="inline-block min-w-11 text-right text-14 tabular-nums text-ink-body">
        {figure}
      </span>
    </span>
  );
}

/**
 * §A.5 + §B.12.2 · the eleven-step ramp, read at the position a value takes
 * **within its own series**: the largest to `--data-100`, the smallest non-zero
 * to `--data-10`. Values between two steps are mixed at read time rather than
 * snapped, so the ramp is continuous rather than a ten-value palette -- but the
 * endpoints are the drawn steps and not a global threshold function.
 */
export const RAMP = [
  "#9ec9f4",
  "#87bff1",
  "#7fb9f0",
  "#6cb0ef",
  "#5fa8ed",
  "#4c9bea",
  "#3389e6",
  "#2683e5",
  "#1a7fe5",
  "#0071e3",
] as const;

export function rampStep(share: number): string {
  const index = Math.round(
    (Math.max(0, Math.min(100, share)) / 100) * (RAMP.length - 1),
  );
  return RAMP[index] ?? RAMP[RAMP.length - 1]!;
}

/** §B.12.2 · normalise a series onto the ramp before it reaches a bar. */
export function normalise(values: number[]): number[] {
  const largest = Math.max(0, ...values);
  if (largest === 0) return values.map(() => 0);
  return values.map((value) => (Math.max(0, value) / largest) * 100);
}

export function cellClass(...classes: string[]) {
  return cn(...classes);
}
