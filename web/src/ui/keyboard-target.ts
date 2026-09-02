const ENTRY_OR_SELECTION =
  'input, textarea, select, [contenteditable="true"], [role="combobox"], ' +
  '[role="listbox"], [role="option"], [role="menu"]';

/** Whether a key event landed somewhere that owns single characters. */
export function isEntryOrSelectionTarget(target: EventTarget | null) {
  const element = target as HTMLElement | null;
  return Boolean(element?.closest?.(ENTRY_OR_SELECTION));
}
