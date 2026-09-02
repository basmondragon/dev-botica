import type { ReactNode } from "react";
import { cn } from "./cn";

/**
 * §B.7 · five families. Part A draws four; neutral is the fifth and introduces
 * **no new colour** -- it is the ink label on the symptom chip's own fill, both
 * already in the system.
 *
 * §B.7.2 · solid means the state is true now. Hollow means it is not yet true,
 * or it is true only under a condition, or it is waiting on something outside
 * this system.
 */
export type Family = "neutral" | "info" | "positive" | "warning" | "critical";
export type Dot = "solid" | "hollow";

const SOLID: Record<Family, string> = {
  neutral: "bg-neutral",
  info: "bg-info",
  positive: "bg-positive",
  warning: "bg-warning",
  critical: "bg-critical",
};

const HOLLOW: Record<Family, string> = {
  neutral: "border border-neutral",
  info: "border border-info",
  positive: "border border-positive",
  warning: "border border-warning",
  critical: "border border-critical",
};

const TINT: Record<Family, string> = {
  neutral: "bg-tint-neutral",
  info: "bg-tint-info",
  positive: "bg-tint-positive",
  warning: "bg-tint-warning",
  critical: "bg-tint-critical",
};

/** §A.16 · 8×8, `margin-right:7px`, `vertical-align:1px`. */
export function StatusDot({
  family,
  dot = "solid",
}: {
  family: Family;
  dot?: Dot;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2 shrink-0 rounded-pill align-[1px]",
        dot === "solid" ? SOLID[family] : cn(HOLLOW[family], "bg-transparent"),
      )}
    />
  );
}

/**
 * §A.16 · the full tinted badge. The label is `#171717` on every tint and the
 * dot carries the meaning at full strength: the family colour on its own tint
 * runs 4.03–4.55:1, so three of four fail AA for a 12px label, where `#171717`
 * on the same four runs 14.66–15.04:1. This is settled; do not revisit it.
 *
 * §B.7.3 · **at most one column per table may use this**, and it is the column
 * the surface is about. A second status on the same row is a `StatusLine`.
 */
export function Badge({
  family = "neutral",
  dot = "solid",
  children,
  className,
}: {
  family?: Family;
  dot?: Dot;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-[7px] whitespace-nowrap rounded-pill",
        "px-2.5 py-1 text-12/[16px] text-ink",
        TINT[family],
        className,
      )}
    >
      <StatusDot family={family} dot={dot} />
      {children}
    </span>
  );
}

/**
 * §B.7.3 · a status shown incidentally: dot plus label, no fill, no pill.
 * Personas uses this, because the roster is not a surface *about* status.
 */
export function StatusLine({
  family,
  dot = "solid",
  label,
  className,
}: {
  family: Family;
  dot?: Dot;
  label: string;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-[7px]", className)}>
      <StatusDot family={family} dot={dot} />
      <span className="whitespace-nowrap text-12 text-ink-body">{label}</span>
    </span>
  );
}

/**
 * §A.16 · a pill without a dot -- the suggestion-type label. It carries no dot
 * because the three sit side by side in one list where the tint alone separates
 * them, and a dot on each would be three dots in a row saying nothing.
 */
export function TypePill({
  family,
  children,
}: {
  family: Family;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-block rounded-pill px-2 py-[3px] text-11/[16px] text-ink",
        TINT[family],
      )}
    >
      {children}
    </span>
  );
}

export interface Meaning {
  family: Family;
  dot: Dot;
  label: string;
}

/** The four rendered invitation states (§B.7.3, *Ajustes · Personas*). */
export const INVITATION_STATE: Record<string, Meaning> = {
  pending: { family: "neutral", dot: "hollow", label: "Pendiente" },
  expired: { family: "warning", dot: "hollow", label: "Vencida" },
  revoked: { family: "neutral", dot: "solid", label: "Revocada" },
  delivery_failed: { family: "critical", dot: "solid", label: "Envío fallido" },
  accepted: { family: "positive", dot: "solid", label: "Aceptada" },
};

export const USER_STATUS: Record<string, Meaning> = {
  active: { family: "positive", dot: "solid", label: "Activo" },
  suspended: { family: "warning", dot: "solid", label: "Suspendido" },
};

export const TENANT_STATUS: Record<string, Meaning> = {
  active: { family: "positive", dot: "solid", label: "Activa" },
  suspended: { family: "warning", dot: "solid", label: "Suspendida" },
};

/**
 * §B.7.4 · `items.invima_status`. Botica surfaces the state and records the
 * pharmacy's own decision; it does not block a sale on it and does not validate
 * against INVIMA's register. So `expired` is a **badge and a filter, and never
 * a disabled row**.
 *
 * `in_process` is hollow because INVIMA has the file: the system is waiting on
 * something outside itself (§B.7.2).
 */
export const INVIMA_STATUS: Record<string, Meaning> = {
  valid: { family: "positive", dot: "solid", label: "Registro vigente" },
  in_process: { family: "warning", dot: "hollow", label: "En trámite" },
  expired: { family: "critical", dot: "solid", label: "Registro vencido" },
  not_applicable: { family: "neutral", dot: "hollow", label: "No aplica" },
};

export const LOCATION_STATUS: Record<string, Meaning> = {
  active: { family: "positive", dot: "solid", label: "Activa" },
  closed: { family: "neutral", dot: "solid", label: "Cerrada" },
};
