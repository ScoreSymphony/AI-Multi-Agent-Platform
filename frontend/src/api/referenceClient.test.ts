import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const artifact = {
  id: "artifact_123e4567-e89b-42d3-a456-426614174010",
  type: "artifact",
  task_id: "task_123e4567-e89b-42d3-a456-426614174000",
};

describe("canonical reference client", () => {
  it("lists reference collections only through the versioned Control Plane", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [artifact], next_cursor: null, total: 1, limit: 50 }), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const page = await client.listReferences("artifacts", { limit: 50, q: "task_123" });

    expect(page.items[0]?.id).toBe(artifact.id);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/artifacts?limit=50&q=task_123");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("include");
    expect((init.headers as Headers).get("Idempotency-Key")).toBeNull();
  });

  it("reads one canonical result reference without inventing a storage route", async () => {
    const result = {
      id: "result_123e4567-e89b-42d3-a456-426614174011",
      type: "result",
      task_id: artifact.task_id,
    };
    const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), { status: 200 }));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.getReference("results", result.id);

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api/v1/results/${result.id}`);
    expect(init.method).toBe("GET");
  });

  it("keeps plans and steps on their canonical read-only collections", async () => {
    const page = { items: [], next_cursor: null, total: 0, limit: 25 };
    const fetchSpy = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(page), { status: 200 })));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.listReferences("plans", { limit: 25 });
    await client.listReferences("steps", { limit: 25 });

    expect((fetchSpy.mock.calls[0] as [string, RequestInit])[0]).toBe("/api/v1/plans?limit=25");
    expect((fetchSpy.mock.calls[1] as [string, RequestInit])[0]).toBe("/api/v1/steps?limit=25");
  });
});
