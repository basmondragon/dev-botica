import { useCallback, useEffect, useRef } from "react";

/**
 * §B.8.5 · **the search field writes its param debounced**, and as a history
 * *replace*, so `Back` returns to the previous view rather than walking
 * backwards one keystroke at a time.
 *
 * The debounce also fixes the defect that shows up the moment a controlled
 * input is driven straight off the router: a navigation is asynchronous, so the
 * next keystroke lands on the value the URL had *before* the last one, and the
 * field keeps only the character most recently typed. The typed text lives in
 * component state; this is what carries it to the URL once the typing stops.
 */
export function useDebounced<T extends unknown[]>(
  run: (...args: T) => void,
  delay = 250,
) {
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef(run);

  useEffect(() => {
    latest.current = run;
  }, [run]);

  useEffect(
    () => () => {
      const pending = timer.current;
      if (pending !== undefined) window.clearTimeout(pending);
    },
    [],
  );

  return useCallback(
    (...args: T) => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => latest.current(...args), delay);
    },
    [delay],
  );
}
