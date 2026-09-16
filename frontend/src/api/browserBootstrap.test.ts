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

const ACTOR = {
  actor_id: "user_1",
  actor_type: "human",
  authentication_method: "browser_session",
  credential_id: "session_1",
  authenticated_at: "2026-09-16T07:00:00+00:00",
  expires_at: "2026-09-16T08:00:00+00:00",
  organization_id: null,
  project_id: null,
  request_id: "request_1",
  correlation_id: "correlation_1",
  provider_metadata: {},
};

describe("browser first-user bootstrap", () => {
  it("reads public initialization state and adopts bootstrap CSRF for the new session", async () => {
    const requests: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith("/auth/bootstrap-status")) {
        return jsonResponse({
          state: "uninitialized",
          bootstrap_available: true,
          password_policy: { min_length: 12, max_bytes: 1024 },
        });
      }
      if (url.endsWith("/auth/bootstrap-admin")) {
        return jsonResponse(
          {
            actor: ACTOR,
            csrf_token: "csrf_bootstrap",
            expires_at: ACTOR.expires_at,
            authorization_granted: true,
          },
          201,
        );
      }
      return jsonResponse({ ok: true });
    });
    const storage = new MemoryStorage();
    const session = new BrowserSessionClient({ fetchImpl, storage });

    const status = await session.bootstrapStatus();
    expect(status.state).toBe("uninitialized");
    expect(status.password_policy.min_length).toBe(12);

    const result = await session.bootstrapAdmin("alice", "correct horse battery staple");
    expect(result.authorization_granted).toBe(true);
    expect(result.actor.actor_id).toBe("user_1");
    expect(session.hasCsrfToken()).toBe(true);

    await session.fetch("/api/v1/onboarding/configure", { method: "POST" });

    expect(requests[0]?.url).toContain("/auth/bootstrap-status");
    expect(new Headers(requests[0]?.init.headers).has("x-csrf-token")).toBe(false);
    expect(new Headers(requests[1]?.init.headers).has("x-csrf-token")).toBe(false);
    expect(new Headers(requests[2]?.init.headers).get("x-csrf-token")).toBe("csrf_bootstrap");
  });
});
