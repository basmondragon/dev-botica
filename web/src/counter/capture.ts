import { useEffect, useRef } from "react";
import { isEntryOrSelectionTarget } from "@/ui/keyboard-target";

/**
 * The capture field, the scan-versus-typing heuristic, and the focus discipline
 * that makes a scan land wherever the cashier was looking.
 *
 * **One capture field per till surface, always focused.** A document-level
 * `keydown` handler redirects any printable character or `Enter` into it
 * whenever focus is on the body, a ticket row, or any non-text control, and
 * gives it focus in the same tick. A cashier will not click into a field before
 * scanning, and a till whose focus is somewhere else is a till where the next
 * scan goes into the void — discovered at a counter with a queue (§B.13.3).
 *
 * **The heuristic is a latency optimisation and not a correctness gate**, and
 * that is what makes it safe to be wrong. A run of six or more characters whose
 * median inter-keystroke gap is under 30 ms, terminated by `Enter` within
 * 400 ms of the first character, is a scan: the whole buffer resolves as one
 * exact match against the local barcode index. Anything slower is typing and
 * drives the incremental filter. A typed string that exactly matches a barcode
 * on `Enter` also adds a line, and a scan misclassified as typing still lands on
 * `Enter` — the only cost of a misclassification is the keystroke-level
 * filtering work that ran on the way.
 *
 * **There is no debounce.** Debouncing exists to spare a server, and this path
 * has no server on it; a debounce would add to the very budget §4 sets at 30 ms.
 */

/** Six characters, because every barcode a droguería prints is longer and no
 *  product name a cashier means to search is shorter as a burst. */
export const SCAN_MIN_LENGTH = 6;

/** Median inter-keystroke gap, in milliseconds. *If the pilot's scanners emit
 *  slower than this*, it is one constant and the fallback path already produces
 *  the right answer (§11.5). */
export const SCAN_MAX_GAP_MS = 30;

/** From the first character to the terminating `Enter`. */
export const SCAN_MAX_SPAN_MS = 400;

export interface Cadence {
  /** When each character of the current buffer arrived. */
  marks: number[];
}

export function freshCadence(): Cadence {
  return { marks: [] };
}

function median(values: number[]): number {
  if (values.length === 0) return Number.POSITIVE_INFINITY;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]!
    : (sorted[middle - 1]! + sorted[middle]!) / 2;
}

/** Whether the buffer that just ended in `Enter` came off a scanner. */
export function looksScanned(
  cadence: Cadence,
  at: number = Date.now(),
): boolean {
  const { marks } = cadence;
  if (marks.length < SCAN_MIN_LENGTH) return false;
  if (at - marks[0]! > SCAN_MAX_SPAN_MS) return false;
  const gaps = marks.slice(1).map((mark, index) => mark - marks[index]!);
  return median(gaps) < SCAN_MAX_GAP_MS;
}

export function noteKeystroke(cadence: Cadence, at: number = Date.now()) {
  // A gap longer than the whole span means the previous burst is over — a
  // cashier who typed `amox`, waited, then scanned would otherwise carry four
  // slow keystrokes into the scan's own median.
  const last = cadence.marks[cadence.marks.length - 1];
  if (last !== undefined && at - last > SCAN_MAX_SPAN_MS) cadence.marks = [];
  cadence.marks.push(at);
}

/**
 * Redirect the keyboard into the capture field.
 *
 * `suspended` is what a dialog sets: **a scan while `Cobro` is open does
 * nothing.** A barcode arriving during payment is a cashier scanning the next
 * customer's item, and adding it to a ticket that is being paid for is the
 * worst outcome available.
 */
export function useCapture(
  field: React.RefObject<HTMLInputElement | null>,
  suspended: boolean,
) {
  // The handler is bound once and reads the latest value through a ref, so a
  // dialog opening does not re-register a window listener on every render --
  // and the ref is written in an effect rather than during render, because a
  // render that is thrown away must not leave the listener believing it.
  const held = useRef(suspended);
  useEffect(() => {
    held.current = suspended;
  }, [suspended]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (held.current) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const input = field.current;
      if (!input || document.activeElement === input) return;
      // A key that landed in another text box, a select or a listbox belongs to
      // that control. Everything else on a till surface belongs to the scanner.
      if (isEntryOrSelectionTarget(event.target)) return;
      if (event.key.length !== 1 && event.key !== "Enter") return;
      // **Focus moves and the event is left alone.** Moving focus during
      // `keydown` is what sends the character that follows to the newly focused
      // element, so the field receives it the way it would have if the cashier
      // had clicked into it first. Writing the character by hand instead would
      // mean fighting React's own value tracking for every keystroke of a
      // barcode burst, and losing exactly one of them is the failure nobody
      // notices until a code comes out one digit short.
      input.focus();
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [field]);
}

/**
 * §B.13.3 · the till's function keys. **No single-letter shortcut on any till
 * surface**: a scan is a burst of characters followed by `Enter`, and a surface
 * where `j` means something is a surface where scanning a product code
 * navigates.
 *
 * `Esc` clears the capture field and **never cancels the sale**.
 */
export function useTillKeys(handlers: {
  cobrar?: () => void;
  focusCapture?: () => void;
  syncPanel?: () => void;
  clear?: () => void;
  suspended?: boolean;
}) {
  const held = useRef(handlers);
  useEffect(() => {
    held.current = handlers;
  });

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const current = held.current;
      if (current.suspended) return;
      switch (event.key) {
        case "F2":
          event.preventDefault();
          current.cobrar?.();
          break;
        case "F4":
          event.preventDefault();
          current.focusCapture?.();
          break;
        case "F8":
          event.preventDefault();
          current.syncPanel?.();
          break;
        case "Escape":
          // Never `preventDefault` beyond the field: a dialog above this
          // surface has its own `Esc`, and swallowing it would trap a cashier
          // inside `Cobro`.
          current.clear?.();
          break;
        default:
          break;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}
