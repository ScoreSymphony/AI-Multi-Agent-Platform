import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "./browserSession";

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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("browser first-run bootstrap", () => {
  it("reads only the minimal public initialization state", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toContain("/api/v1/auth/bootstrap-status");
      expect(init?.method).toBe("GET");
      return jsonResponse({
        state: "uninitialized",
        bootstrap_available: true,
        password_policy: { minimum_length: 12, maximum_bytes: 1024 },
      });
    });
    const session = new BrowserSessionClient({ fetchImpl, storage: null });

    await expect(session.bootstrapStatus()).resolves.toEqual({
      state: "uninitialized",
      bootstrap_available: true,
      password_policy: { minimum_length: 12, maximum_bytes: 1024 },
    });
  });

  it("creates the first administrator and adopts the returned CSRF session", async () => {
    const storage = new MemoryStorage();
    const requests: Array<{ input: string; init: RequestInit }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ input: String(input), init: init ?? {} });
      if (requests.length === 1) {
        return jsonResponse(
          {
            actor: {
              actor_id: "user_1",
              actor_type: "human",
              authentication_method: "browser_session",
              credential_id: "session_1",
              authenticated_at: "2026-09-16T08:00:00+00:00",
              expires_at: "2026-09-16T20:00:00+00:00",
              organization_id: null,
              project_id: null,
              request_id: "request_1",
              correlation_id: "correlation_1",
              provider_metadata: {},
            },
            csrf_token: "csrf_bootstrap",
            expires_at: "2026-09-16T20:00:00+00:00",
            authorization_granted: true,
          },
          201,
        );
      }
      return jsonResponse({ ok: true });
    });
    const session = new BrowserSessionClient({ fetchImpl, storage });

    const result = await session.bootstrapAdmin(
      "admin",
      "correct horse battery staple",
      "correct horse battery staple",
    );
    await session.fetch("/api/v1/tasks/task_1:cancel", { method: "POST" });

    expect(result.authorization_granted).toBe(true);
    expect(requests[0]?.input).toContain("/api/v1/auth/bootstrap-admin");
    expect(JSON.parse(String(requests[0]?.init.body))).toEqual({
      username: "admin",
      password: "correct horse battery staple",
      password_confirmation: "correct horse battery staple",
    });
    expect(new Headers(requests[0]?.init.headers).has("x-csrf-token")).toBe(false);
    expect(new Headers(requests[1]?.init.headers).get("x-csrf-token")).toBe("csrf_bootstrap");
    expect(storage.getItem("ai-agent-platform.csrf-token")).toBe("csrf_bootstrap");
  });
});
