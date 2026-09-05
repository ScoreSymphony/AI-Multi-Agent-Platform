import { describe, expect, it, vi } from "vitest";
import { TaskProjectReassignmentClient } from "./taskProjectReassignment";
import type { CanonicalTask } from "./types";

const movedTask = {
  id: "task_01J00000000000000000000000",
  type: "task",
  title: "Move me",
  objective: "Verify canonical reassignment",
  status: "ready",
  owner: { type: "user", id: "user:alice" },
  project_id: "project_01J00000000000000000000001",
  correlation_id: "task_01J00000000000000000000000",
  plan_ref: null,
  step_ids: [],
  run_ids: [],
  artifact_ids: [],
  result_ids: [],
  wait_reason: null,
  blocked: false,
  revision: 2,
  created_at: "2026-09-05T20:00:00+00:00",
  updated_at: "2026-09-05T20:01:00+00:00",
} as CanonicalTask;

describe("TaskProjectReassignmentClient", () => {
  it("posts the canonical task.project.move command with idempotency", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(movedTask), { status: 200 }),
    );
    const client = new TaskProjectReassignmentClient({
      baseUrl: "https://control.example",
      fetchImpl: fetchSpy as unknown as typeof fetch,
    });

    const result = await client.move(
      movedTask.id,
      "project_01J00000000000000000000001",
    );

    expect(result).toEqual(movedTask);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://control.example/api/v1/commands/task.project.move");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    const headers = new Headers(init.headers);
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(headers.get("X-Correlation-ID")).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      resource_ref: movedTask.id,
      destination_project_id: "project_01J00000000000000000000001",
    });
  });

  it("uses null to move a Task back to the unprojected personal scope", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ...movedTask, project_id: null }), { status: 200 }),
    );
    const client = new TaskProjectReassignmentClient({
      fetchImpl: fetchSpy as unknown as typeof fetch,
    });

    await client.move(movedTask.id, null);

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      resource_ref: movedTask.id,
      destination_project_id: null,
    });
  });
});
