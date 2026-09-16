import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "./browserSession";
import { SetupClient } from "./setup";

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

const STATUS = {
  id: "initial-setup",
  type: "setup_session",
  current_step: "components",
  steps: [],
  catalog: [],
  registry_items: [],
  active_profile_id: "first-run-auto",
  plan: { actions: [], blocking: false, mutation_required: false },
  readiness: {
    ready: false,
    dashboard_allowed: false,
    canonical_onboarding_state: "needs_model",
    blocking_actions: [],
  },
  updated_at: "2026-09-16T08:00:00+00:00",
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("SetupClient", () => {
  it("uses the authenticated browser transport and idempotent Control Plane mutations", async () => {
    const requests: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      requests.push({ url: String(input), init });
      if (String(input).endsWith("/commands/onboarding.provision-setup")) {
        return jsonResponse({
          id: "setup-noop",
          type: "provisioning_operation",
          replayed: false,
          outcome: null,
          setup: STATUS,
        });
      }
      return jsonResponse(STATUS);
    });
    const storage = new MemoryStorage();
    storage.setItem("ai-agent-platform.csrf-token", "csrf_setup");
    const session = new BrowserSessionClient({ fetchImpl, storage });
    const setup = new SetupClient({ transport: session.transport });

    await setup.status();
    await setup.update({
      current_step: "components",
      registry_items: [{ item_id: "example-plugin", version: "1.0.0" }],
    });
    await setup.provision(["registry:example-plugin@1.0.0"]);
    await setup.validate();

    expect(requests.map((request) => request.url)).toEqual([
      "/api/v1/setup-sessions/initial-setup",
      "/api/v1/commands/onboarding.update-setup-session",
      "/api/v1/commands/onboarding.provision-setup",
      "/api/v1/commands/onboarding.validate-setup",
    ]);
    expect(new Headers(requests[0]?.init.headers).has("X-CSRF-Token")).toBe(false);
    for (const request of requests.slice(1)) {
      const headers = new Headers(request.init.headers);
      expect(headers.get("X-CSRF-Token")).toBe("csrf_setup");
      expect(headers.get("Idempotency-Key")).toBeTruthy();
    }

    expect(JSON.parse(String(requests[1]?.init.body))).toEqual({
      resource_ref: "initial-setup",
      current_step: "components",
      registry_items: [{ item_id: "example-plugin", version: "1.0.0" }],
    });
    expect(JSON.parse(String(requests[2]?.init.body))).toEqual({
      resource_ref: "initial-setup",
      action_ids: ["registry:example-plugin@1.0.0"],
    });
  });
});
