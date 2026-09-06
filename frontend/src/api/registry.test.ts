import { describe, expect, it, vi } from "vitest";
import { BrowserSessionClient } from "./browserSession";
import { RegistryClient } from "./registry";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RegistryClient", () => {
  it("reads Registry items through the fixed canonical collection", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new RegistryClient({ fetchImpl });

    await client.list({ limit: 20, cursor: "registry-cursor" });
    await client.get("example.asset", "1.2.3");

    expect(calls).toEqual([
      "/api/v1/registry-items?limit=20&cursor=registry-cursor",
      "/api/v1/registry-items/example.asset%401.2.3",
    ]);
  });

  it("previews and activates the exact Registry version through canonical commands", async () => {
    const calls: Array<{ url: string; body: unknown; idempotency: string | null }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({
        url: String(input),
        body: JSON.parse(String(init?.body)),
        idempotency: headers.get("Idempotency-Key"),
      });
      if (String(input).endsWith("registry.preview")) {
        return jsonResponse({
          id: "example.asset@1.2.3",
          type: "registry-preview",
          provider_id: "local",
          route: "portable_import",
          activation_allowed: true,
          findings: [],
        });
      }
      return jsonResponse({
        id: "example.asset@1.2.3",
        type: "registry-activation",
        status: "applied",
        route: "portable_import",
      });
    });
    const client = new RegistryClient({ fetchImpl });

    await client.preview("example.asset", "1.2.3", "registry-preview-key");
    await client.activate("example.asset", "1.2.3", "registry-activate-key");

    expect(calls).toEqual([
      {
        url: "/api/v1/commands/registry.preview",
        body: { resource_ref: "example.asset", version: "1.2.3" },
        idempotency: "registry-preview-key",
      },
      {
        url: "/api/v1/commands/registry.activate",
        body: { resource_ref: "example.asset", version: "1.2.3" },
        idempotency: "registry-activate-key",
      },
    ]);
  });

  it("inherits the shared BrowserSession CSRF boundary for Registry activation", async () => {
    const csrfStorage = new Map<string, string>([
      ["ai-agent-platform.csrf-token", "csrf-registry-test"],
    ]);
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/registry.activate");
      const headers = new Headers(init?.headers);
      expect(headers.get("X-CSRF-Token")).toBe("csrf-registry-test");
      expect(headers.get("Idempotency-Key")).toBe("registry-browser-key");
      expect(init?.credentials).toBe("include");
      return jsonResponse({
        id: "example.asset@1.2.3",
        type: "registry-activation",
        status: "applied",
        route: "portable_import",
      });
    });
    const session = new BrowserSessionClient({
      fetchImpl,
      storage: {
        getItem: (key) => csrfStorage.get(key) ?? null,
        setItem: (key, value) => void csrfStorage.set(key, value),
        removeItem: (key) => void csrfStorage.delete(key),
      },
    });
    const client = new RegistryClient({ fetchImpl: session.fetch });

    await client.activate("example.asset", "1.2.3", "registry-browser-key");
  });

  it("rejects blank identities and idempotency keys before transport", async () => {
    const fetchImpl = vi.fn();
    const client = new RegistryClient({ fetchImpl });

    await expect(client.preview(" ", "1.2.3", "key")).rejects.toThrow("Registry item ID");
    await expect(client.activate("example.asset", " ", "key")).rejects.toThrow(
      "Registry version",
    );
    await expect(client.activate("example.asset", "1.2.3", " ")).rejects.toThrow(
      "idempotency",
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
