import { describe, expect, it, vi } from "vitest";
import { OnboardingClient } from "./onboarding";

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("official multi-agent first run API", () => {
  it("uses the canonical onboarding command and preserves explicit scope", async () => {
    const seen: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      seen.push({ url: String(input), init });
      return jsonResponse({
        id: "task-1",
        type: "multi_agent_first_run_result",
        workflow: "reference-multi-agent",
        task_id: "task-1",
        task_status: "succeeded",
        plan_id: "plan-1",
        project_id: "project-1",
        workspace_id: "workspace-1",
        agents: {},
        steps: [],
        result_id: "result-1",
        result_ids: ["result-1"],
        artifact_ids: ["artifact-1"],
        review: {
          step_status: "passed",
          verification_status: "pass",
          verification_id: "verification-1",
        },
        verification: [],
        trace: { task_id: "task-1", plan_id: "plan-1", step_ids: [] },
      });
    });
    const client = new OnboardingClient({ baseUrl: "https://platform.test", fetchImpl });

    await client.runMultiAgentGoldenPath({
      title: "First goal",
      objective: "Research, execute, and review.",
      project_id: "project-1",
      workspace_id: "workspace-1",
    });

    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe(
      "https://platform.test/api/v1/commands/onboarding.run-multi-agent-golden-path",
    );
    expect(new Headers(seen[0].init.headers).get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(seen[0].init.body))).toEqual({
      resource_ref: "first-run",
      title: "First goal",
      objective: "Research, execute, and review.",
      project_id: "project-1",
      workspace_id: "workspace-1",
    });
    expect(seen[0].url).not.toMatch(/hermes|forge|litellm|model-backend/i);
  });
});
