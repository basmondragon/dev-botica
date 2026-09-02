import { useCallback, useEffect, useRef, useState } from "react";

/**
 * §B.13.2 · every office list surface answers `j`, `k`, `Enter`, `x`, `Esc` and
 * `/`. Not per-screen opt-in.
 *
 * Single-letter shortcuts are suppressed whenever focus is inside a text input,
 * a textarea or a contenteditable -- and they are prohibited outright on till
 * surfaces (§B.13.3), because a scan is a burst of characters and any surface
 * where `j` means something is a surface where scanning navigates.
 */
export interface ListKeysOptions {
  rowCount: number;
  rowId: (index: number) => string;
  pageKey?: string | number;
  onOpen?: (index: number) => void;
  onToggleCheck?: (index: number) => void;
  onExtendCheck?: (index: number) => void;
  onEscape?: () => boolean | void;
  onSearch?: () => void;
  onNextPage?: () => boolean | void;
  onPreviousPage?: () => boolean | void;
  enabled?: boolean;
}

const TEXT_INPUT_TYPES = new Set([
  "text",
  "search",
  "email",
  "password",
  "url",
  "tel",
  "number",
  "date",
  "datetime-local",
  "month",
  "time",
  "week",
]);

function typingInAField(target: EventTarget | null) {
  const element = target as HTMLElement | null;
  if (!element) return false;
  if (element.isContentEditable) return true;
  const tag = element.tagName;
  if (tag === "TEXTAREA") return true;
  if (tag !== "INPUT") return false;
  return TEXT_INPUT_TYPES.has((element as HTMLInputElement).type);
}

const DIALOG = '[role="dialog"], [role="alertdialog"]';
const ENTER_OWNERS =
  'button, a[href], summary, select, [role="option"], [role="combobox"], [role="menuitem"]';
const ARROW_OWNERS =
  'select, [role="option"], [role="combobox"], [role="listbox"], [role="menu"], ' +
  '[role="radiogroup"], input[type="radio"], input[type="range"]';
const ESCAPE_OWNERS = "[data-owns-escape]";

function ownsKey(target: EventTarget | null, key: string) {
  const element = target as HTMLElement | null;
  if (!element || typeof element.closest !== "function") return false;
  if (key === "Enter") return !!element.closest(ENTER_OWNERS);
  if (key === "ArrowDown" || key === "ArrowUp")
    return !!element.closest(ARROW_OWNERS);
  if (key === "Escape") return !!element.closest(ESCAPE_OWNERS);
  return false;
}

export function useListKeys({
  rowCount,
  rowId,
  pageKey,
  onOpen,
  onToggleCheck,
  onExtendCheck,
  onEscape,
  onSearch,
  onNextPage,
  onPreviousPage,
  enabled = true,
}: ListKeysOptions) {
  const [cursor, setCursor] = useState(-1);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pendingEdge = useRef<{
    edge: "first" | "last";
    from: string | number | undefined;
    confirmed: boolean;
  } | null>(null);

  function requestPage(
    edge: "first" | "last",
    turn: (() => boolean | void) | undefined,
  ) {
    if (!turn) return;
    const answer = turn();
    if (answer === false) return;
    pendingEdge.current = { edge, from: pageKey, confirmed: answer === true };
  }

  const move = useCallback(
    (delta: number, extend: boolean) => {
      const next = cursor + delta;
      if (next < 0) {
        if (cursor > 0) {
          setCursor(0);
          return;
        }
        requestPage("last", onPreviousPage);
        return;
      }
      if (next >= rowCount) {
        // `j`/`k` past the last row advances to the next page and lands on its
        // first row.
        requestPage("first", onNextPage);
        return;
      }
      setCursor(next);
      if (extend) onExtendCheck?.(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cursor, pageKey, rowCount, onExtendCheck, onNextPage, onPreviousPage],
  );

  useEffect(() => {
    if (!enabled) return;
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const dialog = target?.closest?.(DIALOG);
      if (dialog && !dialog.contains(containerRef.current)) return;
      if (typingInAField(event.target) && event.key !== "Escape") return;
      if (ownsKey(event.target, event.key)) return;
      // A modified key is not a single-letter shortcut. Without this, ⌘K --
      // which §B.13.2 reserves and binds to nothing -- moves the row cursor.
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      switch (event.key) {
        case "j":
        case "ArrowDown":
          event.preventDefault();
          move(1, false);
          break;
        case "k":
        case "ArrowUp":
          event.preventDefault();
          move(-1, false);
          break;
        case "J":
          event.preventDefault();
          move(1, true);
          break;
        case "K":
          event.preventDefault();
          move(-1, true);
          break;
        case "Enter":
          if (cursor >= 0 && onOpen) {
            event.preventDefault();
            onOpen(cursor);
          }
          break;
        case "x":
          if (cursor >= 0 && onToggleCheck) {
            event.preventDefault();
            onToggleCheck(cursor);
          }
          break;
        case "/":
          if (onSearch) {
            event.preventDefault();
            onSearch();
          }
          break;
        default:
          break;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled, cursor, move, onOpen, onToggleCheck, onSearch]);

  useEffect(() => {
    if (!enabled || !onEscape) return;
    function onEscapeCapture(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      const target = event.target as HTMLElement | null;
      if (ownsKey(target, "Escape")) return;
      const dialog = target?.closest?.(DIALOG);
      if (dialog && !dialog.contains(containerRef.current)) return;
      if (onEscape!() === true) event.stopPropagation();
    }
    window.addEventListener("keydown", onEscapeCapture, true);
    return () => window.removeEventListener("keydown", onEscapeCapture, true);
  }, [enabled, onEscape]);

  useEffect(() => {
    const pending = pendingEdge.current;
    pendingEdge.current = null;
    if (rowCount === 0) return;
    if (pending && (pending.confirmed || pending.from !== pageKey)) {
      setCursor(pending.edge === "first" ? 0 : rowCount - 1);
      return;
    }
    setCursor((current) => (current >= rowCount ? rowCount - 1 : current));
  }, [rowCount, pageKey]);

  useEffect(() => {
    if (cursor < 0) return;
    document
      .getElementById(rowId(cursor))
      ?.scrollIntoView({ block: "nearest", behavior: "auto" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor]);

  return {
    cursor,
    setCursor,
    containerRef,
    /** §B.13.1 · `role="grid"` with `aria-activedescendant`, so a screen reader
     *  follows the cursor without moving DOM focus per row. */
    containerProps: {
      ref: containerRef,
      tabIndex: 0,
      role: "grid" as const,
      "aria-activedescendant": cursor >= 0 ? rowId(cursor) : undefined,
    },
  };
}
