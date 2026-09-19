import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DecisionRecordClient } from "../api/decisions";
import { ResearchClient } from "../api/research";
import { RouterProvider } from "../app/router";
import { DecisionRecordsPage } from "./DecisionRecordsPage";
import { ResearchPage } from "./ResearchPage";

describe("#1234 Research and Decision maintained Web surfaces", () => {
  it("renders the Research Evidence inventory and canonical filters", () => {
    const fetchSpy = vi.fn();
    const client = new ResearchClient({ fetchImpl: fetchSpy as unknown as typeof fetch });
    const markup = renderToStaticMarkup(
      <RouterProvider>
        <ResearchPage client={client} />
      </RouterProvider>,
    );

    expect(markup).toContain("Research Evidence");
    expect(markup).toContain("Research filters");
    expect(markup).toContain("Research class");
    expect(markup).toContain("Status");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("renders the Decision Records inventory and canonical filters", () => {
    const fetchSpy = vi.fn();
    const client = new DecisionRecordClient({ fetchImpl: fetchSpy as unknown as typeof fetch });
    const markup = renderToStaticMarkup(
      <RouterProvider>
        <DecisionRecordsPage client={client} />
      </RouterProvider>,
    );

    expect(markup).toContain("Decision Records");
    expect(markup).toContain("Decision filters");
    expect(markup).toContain("Category");
    expect(markup).toContain("Scope type");
    expect(markup).toContain("Status");
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
