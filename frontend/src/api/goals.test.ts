import { describe, expect, it, vi } from "vitest";
import { GoalClient } from "./goals";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("GoalClient", () => {
  it("reads the canonical goals collection", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/goals?limit=25&sort=id&direction=asc");
      expect(init?.method).toBe("GET");
      expect(init?.credentials).toBe("include");
      return jsonResponse({ items: [], total: 0, next_cursor: null });
    });
    const client = new GoalClient({ fetchImpl });

    await client.list({ limit: 25, sort: "id", direction: "asc" });
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("creates Goals through the canonical command route with idempotency", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/goal.create");
      expect(init?.method).toBe("POST");
      const headers = new Headers(init?.headers);
      expect(headers.has("idempotency-key")).toBe(true);
      expect(headers.has("x-correlation-id")).toBe(true);
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body.resource_ref).toBe("goals");
      expect(body.title).toBe("Maintain repository health");
      return jsonResponse({ id: "goal_1", type: "goal" });
    });
    const client = new GoalClient({ fetchImpl });

    await client.create({
      title: "Maintain repository health",
      objective: "Keep the repository within explicit quality constraints",
      success_criteria: [
        {
          criterion_id: "accepted",
          kind: "human_acceptance",
          description: "A human accepts the maintained state",
          operator: "truthy",
          target: true,
          required: true,
        },
      ],
    });
  });

  it("uses exact lifecycle, revision, review and Task-link commands", async () => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return jsonResponse({ id: "goal_1", type: "goal" });
    });
    const client = new GoalClient({ fetchImpl });

    await client.activate("goal_1");
    await client.pause("goal_1");
    await client.resume("goal_1");
    await client.cancel("goal_1", "operator decision");
    await client.fail("goal_1", "objective is no longer achievable");
    await client.revise("goal_1", {
      expected_revision: 3,
      objective: "Revised objective",
      active_task_policy: "supersede",
    });
    await client.review("goal_1", {
      expected_revision: 4,
      trigger_ref: "manual:test",
      evidence: [],
    });
    await client.attachTask("goal_1", "task_1", 4);

    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/commands/goal.activate",
      "/api/v1/commands/goal.pause",
      "/api/v1/commands/goal.resume",
      "/api/v1/commands/goal.cancel",
      "/api/v1/commands/goal.fail",
      "/api/v1/commands/goal.revise",
      "/api/v1/commands/goal.review",
      "/api/v1/commands/goal.attach-task",
    ]);
    expect(calls[3]?.body).toEqual({ resource_ref: "goal_1", reason: "operator decision" });
    expect(calls[4]?.body).toEqual({
      resource_ref: "goal_1",
      reason: "objective is no longer achievable",
    });
    expect(calls[5]?.body).toMatchObject({
      resource_ref: "goal_1",
      expected_revision: 3,
      active_task_policy: "supersede",
    });
    expect(calls[7]?.body).toEqual({
      resource_ref: "goal_1",
      task_id: "task_1",
      expected_revision: 4,
    });
  });
});
