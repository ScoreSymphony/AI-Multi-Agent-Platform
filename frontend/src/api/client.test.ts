import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient, ControlPlaneError } from "./client";

const task = {
  id: "task_123e4567-e89b-42d3-a456-426614174000",
  type: "task",
  title: "Test",
  objective: "Verify client",
  status: "draft",
  owner: { type: "user", id: "test" },
  project_id: null,
  revision: 1,
  plan_ref: null,
  step_ids: [],
  run_ids: [],
  artifact_ids: [],
  result_ids: [],
  wait_reason: null,
  blocked: false,
  correlation_id: null,
  causation_id: null,
  created_at: "2026-09-03T00:00:00+00:00",
  updated_at: "2026-09-03T00:00:00+00:00",
};

describe("ControlPlaneClient", () => {
  it("sends mutating Task requests only through the versioned Control Plane", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(task), { status: 201 }));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.createTask({
      title: "Test",
      objective: "Verify client",
      owner_type: "user",
      owner_id: "test",
    });

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/tasks");
    expect(init.credentials).toBe("include");
    const headers = init.headers as Headers;
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(headers.get("X-Correlation-ID")).toBeTruthy();
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("preserves the canonical error envelope", async () => {
    const errorBody = {
      code: "forbidden",
      category: "authorization",
      message: "denied",
      request_id: "request_test",
      correlation_id: "correlation_test",
      retryable: false,
    };
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(errorBody), { status: 403, headers: { "content-type": "application/json" } }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await expect(client.listTasks()).rejects.toMatchObject({
      status: 403,
      body: errorBody,
    } satisfies Partial<ControlPlaneError>);
  });

  it("encodes canonical collection filters without inventing backend routes", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_cursor: null, total: 0, limit: 25 }), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.listTasks({ limit: 25, filters: { status: "running" } });

    const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/tasks?");
    expect(url).toContain("limit=25");
    expect(url).toContain("filter%5Bstatus%5D=running");
  });
});
