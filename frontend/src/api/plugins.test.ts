import { describe, expect, it, vi } from "vitest";
import { PluginsClient } from "./plugins";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("PluginsClient", () => {
  it("reads installed plugins and discovery candidates through canonical collections", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new PluginsClient({ fetchImpl });

    await client.listPlugins({ limit: 20, cursor: "opaque-plugin-cursor" });
    await client.listCandidates({ limit: 30, cursor: "opaque-candidate-cursor" });

    expect(calls).toEqual([
      "/api/v1/plugins?limit=20&cursor=opaque-plugin-cursor",
      "/api/v1/plugin-candidates?limit=30&cursor=opaque-candidate-cursor",
    ]);
  });

  it("pins installation to the inspected candidate manifest digest", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/plugin.install");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.get("idempotency-key")).toBe("install-key");
      expect(headers.has("x-correlation-id")).toBe(true);
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "plugin.reference",
        manifest_digest: "digest-123",
      });
      return jsonResponse({ id: "plugin.reference", state: "installed" });
    });
    const client = new PluginsClient({ fetchImpl });

    await client.install("plugin.reference", "digest-123", "install-key");
  });

  it("configures only through plugin.configure and does not expect configuration echo", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/plugin.configure");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "plugin.reference",
        configuration: { prefix: "cp:" },
      });
      return jsonResponse({ id: "plugin.reference", configured: true, state: "configured" });
    });
    const client = new PluginsClient({ fetchImpl });

    await client.configure("plugin.reference", { prefix: "cp:" }, "configure-key");
  });

  it("pins enable and update validation to manifest digests", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body)) });
      return jsonResponse({ id: "plugin.reference", compatible: true });
    });
    const client = new PluginsClient({ fetchImpl });

    await client.enable("plugin.reference", "installed-digest", "enable-key");
    await client.validateUpdate("plugin.reference", "candidate-digest", "validate-key");

    expect(calls).toEqual([
      {
        url: "/api/v1/commands/plugin.enable",
        body: { resource_ref: "plugin.reference", manifest_digest: "installed-digest" },
      },
      {
        url: "/api/v1/commands/plugin.validate-update",
        body: { resource_ref: "plugin.reference", manifest_digest: "candidate-digest" },
      },
    ]);
  });

  it.each([
    ["disable", "plugin.disable"],
    ["refreshHealth", "plugin.refresh-health"],
    ["remove", "plugin.remove"],
  ] as const)("forwards %s through the exact canonical command", async (method, command) => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`/api/v1/commands/${command}`);
      expect(JSON.parse(String(init?.body))).toEqual({ resource_ref: "plugin.reference" });
      return jsonResponse({ id: "plugin.reference" });
    });
    const client = new PluginsClient({ fetchImpl });

    await client[method]("plugin.reference", `${method}-key`);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("rejects blank references, digests and idempotency keys before transport", async () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({}));
    const client = new PluginsClient({ fetchImpl });

    expect(() => client.getPlugin(" ")).toThrow("plugin reference is required");
    expect(() => client.install("plugin.reference", " ")).toThrow("plugin manifest digest is required");
    await expect(client.disable("plugin.reference", " ")).rejects.toThrow(
      "plugin idempotency key is required",
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
