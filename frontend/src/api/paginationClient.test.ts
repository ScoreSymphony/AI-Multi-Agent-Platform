import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

describe("Control Plane cursor forwarding", () => {
  it("forwards an opaque Task cursor without decoding or changing its value", async () => {
    const page = { items: [], next_cursor: null, total: 0, limit: 100 };
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(page), { status: 200 }),
    );
    const client = new ControlPlaneClient({
      fetchImpl: fetchSpy as unknown as typeof fetch,
    });

    await client.listTasks({
      limit: 100,
      cursor: "opaque/server+cursor==",
      sort: "priority",
      direction: "desc",
    });

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const parsed = new URL(url, "https://platform.invalid");
    expect(parsed.pathname).toBe("/api/v1/tasks");
    expect(parsed.searchParams.get("cursor")).toBe("opaque/server+cursor==");
    expect(parsed.searchParams.get("sort")).toBe("priority");
    expect(parsed.searchParams.get("direction")).toBe("desc");
    expect(init.method).toBe("GET");
  });
});