import { useCallback, useMemo, useSyncExternalStore } from "react";

/** §B.11 · the counter's screen floor. Below 1280 is unsupported in v1. */
export const COUNTER_FLOOR = "(min-width: 1280px)";
/** §B.17·2 · the handoff assumes desktop ≥1440; nothing below 1280 is designed. */
export const DESKTOP = "(min-width: 1440px)";

export function useMediaQuery(query: string, fallback = false): boolean {
  const list = useMemo(
    () =>
      typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? window.matchMedia(query)
        : null,
    [query],
  );

  const subscribe = useCallback(
    (onChange: () => void) => {
      if (!list) return () => {};
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [list],
  );

  const read = useCallback(() => list?.matches ?? fallback, [list, fallback]);

  return useSyncExternalStore(subscribe, read);
}
