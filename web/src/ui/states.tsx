import type { ReactNode } from "react";
import { Button } from "./button";
import { cn } from "./cn";

/**
 * §B.10.1 · a skeleton reproduces the geometry it replaces. If the skeleton and
 * the loaded state differ in height, the skeleton is wrong -- a table that grows
 * 200px when it resolves has thrown away the reader's place.
 *
 * **No spinners.** The one exception in the entire product is the 14px inline
 * indicator inside a button the user has already pressed (§B.6.2).
 */
export function SkeletonBar({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      aria-hidden
      style={style}
      className={cn("skeleton block", className)}
    />
  );
}

/** Bar heights, per §B.10.1: one per type step it stands in for. */
export const BAR = {
  caption: "h-2.5",
  label: "h-3",
  cell: "h-3.5",
  heading: "h-5",
  control: "h-[34px]",
} as const;

export function SkeletonRows({
  rows = 8,
  columns,
  rowHeight = "h-12",
  padding = "px-[22px]",
}: {
  rows?: number;
  columns: string[];
  rowHeight?: string;
  padding?: string;
}) {
  return (
    <>
      {Array.from({ length: rows }, (_, rowIndex) => (
        <tr
          key={rowIndex}
          aria-hidden
          className={cn("border-b border-hairline last:border-b-0", rowHeight)}
        >
          {columns.map((width, columnIndex) => {
            // Vary widths per row by ±20% so the block does not read as a grid.
            const jitter =
              0.8 + (((rowIndex * 7 + columnIndex * 3) % 5) / 4) * 0.4;
            return (
              <td key={columnIndex} className={cn("align-middle", padding)}>
                <SkeletonBar
                  className={BAR.cell}
                  style={{ width: `calc(${width} * ${jitter.toFixed(2)})` }}
                />
              </td>
            );
          })}
        </tr>
      ))}
    </>
  );
}

/**
 * §B.10.2 · three kinds, and conflating them is the defect.
 *
 * `never-populated` names what fills it and where that happens, with a primary.
 * `filtered` echoes the active filters back and offers `Quitar filtros` as a
 * **secondary**, because the intent was to filter. `deliberate` carries no
 * action, because there is nothing for a reader to do about it.
 *
 * An empty state whose body is `Sin datos` is a defect.
 */
export function EmptyState({
  kind = "never-populated",
  title,
  body,
  actionLabel,
  onAction,
}: {
  kind?: "never-populated" | "filtered" | "deliberate";
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="mx-auto flex max-w-[420px] flex-col items-center py-12 text-center">
      <p className="text-16 text-ink">{title}</p>
      <p className="mt-2 text-14 text-ink-body">{body}</p>
      {kind !== "deliberate" && actionLabel && onAction ? (
        <Button
          size="md"
          variant={kind === "filtered" ? "secondary" : "primary"}
          className="mt-5"
          onClick={onAction}
        >
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}

/**
 * §B.10.3 · route scope. The empty-state geometry, a retry, and a
 * `user-select:all` correlation id at `t-10` mono.
 *
 * Every error names the operation, the entity and the recovery. `Error`,
 * `Ocurrió un error` and `Falló la solicitud` are prohibited, as is any raw
 * exception or vendor payload rendered to a user.
 */
export function RouteError({
  title,
  detail,
  requestId,
  onRetry,
  retryLabel = "Reintentar",
}: {
  title: string;
  detail: string;
  requestId?: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="mx-auto flex max-w-[420px] flex-col items-center py-12 text-center">
      <p className="text-16 text-ink">{title}</p>
      <p className="mt-2 text-14 text-ink-body">{detail}</p>
      <div className="mt-5 flex items-center gap-4">
        {onRetry ? (
          <Button size="md" variant="secondary" onClick={onRetry}>
            {retryLabel}
          </Button>
        ) : null}
        {requestId ? <RequestId value={requestId} /> : null}
      </div>
    </div>
  );
}

/** §B.10.3 · region scope: L2 on the critical tint, one `sm` secondary. */
export function RegionError({
  title,
  detail,
  requestId,
  onRetry,
  retryLabel = "Reintentar",
  children,
}: {
  title: string;
  detail: string;
  requestId?: string;
  onRetry?: () => void;
  retryLabel?: string;
  children?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className="rounded-card border border-edge-critical bg-tint-critical p-4"
    >
      <p className="text-14 font-medium text-ink">{title}</p>
      <p className="mt-1.5 text-12 text-ink-body">{detail}</p>
      {onRetry || requestId ? (
        <div className="mt-3 flex items-center gap-4">
          {onRetry ? (
            <Button size="sm" variant="secondary" onClick={onRetry}>
              {retryLabel}
            </Button>
          ) : null}
          {requestId ? <RequestId value={requestId} /> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function RequestId({ value }: { value: string }) {
  return (
    <code className="select-all font-mono text-10 uppercase tracking-eyebrow text-ink-label">
      {value}
    </code>
  );
}

/**
 * §B.10.1 · a re-fetch dims rather than blanks. A table re-fetching after a
 * filter, sort or page change keeps its previous rows at `opacity:0.6` and
 * shows a 2px progress line under the filter bar; blanking a populated table on
 * every keystroke is worse than the wait.
 */
export function ProgressLine({ active }: { active: boolean }) {
  return (
    <div aria-hidden className="relative h-0.5 shrink-0 overflow-hidden">
      {active ? <span className="block h-0.5 w-full bg-ink" /> : null}
    </div>
  );
}
