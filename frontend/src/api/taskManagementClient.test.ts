import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const taskId = "task_123e4567-e89b-42d3-a456-426614174000";

const managedTask = {
  id: taskId,
  type: "task",
  title: "Managed",
  objective: "Verify management client",
  status: "draft",
  owner: { type: "user", id: "test" },
  project_id: null,
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
  created_at: "2026-09-03T00:00:00+00:00",
  updated_at: "2026-09-03T00:01:00+00:00",
  priority: "urgent",
};

describe("Task management Control Plane client", () => {
  it("updates planning metadata through the canonical built-in command", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(managedTask), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.updateTaskManagement(taskId, { priority: "urgent", labels: ["release"] });

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/commands/task-management.update");
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(init.body))).toEqual({
      resource_ref: taskId,
      priority: "urgent",
      labels: ["release"],
    });
  });

  it("bulk-updates only through the canonical bulk command", async () => {
    const result = {
      id: "bulk:test",
      type: "task-management-bulk-result",
      atomic: false,
      authorization_preflighted: true,
      count: 1,
      items: [{ task_id: taskId, eligible: true }],
    };
    const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), { status: 200 }));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.bulkUpdateTaskManagement([
      { task_id: taskId, changes: { archived: true } },
    ]);

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/commands/task-management.bulk-update");
    expect(JSON.parse(String(init.body))).toEqual({
      resource_ref: "tasks",
      updates: [{ task_id: taskId, changes: { archived: true } }],
    });
  });
});
