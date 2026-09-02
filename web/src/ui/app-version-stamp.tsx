import { APP_VERSION_LABEL } from "@/app-version";
import { cn } from "./cn";

export function AppVersionStamp({ className }: { className?: string }) {
  return (
    <div className={cn("flex shrink-0 items-end px-5", className)}>
      <span className="max-w-full truncate font-mono text-10 uppercase tracking-eyebrow tabular-nums text-ink-soft">
        {APP_VERSION_LABEL}
      </span>
    </div>
  );
}
