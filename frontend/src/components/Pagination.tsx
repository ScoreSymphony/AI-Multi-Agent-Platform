import type { Page } from "../api/types";

export function PaginationControls({
  page,
  pageNumber,
  hasPrevious,
  onPrevious,
  onNext,
  onRefresh,
}: {
  page: Pick<Page<unknown>, "items" | "next_cursor" | "total">;
  pageNumber: number;
  hasPrevious: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onRefresh: () => void;
}) {
  return (
    <nav className="pagination" aria-label="List pagination">
      <span aria-live="polite">Page {pageNumber} · {page.items.length} shown · {page.total} total</span>
      <div className="actions">
        <button type="button" disabled={!hasPrevious} onClick={onPrevious}>Previous</button>
        <button type="button" onClick={onRefresh}>Refresh</button>
        <button type="button" disabled={!page.next_cursor} onClick={onNext}>Next</button>
      </div>
    </nav>
  );
}
