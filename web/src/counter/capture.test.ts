import { describe, expect, it } from "vitest";
import {
  SCAN_MAX_GAP_MS,
  SCAN_MAX_SPAN_MS,
  SCAN_MIN_LENGTH,
  freshCadence,
  looksScanned,
  noteKeystroke,
} from "./capture";

/**
 * The scan-versus-typing heuristic.
 *
 * **It is a latency optimisation and not a correctness gate**, and that is what
 * makes it safe to be wrong: a typed string that exactly matches a barcode on
 * `Enter` also adds a line, and a scan misclassified as typing still lands on
 * `Enter`. The only cost of a misclassification is the keystroke-level
 * filtering work that ran on the way — which is why what is checked here is
 * that it *classifies*, not that the till depends on it.
 */

function burst(count: number, gap: number, start = 1_000): number[] {
  return Array.from({ length: count }, (_, index) => start + index * gap);
}

function cadenceOf(marks: number[]) {
  const cadence = freshCadence();
  for (const mark of marks) noteKeystroke(cadence, mark);
  return cadence;
}

describe("a scanner in HID keyboard mode", () => {
  it("reads as a scan: thirteen digits, 8 ms apart, terminated at once", () => {
    const marks = burst(13, 8);
    const cadence = cadenceOf(marks);
    expect(looksScanned(cadence, marks[marks.length - 1]! + 5)).toBe(true);
  });

  it("reads as typing when the gaps are a person's", () => {
    const marks = burst(13, 140);
    const cadence = cadenceOf(marks);
    expect(looksScanned(cadence, marks[marks.length - 1]! + 5)).toBe(false);
  });

  it("reads as typing below the length floor", () => {
    const marks = burst(SCAN_MIN_LENGTH - 1, 8);
    expect(looksScanned(cadenceOf(marks), marks[marks.length - 1]! + 5)).toBe(
      false,
    );
  });

  it("reads as typing when the terminator came too late", () => {
    // A burst that arrived fast and then sat there is somebody who pasted a
    // code and thought about it — the resolution is the same either way.
    const marks = burst(13, 8);
    expect(
      looksScanned(cadenceOf(marks), marks[0]! + SCAN_MAX_SPAN_MS + 50),
    ).toBe(false);
  });

  it("holds the boundary the constant names", () => {
    const fast = burst(10, SCAN_MAX_GAP_MS - 1);
    const slow = burst(10, SCAN_MAX_GAP_MS + 1);
    expect(looksScanned(cadenceOf(fast), fast[fast.length - 1]! + 1)).toBe(
      true,
    );
    expect(looksScanned(cadenceOf(slow), slow[slow.length - 1]! + 1)).toBe(
      false,
    );
  });
});

describe("one burst does not contaminate the next", () => {
  it("drops a stale buffer when the cashier stopped typing", () => {
    // `amox`, a pause, then a scan. Carrying the four slow keystrokes into the
    // scan's own median is what would make the scan read as typing.
    const cadence = freshCadence();
    for (const mark of burst(4, 200)) noteKeystroke(cadence, mark);
    const scan = burst(13, 8, 5_000);
    for (const mark of scan) noteKeystroke(cadence, mark);
    expect(looksScanned(cadence, scan[scan.length - 1]! + 5)).toBe(true);
  });
});
