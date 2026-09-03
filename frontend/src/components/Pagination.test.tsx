import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { PaginationControls } from "./Pagination";

describe("PaginationControls", () => {
  it("announces page state and disables unavailable directions", () => {
    const html = renderToStaticMarkup(
      <PaginationControls
        page={{ items: [{ id: "task" }], next_cursor: null, total: 1 }}
        pageNumber={1}
        hasPrevious={false}
        onPrevious={vi.fn()}
        onNext={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(html).toContain('aria-label="List pagination"');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain("Page 1 · 1 shown · 1 total");
    expect(html).toContain(">Previous</button>");
    expect(html).toContain(">Next</button>");
    expect((html.match(/disabled=""/g) ?? []).length).toBe(2);
  });
});