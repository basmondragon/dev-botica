import { useCallback, useState } from "react";
import { PAGE_SIZES } from "./table";

/**
 * §B.4 + §9 · the client half of the grid contract, taken through an adapter so
 * a **route** and a **dialog** get the same server contract.
 *
 * On a route the state lives in TanStack Router's typed search params, so any
 * view is a link. Inside the settings dialog it is component-local -- §B.8.4·4's
 * deliberate departure: a dialog does not own the address bar it floats over,
 * and if it wrote search params, `Escape` would have to restore the underlying
 * route's own params and would lose the caller's filters.
 */
export interface GridState {
  page: number;
  pageSize: number;
  sort?: string;
  order: "asc" | "desc";
}

export interface GridAdapter extends GridState {
  setPage: (next: number) => void;
  setPageSize: (next: number) => void;
  /** One sort column at a time; clicking a different column replaces the sort. */
  toggleSort: (key: string) => void;
  /** Any filter change resets to page 1. */
  resetToFirstPage: () => void;
}

export const DEFAULT_GRID: GridState = {
  page: 1,
  pageSize: PAGE_SIZES[0],
  sort: undefined,
  order: "desc",
};

/** The dialog half: component-local state, same shape. */
export function useLocalGrid(initial: Partial<GridState> = {}): GridAdapter {
  const [state, setState] = useState<GridState>({
    ...DEFAULT_GRID,
    ...initial,
  });

  const setPage = useCallback(
    (page: number) => setState((current) => ({ ...current, page })),
    [],
  );

  const setPageSize = useCallback(
    (pageSize: number) =>
      setState((current) => ({ ...current, pageSize, page: 1 })),
    [],
  );

  const toggleSort = useCallback(
    (key: string) =>
      setState((current) => ({
        ...current,
        page: 1,
        sort: key,
        order: current.sort === key && current.order === "asc" ? "desc" : "asc",
      })),
    [],
  );

  const resetToFirstPage = useCallback(
    () => setState((current) => ({ ...current, page: 1 })),
    [],
  );

  return { ...state, setPage, setPageSize, toggleSort, resetToFirstPage };
}

/** The route half: the same shape over typed search params. */
export function routeGrid(
  state: GridState,
  navigate: (next: Partial<GridState>) => void,
): GridAdapter {
  return {
    ...state,
    setPage: (page) => navigate({ page }),
    setPageSize: (pageSize) => navigate({ pageSize, page: 1 }),
    toggleSort: (key) =>
      navigate({
        page: 1,
        sort: key,
        order: state.sort === key && state.order === "asc" ? "desc" : "asc",
      }),
    resetToFirstPage: () => navigate({ page: 1 }),
  };
}
