import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const emptyPage = { items: [], next_cursor: null, total: 0, limit: 25 };

describe("ControlPlaneClient search", () => {
  it("encodes the canonical global search contract on one Control Plane route", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(emptyPage), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.search({
      q: "global search",
      types: ["task", "run"],
      project_id: "project_123",
      workspace_id: "workspace_456",
      statuses: ["running", "succeeded"],
      tags: ["search", "core"],
      sources: ["canonical"],
      providers: ["control-plane"],
      updated_after: "2026-09-03T10:00:00+02:00",
      updated_before: "2026-09-03T18:00:00+02:00",
      mode: "keyword",
      limit: 25,
      cursor: "cursor_1",
      sort: "relevance",
      direction: "desc",
    });

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [rawUrl, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const url = new URL(rawUrl, "https://control.test");
    expect(url.pathname).toBe("/api/v1/search");
    expect(url.searchParams.get("q")).toBe("global search");
    expect(url.searchParams.get("type")).toBe("task,run");
    expect(url.searchParams.get("project_id")).toBe("project_123");
    expect(url.searchParams.get("workspace_id")).toBe("workspace_456");
    expect(url.searchParams.get("status")).toBe("running,succeeded");
    expect(url.searchParams.get("tag")).toBe("search,core");
    expect(url.searchParams.get("source")).toBe("canonical");
    expect(url.searchParams.get("provider")).toBe("control-plane");
    expect(url.searchParams.get("updated_after")).toBe("2026-09-03T10:00:00+02:00");
    expect(url.searchParams.get("updated_before")).toBe("2026-09-03T18:00:00+02:00");
    expect(url.searchParams.get("mode")).toBe("keyword");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.get("cursor")).toBe("cursor_1");
    expect(url.searchParams.get("sort")).toBe("relevance");
    expect(url.searchParams.get("direction")).toBe("desc");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
  });

  it("supports exact canonical ID lookup without inventing a resource-specific route", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(emptyPage), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.search({ id: "task_123", types: ["task"] });

    const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/search?id=task_123&type=task");
  });
});
