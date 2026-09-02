import { cn } from "./cn";
import { ProgressBar, normalise, rampStep } from "./tile";

/**
 * §B.12.5 · four forms, all drawn, and they are the whole inventory. A new
 * report picks one of these four. No pie chart, no stacked area, no dual axis,
 * no legend, no gridline, and **no chart animation** -- a chart that animates in
 * is a chart the reader waits for.
 *
 * §B.12.3 · every figure a chart draws is printed as text in the same card, and
 * the whole series is in the figure's `aria-label`. Depth is always redundant
 * with geometry, and a zero draws no fill.
 */

export interface Point {
  label: string;
  value: number;
  /** The figure as the §A.11 formatter renders it. */
  display: string;
}

/** §A.20 · the ranked bar list: a `dl` at 76 · rail · 46. */
export function RankedBars({
  points,
  label,
}: {
  points: Point[];
  label: string;
}) {
  const shares = normalise(points.map((point) => point.value));
  return (
    <dl aria-label={label} className="flex flex-col gap-3">
      {points.map((point, index) => (
        <div key={point.label} className="flex items-center gap-3">
          <dt className="w-19 shrink-0 truncate text-11 text-ink-label">
            {point.label}
          </dt>
          <span aria-hidden className="h-1.5 flex-1 rounded-pill bg-data-track">
            {(shares[index] ?? 0) > 0 ? (
              <span
                className="block h-1.5 rounded-pill"
                style={{
                  width: `${shares[index]}%`,
                  backgroundColor: rampStep(shares[index] ?? 0),
                }}
              />
            ) : null}
          </span>
          <dd className="w-[46px] shrink-0 text-right text-11 tabular-nums text-ink-body">
            {point.display}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** §A.20 · the column histogram: `gap:1px`, a 120px floor, its axis beneath. */
export function Histogram({
  points,
  label,
  axis,
}: {
  points: Point[];
  label: string;
  axis?: [string, string];
}) {
  const shares = normalise(points.map((point) => point.value));
  return (
    <figure aria-label={label} className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-[120px] flex-1 items-end gap-px border-b border-hairline">
        {points.map((point, index) => (
          <span
            key={point.label}
            title={`${point.label} · ${point.display}`}
            className="flex-1"
            style={{
              height: `${shares[index] ?? 0}%`,
              backgroundColor: rampStep(shares[index] ?? 0),
            }}
          />
        ))}
      </div>
      {axis ? (
        <figcaption className="mt-2 flex justify-between text-11 tabular-nums text-ink-label">
          <span>{axis[0]}</span>
          <span>{axis[1]}</span>
        </figcaption>
      ) : null}
    </figure>
  );
}

/** §A.20 · the donut: 64px, r 28.5, `stroke-width:7`, its arc at `--data-100`. */
export function Donut({
  share,
  figure,
  caption,
  label,
}: {
  /** 0–100. */
  share: number;
  figure: string;
  caption: string;
  label: string;
}) {
  const circumference = 2 * Math.PI * 28.5;
  const arc = (Math.max(0, Math.min(100, share)) / 100) * circumference;
  return (
    <div className="flex items-center gap-4" aria-label={label}>
      <svg
        aria-hidden
        viewBox="0 0 64 64"
        className="size-16 shrink-0 -rotate-90"
      >
        <circle
          cx="32"
          cy="32"
          r="28.5"
          fill="none"
          stroke="var(--color-data-track)"
          strokeWidth="7"
        />
        {arc > 0 ? (
          <circle
            cx="32"
            cy="32"
            r="28.5"
            fill="none"
            stroke="var(--color-data-100)"
            strokeWidth="7"
            strokeLinecap="round"
            strokeDasharray={`${arc} ${circumference - arc}`}
          />
        ) : null}
      </svg>
      <div className="min-w-0">
        <p className="text-28 tracking-display tabular-nums text-ink">
          {figure}
        </p>
        <p className="mt-1 text-11 text-ink-note">{caption}</p>
      </div>
    </div>
  );
}

/** §A.19.1 · progress with an optional target marker. */
export function Progress({
  fill,
  target,
  className,
}: {
  fill: number;
  target?: number;
  className?: string;
}) {
  return (
    <div className={cn(className)}>
      <ProgressBar fill={fill} target={target} />
    </div>
  );
}
