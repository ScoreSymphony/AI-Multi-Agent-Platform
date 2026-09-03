import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const emptyPage = { items: [], next_cursor: null, total: 0, limit: 100 };

function clientWithFetch() {
  const fetchSpy = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(emptyPage), { status: 200 }),
  );
  return {
    client: new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch }),
    fetchSpy,
  };
}

describe("paginated canonical list clients", () => {
  it("forwards the opaque Run cursor through /api/v1/runs", async () => {
    const { client, fetchSpy } = clientWithFetch();

    await client.listRuns({
      limit: 100,
      cursor: "opaque-run/cursor+2==",
      sort: "updated_at",
      direction: "desc",
    });

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url, "https://platform.invalid");
    expect(parsed.pathname).toBe("/api/v1/runs");
    expect(parsed.searchParams.get("cursor")).toBe("opaque-run/cursor+2==");
    expect(parsed.searchParams.get("sort")).toBe("updated_at");
    expect(parsed.searchParams.get("direction")).toBe("desc");
    expect(init.method).toBe("GET");
  });

  it("keeps reference search and cursor on the selected canonical collection", async () => {
    const { client, fetchSpy } = clientWithFetch();

    await client.listReferences("artifacts", {
      limit: 100,
      cursor: "opaque-artifact/cursor+2==",
      q: "task_123",
    });

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url, "https://platform.invalid");
    expect(parsed.pathname).toBe("/api/v1/artifacts");
    expect(parsed.searchParams.get("cursor")).toBe("opaque-artifact/cursor+2==");
    expect(parsed.searchParams.get("q")).toBe("task_123");
    expect(init.method).toBe("GET");
  });
});