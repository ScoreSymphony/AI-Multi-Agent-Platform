import { describe, expect, it, vi } from "vitest";
import { ControlPlaneClient } from "./client";

const taskId = "task_123e4567-e89b-42d3-a456-426614174000";
const projectId = "project_123e4567-e89b-42d3-a456-426614174001";
const modelId = "model-local-coder";
const providerId = "local-openai";

describe("#17 browser Control Plane boundary", () => {
  it("keeps representative reads from every integrated page under /api/v1", async () => {
    const page = { items: [], next_cursor: null, total: 0, limit: 1 };
    const fetchSpy = vi.fn().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify(page), { status: 200 })),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.listProjects({ limit: 1 });
    await client.listWorkspaces({ limit: 1 });
    await client.listTasks({ limit: 1 });
    await client.listRuns({ limit: 1 });
    await client.timeline(taskId, { limit: 1 });
    await client.listReferences("artifacts", { limit: 1 });
    await client.listModels({ limit: 1 });
    await client.listModelProviders({ limit: 1 });
    await client.listUsageRecords({ limit: 1 });
    await client.listUsageAggregates({ limit: 1 });
    await client.listUsageBudgets({ limit: 1 });

    for (const [url, init] of fetchSpy.mock.calls as [string, RequestInit][]) {
      expect(url.startsWith("/api/v1/")).toBe(true);
      expect(url).not.toContain("forge");
      expect(url).not.toContain("mcp");
      expect(url).not.toContain("litellm");
      expect(init.method).toBe("GET");
      expect(init.credentials).toBe("include");
    }
  });

  it("keeps representative mutations idempotent and on canonical routes", async () => {
    const fetchSpy = vi.fn().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({}), { status: 200 })),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.createProject({ name: "Project", owner_type: "user", owner_id: "test" });
    await client.createWorkspace({ project_id: projectId });
    await client.queueTask(taskId);
    await client.disableModel(modelId);
    await client.refreshModelProviderHealth(providerId);

    for (const [url, init] of fetchSpy.mock.calls as [string, RequestInit][]) {
      expect(url.startsWith("/api/v1/")).toBe(true);
      expect(init.method).toBe("POST");
      expect((init.headers as Headers).get("Idempotency-Key")).toBeTruthy();
      expect((init.headers as Headers).get("X-Correlation-ID")).toBeTruthy();
    }
  });
});
