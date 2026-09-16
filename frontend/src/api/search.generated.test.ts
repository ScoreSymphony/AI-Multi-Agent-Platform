import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";
import type { SearchRequest } from "./types";

describe("generated Search transport contract", () => {
  it("maps the frontend query-builder model onto canonical Search query parameters", async () => {
    const page = { items: [], next_cursor: null, total: 0, limit: 25 };
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });
    const request: SearchRequest = {
      q: "worker recovery",
      types: ["task", "run"],
      project_id: "project_123",
      statuses: ["running", "failed"],
      tags: ["recovery"],
      sources: ["control-plane"],
      providers: ["runtime"],
      mode: "hybrid",
      limit: 25,
      sort: "updated_at",
      direction: "desc",
    };

    const result = await client.search(request);
    expect(result).toEqual(page);

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const [, query = ""] = url.split("?");
    const params = new URLSearchParams(query);
    expect(url.startsWith("/api/v1/search?")).toBe(true);
    expect(params.get("q")).toBe("worker recovery");
    expect(params.get("type")).toBe("task,run");
    expect(params.get("project_id")).toBe("project_123");
    expect(params.get("status")).toBe("running,failed");
    expect(params.get("tag")).toBe("recovery");
    expect(params.get("source")).toBe("control-plane");
    expect(params.get("provider")).toBe("runtime");
    expect(params.get("mode")).toBe("hybrid");
    expect(params.get("limit")).toBe("25");
    expect(params.get("sort")).toBe("updated_at");
    expect(params.get("direction")).toBe("desc");
    expect(init.method).toBe("GET");
  });
});
