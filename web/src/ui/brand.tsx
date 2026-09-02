import { cn } from "./cn";

/**
 * §A.12 · there are no images and no third-party logos in this system. The
 * brand is the 24×24 `#171717` square carrying a 10px/500 `#fbfbfb` `B`, and
 * the organisation's name beside it.
 */
export function BrandSquare({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-mark bg-ink",
        "font-medium text-10 text-canvas",
        className,
      )}
    >
      B
    </span>
  );
}
