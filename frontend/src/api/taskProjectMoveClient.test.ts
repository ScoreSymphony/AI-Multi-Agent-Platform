import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const task = {
  id: "task_123e4567-e89b-42d3-a456-426614174000",
  type: "task",
  title: "Test",
  objective: "Move task",
  status: "draft",
  owner: { type: "user", id: "user:alice" },
  project_id: "project_123e4567-e89b-42d3-a456-426614174001",
  revision: 2,
  plan_ref: null,
  step_ids: [],
  run_ids: [],
  artifact_ids: [],
  result_ids: [],
  wait_reason: null,
  blocked: false,
  correlation_id: null,
  causation_id: null,
  created_at: "2026-09-06T00:00:00+00:00",
  updated_at: "2026-09-06T00:00:00+00:00",
};

describe("ControlPlaneClient Task Project reassignment", () => {
  it("uses the client's existing authenticated fetch boundary", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(task), { status: 200 }),
    );
    const client = new ControlPlaneClient({
      baseUrl: "https://control.example",
      fetchImpl: fetchSpy as unknown as typeof fetch,
    });

    await client.moveTaskProject(
      task.id,
      "project_123e4567-e89b-42d3-a456-426614174001",
    );

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://control.example/api/v1/commands/task.project.move");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body as string)).toEqual({
      resource_ref: task.id,
      destination_project_id: "project_123e4567-e89b-42d3-a456-426614174001",
    });
    const headers = init.headers as Headers;
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(headers.get("X-Correlation-ID")).toBeTruthy();
  });
});
