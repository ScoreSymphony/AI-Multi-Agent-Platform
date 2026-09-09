import { describe, expect, it, vi } from "vitest";
import { ContextInspectionClient } from "./context";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ContextInspectionClient", () => {
  it("resolves Run to exact AgentRun bindings and canonical Context Bundles", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      expect(init?.method).toBe("GET");
      expect(init?.credentials).toBe("include");
      if (url.includes("/context-run-bindings?")) {
        expect(url).toContain("filter%5Brun_id%5D=run_650");
        return jsonResponse({
          items: [
            {
              id: "agent_run_650",
              agent_run_id: "agent_run_650",
              run_id: "run_650",
              task_id: "task_650",
              agent_id: "agent_650",
              agent_revision: 3,
              context_bundle_id: "context_bundle_650",
              context_bundle_digest: "bundle-digest",
              resolver_version: "resolver/v1",
              policy_version: "policy/v1",
              orchestrator_adapter_id: "reference-context-orchestrator/v1",
              created_at: "2026-09-08T09:00:00+00:00",
            },
          ],
          next_cursor: null,
          total: 1,
          limit: 100,
        });
      }
      expect(url).toBe("/api/v1/context-bundles/context_bundle_650");
      return jsonResponse({
        id: "context_bundle_650",
        context_bundle_id: "context_bundle_650",
        digest: "bundle-digest",
        task_id: "task_650",
        run_id: "run_650",
        agent_id: "agent_650",
        agent_revision: 3,
        plan_id: null,
        step_id: null,
        skill_bundle_id: null,
        skill_bundle_digest: null,
        entries: [
          {
            ordinal: 0,
            source_type: "task",
            mandatory: true,
            role: "context",
            selection_reason: "canonical task",
            freshness: "current",
            data_classification: "internal",
            estimated_tokens: 20,
            content_bytes: 80,
            hidden: false,
            source_id: "task_650",
            inline_content: null,
          },
        ],
        omissions: [],
        budget: { max_tokens: 64000, max_bytes: 262144, max_items: 128 },
        usage: { estimated_tokens: 20, bytes: 80, items: 1 },
        resolver_version: "resolver/v1",
        policy_version: "policy/v1",
        created_at: "2026-09-08T09:00:00+00:00",
        reproducibility_limited: false,
      });
    });
    const client = new ContextInspectionClient({ fetchImpl });

    const inspection = await client.forRun("run_650");

    expect(inspection.bindings).toHaveLength(1);
    expect(inspection.bindings[0].context_bundle_digest).toBe("bundle-digest");
    expect(inspection.bundles).toHaveLength(1);
    expect(inspection.bundles[0].context_bundle_id).toBe("context_bundle_650");
    expect(inspection.bundles[0].entries[0].inline_content).toBeNull();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
});
