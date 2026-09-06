import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "./browserSession";
import { OnboardingClient, type OnboardingStatus } from "./onboarding";

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function status(state: OnboardingStatus["state"] = "needs_model"): OnboardingStatus {
  return {
    id: "first-run",
    type: "onboarding_status",
    state,
    authenticated_actor_present: true,
    project_count: 0,
    workspace_count: 0,
    local_model_count: 0,
    self_hosted_model_count: 0,
    remote_model_count: 0,
    text_capable_golden_path_model_count: 0,
    usable_golden_path_model_count: 0,
    general_assistant_count: 0,
    executable_general_assistant_count: 0,
    general_assistant_blockers: [],
    selection_required: false,
    selection_kind: null,
    candidate_project_ids: [],
    candidate_workspace_ids: [],
    candidate_agent_ids: [],
    starter_catalog_installed: false,
    installed_model_adapter_ids: ["adapter-test"],
    automatic_remote_provider_selection: false,
    automatic_paid_provider_selection: false,
    guidance: ["Configure a local model."],
  };
}

describe("OnboardingClient", () => {
  it("reads first-run state only through the canonical Control Plane resource", async () => {
    const requests: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      requests.push(String(input));
      return jsonResponse(status());
    });
    const client = new OnboardingClient({ baseUrl: "https://platform.test", fetchImpl });

    await expect(client.status()).resolves.toMatchObject({ state: "needs_model" });
    expect(requests).toEqual(["https://platform.test/api/v1/onboarding/first-run"]);
  });

  it("uses BrowserSession CSRF plus idempotency and sends only SecretReference metadata", async () => {
    const storage = new MemoryStorage();
    storage.setItem("ai-agent-platform.csrf-token", "csrf-test");
    const seen: Array<{ url: string; init: RequestInit }> = [];
    const transport = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      seen.push({ url: String(input), init });
      return jsonResponse({
        id: "model-local",
        type: "model",
        provider_id: "provider-local",
        adapter_id: "adapter-test",
        display_name: "Local model",
        location: "local",
        health: "healthy",
        enabled: true,
        external_paid_provider_selected: false,
        credential_mode: "secret_reference",
      });
    });
    const session = new BrowserSessionClient({
      baseUrl: "https://platform.test",
      fetchImpl: transport,
      storage,
    });
    const client = new OnboardingClient({
      baseUrl: "https://platform.test",
      fetchImpl: session.fetch,
    });

    await client.configureModel({
      adapter_id: "adapter-test",
      provider_id: "provider-local",
      model_config_id: "model-local",
      provider_model: "native-model",
      display_name: "Local model",
      base_url: "http://127.0.0.1:8001/v1",
      location: "local",
      credential_ref: {
        provider: "local-secrets",
        secret_id: "model-token",
        scope: "platform",
        version: "current",
      },
    });

    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe(
      "https://platform.test/api/v1/commands/onboarding.configure-model",
    );
    const headers = new Headers(seen[0].init.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-test");
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(seen[0].init.credentials).toBe("include");
    const body = JSON.parse(String(seen[0].init.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      resource_ref: "first-run",
      adapter_id: "adapter-test",
      location: "local",
      credential_ref: {
        provider: "local-secrets",
        secret_id: "model-token",
        scope: "platform",
        version: "current",
      },
    });
    expect(JSON.stringify(body)).not.toMatch(/password|api[_-]?key|bearer|token_value/i);
  });

  it("keeps starter and first-task mutations on canonical command URLs", async () => {
    const urls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      urls.push(url);
      if (url.endsWith("standard-agent.bootstrap")) {
        return jsonResponse({
          id: "standard-agent-catalog",
          type: "standard_agent_bootstrap_result",
          catalog_version: "1.0.0",
          installed_agent_keys: ["general_assistant"],
          preserved_agent_keys: [],
          installed_team_keys: [],
          preserved_team_keys: [],
          readiness: [],
        });
      }
      if (url.endsWith("standard-agent.clone")) {
        return jsonResponse({
          id: "agent-user",
          type: "agent",
          current_revision: 1,
          project_id: "project-1",
          workspace_id: "workspace-1",
          owner_ref: { type: "user", id: "user-1" },
          created_at: "2026-09-06T00:00:00Z",
          updated_at: "2026-09-06T00:00:00Z",
          revision: {},
        });
      }
      return jsonResponse({
        id: "result-1",
        type: "first_run_result",
        task_id: "task-1",
        task_status: "succeeded",
        run_id: "run-1",
        run_status: "succeeded",
        agent_id: "agent-user",
        workspace_id: "workspace-1",
        project_id: "project-1",
        result_id: "result-1",
        output: { text: "done" },
      });
    });
    const client = new OnboardingClient({ baseUrl: "https://platform.test", fetchImpl });

    await client.bootstrapStandardAgents();
    await client.cloneGeneralAssistant({ project_id: "project-1", workspace_id: "workspace-1" });
    await client.runFirstTask({ objective: "Return a short response." });

    expect(urls).toEqual([
      "https://platform.test/api/v1/commands/standard-agent.bootstrap",
      "https://platform.test/api/v1/commands/standard-agent.clone",
      "https://platform.test/api/v1/commands/onboarding.run-first-task",
    ]);
    expect(urls.join("\n")).not.toMatch(/ollama|lm studio|litellm|hermes|forge|\/v1\/models/i);
  });
});
