import type { ReactNode } from "react";
import { BrandSquare } from "@/ui/brand";

/**
 * §B.8.4·5 · the 380px L2 card the two unauthenticated screens share: the 24px
 * brand square, the wordmark, a `t-20` heading, and the invite-only line at
 * 11px `#727272`.
 */
export function AuthCard({
  heading,
  children,
  footnote = true,
}: {
  heading: string;
  children: ReactNode;
  footnote?: boolean;
}) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas p-8">
      <div className="w-[380px] max-w-full rounded-panel border border-edge-soft bg-surface p-8 shadow-plane">
        <div className="flex items-center gap-2.5">
          <BrandSquare />
          <span className="text-14 font-medium text-ink">Botica</span>
        </div>
        <h1 className="mt-6 text-20 tracking-display text-ink">{heading}</h1>
        <div className="mt-5">{children}</div>
        {footnote ? (
          <p className="mt-5 text-11 text-ink-label">
            El acceso a Botica es por invitación. Pida el enlace a la
            administradora de su droguería.
          </p>
        ) : null}
      </div>
    </div>
  );
}
