import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "./browserSession";
import { ControlPlaneClient } from "./client";

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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BrowserSessionClient", () => {
  it("binds the native global fetch receiver for real browser calls", async () => {
    const nativeFetch = vi.fn(function (this: typeof globalThis) {
      expect(this).toBe(globalThis);
      return Promise.resolve(jsonResponse({ ok: true }));
    }) as unknown as typeof fetch;
    vi.stubGlobal("fetch", nativeFetch);
    const session = new BrowserSessionClient({ storage: null });

    await session.fetch("/api/v1/health");

    expect(nativeFetch).toHaveBeenCalledOnce();
  });

  it("stores the login CSRF token and injects it into later cookie mutations", async () => {
    const requests: RequestInit[] = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(init ?? {});
      if (requests.length === 1) {
        return jsonResponse({
          actor: {
            actor_id: "user_1",
            actor_type: "human",
            authentication_method: "browser_session",
            credential_id: "session_1",
            authenticated_at: "2026-09-03T18:00:00+00:00",
            expires_at: "2026-09-03T19:00:00+00:00",
            organization_id: null,
            project_id: null,
            request_id: "request_1",
            correlation_id: "correlation_1",
            provider_metadata: {},
          },
          csrf_token: "csrf_123",
          expires_at: "2026-09-03T19:00:00+00:00",
        });
      }
      return jsonResponse({ ok: true });
    });
    const storage = new MemoryStorage();
    const session = new BrowserSessionClient({ fetchImpl, storage });

    await session.login("alice", "correct horse battery staple");
    await session.fetch("/api/v1/tasks/task_1:cancel", { method: "POST" });

    const loginHeaders = new Headers(requests[0]?.headers);
    const mutationHeaders = new Headers(requests[1]?.headers);
    expect(loginHeaders.has("x-csrf-token")).toBe(false);
    expect(mutationHeaders.get("x-csrf-token")).toBe("csrf_123");
    expect(session.hasCsrfToken()).toBe(true);
  });

  it("reads the current shared browser CSRF token before each unsafe request", async () => {
    const storage = new MemoryStorage();
    storage.setItem("ai-agent-platform.csrf-token", "csrf_initial");
    const seen: Array<string | null> = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      seen.push(new Headers(init?.headers).get("x-csrf-token"));
      return jsonResponse({ ok: true });
    });
    const firstTab = new BrowserSessionClient({ fetchImpl, storage });
    const secondTab = new BrowserSessionClient({ fetchImpl, storage });

    await firstTab.fetch("/api/v1/tasks/task_1:cancel", { method: "POST" });
    storage.setItem("ai-agent-platform.csrf-token", "csrf_rotated");
    await secondTab.fetch("/api/v1/tasks/task_2:cancel", { method: "POST" });
    await firstTab.fetch("/api/v1/tasks/task_3:cancel", { method: "POST" });

    expect(seen).toEqual(["csrf_initial", "csrf_rotated", "csrf_rotated"]);
  });

  it("applies the stored CSRF token to existing ControlPlaneClient commands", async () => {
    const storage = new MemoryStorage();
    storage.setItem("ai-agent-platform.csrf-token", "csrf_stored");
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("x-csrf-token")).toBe("csrf_stored");
      expect(headers.has("idempotency-key")).toBe(true);
      return jsonResponse({ id: "task_1", status: "cancelled" });
    });
    const session = new BrowserSessionClient({ fetchImpl, storage });
    const client = new ControlPlaneClient({ fetchImpl: session.fetch });

    await client.cancelTask("task_1");

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("does not attach a browser CSRF token to bearer-authenticated mutations", async () => {
    const storage = new MemoryStorage();
    storage.setItem("ai-agent-platform.csrf-token", "csrf_stored");
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("authorization")).toBe("Bearer token_1");
      expect(headers.has("x-csrf-token")).toBe(false);
      return jsonResponse({ ok: true });
    });
    const session = new BrowserSessionClient({ fetchImpl, storage });

    await session.fetch("/api/v1/tasks", {
      method: "POST",
      headers: { Authorization: "Bearer token_1" },
    });
  });

  it("uses canonical cookie-authenticated mobile pairing and revocation routes", async () => {
    const storage = new MemoryStorage();
    storage.setItem("ai-agent-platform.csrf-token", "csrf_stored");
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init: init ?? {} });
      const url = String(input);
      if (url.endsWith("/auth/mobile-pairings")) {
        return jsonResponse({
          id: "pairing_1",
          server_origin: "https://platform.example",
          code: "ABCD-EFGH-JKLM",
          pairing_uri: "amp-mobile://pair?server=https%3A%2F%2Fplatform.example&id=pairing_1&code=ABCD-EFGH-JKLM&v=1",
          protocol_version: "1",
          expires_at: "2026-09-21T10:10:00+00:00",
          secret_display: "one_time",
        }, 201);
      }
      if (url.endsWith("/auth/mobile-devices")) {
        return jsonResponse({ items: [] });
      }
      if (url.endsWith(":revoke")) {
        return jsonResponse({ id: "mobile_device_1", revoked: true });
      }
      return jsonResponse({ id: "pairing_1", cancelled: true });
    });
    const session = new BrowserSessionClient({
      baseUrl: "https://platform.example/api/v1",
      fetchImpl,
      storage,
    });

    const pairing = await session.createMobilePairing("https://platform.example");
    await session.cancelMobilePairing(pairing.id);
    expect(await session.listMobileDevices()).toEqual([]);
    await session.revokeMobileDevice("mobile_device_1");

    expect(calls.map((call) => call.url)).toEqual([
      "https://platform.example/api/v1/auth/mobile-pairings",
      "https://platform.example/api/v1/auth/mobile-pairings/pairing_1:cancel",
      "https://platform.example/api/v1/auth/mobile-devices",
      "https://platform.example/api/v1/auth/mobile-devices/mobile_device_1:revoke",
    ]);
    const mutationHeaders = calls
      .filter((call) => call.init.method === "POST")
      .map((call) => new Headers(call.init.headers).get("x-csrf-token"));
    expect(mutationHeaders).toEqual(["csrf_stored", "csrf_stored", "csrf_stored"]);
  });

  it("clears the stored CSRF token after successful logout", async () => {
    const storage = new MemoryStorage();
    storage.setItem("ai-agent-platform.csrf-token", "csrf_stored");
    const fetchImpl = vi.fn(async () => jsonResponse({ logged_out: true }));
    const session = new BrowserSessionClient({ fetchImpl, storage });

    await session.logout();

    expect(session.hasCsrfToken()).toBe(false);
    expect(storage.getItem("ai-agent-platform.csrf-token")).toBeNull();
  });
});
