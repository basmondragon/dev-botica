import type { ReactNode } from "react";

/**
 * §B.8.4·4 · block titles inside the pane are `t-16`/500, one step below the
 * dialog's own rank; blocks are separated by space and hairlines, never by
 * nested cards.
 */
export function SectionHeading({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex items-start gap-4 pb-4">
      <div className="min-w-0 flex-1">
        <h3 className="text-16 font-medium text-ink">{title}</h3>
        {description ? (
          <p className="mt-1 max-w-[720px] text-12 text-ink-label">
            {description}
          </p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}
