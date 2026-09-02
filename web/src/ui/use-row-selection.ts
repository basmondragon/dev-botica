import { useCallback, useState } from "react";
import { count } from "./format";

/**
 * §B.4.5 · a checked set survives paging, is announced as a count in the bulk
 * bar, and is cleared by `Esc` and by any filter change -- and the
 * filter-change clear is announced.
 */
export function useRowSelection() {
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [announcement, setAnnouncement] = useState("");

  const toggle = useCallback((id: string | undefined) => {
    if (!id) return;
    setCheckedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      setAnnouncement(`${count(next.size)} seleccionadas`);
      return next;
    });
  }, []);

  const setPage = useCallback((ids: string[], checked: boolean) => {
    setCheckedIds((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      setAnnouncement(`${count(next.size)} seleccionadas`);
      return next;
    });
  }, []);

  const clear = useCallback((reason?: string) => {
    setCheckedIds((current) => {
      if (current.size === 0) return current;
      setAnnouncement(reason ?? "Se quitó la selección.");
      return new Set();
    });
  }, []);

  return { checkedIds, toggle, setPage, clear, announcement };
}
