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

const model = {
  id: "model-local-coder",
  config_id: "model-local-coder",
  type: "model",
  display_name: "Local coder",
  provider_id: "local-openai",
  capabilities: {
    context_window: 32768,
    tool_calling: true,
    structured_output: true,
    streaming: true,
    modalities: ["text"],
    reasoning: [],
  },
  revision: 1,
  aliases: ["coder"],
  location: "self_hosted",
  node_ref: null,
  health: "healthy",
  enabled: true,
  priority: 10,
  resource_hints: {},
  cost_metadata: {},
  adapter_metadata: [],
  effective_health: "healthy",
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

  it("reads model inventory through the canonical model collection", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [model], next_cursor: null, total: 1, limit: 100 }), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    const page = await client.listModels({ limit: 100 });

    expect(page.items[0]?.id).toBe("model-local-coder");
    const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/models?limit=100");
  });

  it("uses idempotent Control Plane commands for model enablement", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ...model, enabled: false }), { status: 200 }),
    );
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.disableModel("model-local-coder");

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/models/model-local-coder:disable");
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("Idempotency-Key")).toBeTruthy();
  });

  it("refreshes provider health through the canonical provider command", async () => {
    const provider = {
      id: "local-openai",
      type: "model-provider",
      provider_type: "openai-compatible",
      contract_version: "1",
      supported_operations: ["generate"],
      capabilities: [],
      health: "healthy",
      enabled: true,
      available: true,
      limits: {},
      resources: {},
      adapter_metadata: [],
    };
    const fetchSpy = vi.fn().mockResolvedValue(new Response(JSON.stringify(provider), { status: 200 }));
    const client = new ControlPlaneClient({ fetchImpl: fetchSpy as unknown as typeof fetch });

    await client.refreshModelProviderHealth("local-openai");

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/model-providers/local-openai:refresh-health");
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("Idempotency-Key")).toBeTruthy();
  });
});
