import { useCallback, useEffect, useMemo, useState } from "react";

export interface CursorPaginationState {
  queryKey: string;
  cursor: string | null;
  history: Array<string | null>;
  pageNumber: number;
}

export interface CursorPager {
  cursor: string | undefined;
  pageNumber: number;
  hasPrevious: boolean;
  next: (nextCursor: string | null) => void;
  previous: () => void;
  reset: () => void;
}

export function initialCursorPagination(queryKey: string): CursorPaginationState {
  return { queryKey, cursor: null, history: [], pageNumber: 1 };
}

export function advanceCursorPagination(
  state: CursorPaginationState,
  nextCursor: string | null,
): CursorPaginationState {
  if (!nextCursor) return state;
  return {
    ...state,
    cursor: nextCursor,
    history: [...state.history, state.cursor],
    pageNumber: state.pageNumber + 1,
  };
}

export function retreatCursorPagination(state: CursorPaginationState): CursorPaginationState {
  if (state.history.length === 0) return state;
  const history = state.history.slice(0, -1);
  return {
    ...state,
    cursor: state.history[state.history.length - 1] ?? null,
    history,
    pageNumber: Math.max(1, state.pageNumber - 1),
  };
}

export function useCursorPagination(queryKey: string): CursorPager {
  const [state, setState] = useState<CursorPaginationState>(() => initialCursorPagination(queryKey));
  const effective = useMemo(
    () => state.queryKey === queryKey ? state : initialCursorPagination(queryKey),
    [queryKey, state],
  );

  useEffect(() => {
    if (state.queryKey !== queryKey) setState(initialCursorPagination(queryKey));
  }, [queryKey, state.queryKey]);

  const next = useCallback((nextCursor: string | null) => {
    setState((current) => {
      const base = current.queryKey === queryKey ? current : initialCursorPagination(queryKey);
      return advanceCursorPagination(base, nextCursor);
    });
  }, [queryKey]);

  const previous = useCallback(() => {
    setState((current) => {
      const base = current.queryKey === queryKey ? current : initialCursorPagination(queryKey);
      return retreatCursorPagination(base);
    });
  }, [queryKey]);

  const reset = useCallback(() => setState(initialCursorPagination(queryKey)), [queryKey]);

  return {
    cursor: effective.cursor ?? undefined,
    pageNumber: effective.pageNumber,
    hasPrevious: effective.history.length > 0,
    next,
    previous,
    reset,
  };
}
