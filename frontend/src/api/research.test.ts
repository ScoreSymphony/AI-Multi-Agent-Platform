import { describe, expect, it, vi } from "vitest";
import { ResearchClient } from "./research";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("#589 Research Evidence frontend client", () => {
  it("reads canonical Research collections through the versioned Control Plane", async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 }))
      .mockResolvedValueOnce(jsonResponse({ id: "research_1", type: "research-item" }))
      .mockResolvedValueOnce(jsonResponse({ id: "source_1", type: "research-source" }))
      .mockResolvedValueOnce(jsonResponse({ id: "observation_1", type: "research-source-observation" }))
      .mockResolvedValueOnce(jsonResponse({ id: "claim_1", type: "research-claim" }))
      .mockResolvedValueOnce(jsonResponse({ id: "evidence_1", type: "research-evidence" }));
    const client = new ResearchClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.listItems({
      q: "runtime",
      filters: { research_class: "project_research", status: "current" },
    });
    await client.getItem("research_1");
    await client.getSource("source_1");
    await client.getObservation("observation_1");
    await client.getClaim("claim_1");
    await client.getEvidence("evidence_1");

    const listUrl = new URL(fetchSpy.mock.calls[0][0] as string, "https://platform.invalid");
    expect(listUrl.pathname).toBe("/api/v1/research-items");
    expect(listUrl.searchParams.get("q")).toBe("runtime");
    expect(listUrl.searchParams.get("filter[research_class]")).toBe("project_research");
    expect(listUrl.searchParams.get("filter[status]")).toBe("current");

    expect(new URL(fetchSpy.mock.calls[1][0] as string, "https://platform.invalid").pathname)
      .toBe("/api/v1/research-items/research_1");
    expect(new URL(fetchSpy.mock.calls[2][0] as string, "https://platform.invalid").pathname)
      .toBe("/api/v1/research-sources/source_1");
    expect(new URL(fetchSpy.mock.calls[3][0] as string, "https://platform.invalid").pathname)
      .toBe("/api/v1/research-source-observations/observation_1");
    expect(new URL(fetchSpy.mock.calls[4][0] as string, "https://platform.invalid").pathname)
      .toBe("/api/v1/research-claims/claim_1");
    expect(new URL(fetchSpy.mock.calls[5][0] as string, "https://platform.invalid").pathname)
      .toBe("/api/v1/research-evidence/evidence_1");

    for (const [, init] of fetchSpy.mock.calls as Array<[string, RequestInit]>) {
      expect(init.method).toBe("GET");
      expect(init.credentials).toBe("include");
    }
  });
});
