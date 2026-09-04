import { describe, expect, it, vi } from "vitest";
import { IntegrationsClient } from "./integrations";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("IntegrationsClient", () => {
  it("reads connector definitions and connections only through canonical collections", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new IntegrationsClient({ fetchImpl });

    await client.listDefinitions({ limit: 20, cursor: "opaque-definition-cursor" });
    await client.listConnections({ limit: 30, cursor: "opaque-connection-cursor" });

    expect(calls).toEqual([
      "/api/v1/connector-definitions?limit=20&cursor=opaque-definition-cursor",
      "/api/v1/connections?limit=30&cursor=opaque-connection-cursor",
    ]);
  });

  it("creates a Connection through connection.create without embedding secret material", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/connection.create");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.get("idempotency-key")).toBe("connection-create-key");
      expect(headers.has("x-correlation-id")).toBe(true);
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "connections",
        connector_type_id: "reference.local",
        connector_version: "1.0",
        owner_type: "human",
        owner_id: "user:integration-test",
        display_name: "Reference account",
        project_id: "project_123",
        endpoint_metadata: { account: "local-fixture" },
        secret_references: [
          {
            provider: "local-secrets",
            secret_id: "connector-token",
            scope: "project_123",
            version: null,
            metadata: {},
          },
        ],
        requested_scopes: ["read", "write"],
      });
      return jsonResponse({ id: "connection_123", type: "connection" });
    });
    const client = new IntegrationsClient({ fetchImpl });

    await client.createConnection(
      {
        connector_type_id: "reference.local",
        connector_version: "1.0",
        owner_type: "human",
        owner_id: "user:integration-test",
        display_name: "Reference account",
        project_id: "project_123",
        endpoint_metadata: { account: "local-fixture" },
        secret_references: [
          {
            provider: "local-secrets",
            secret_id: "connector-token",
            scope: "project_123",
          },
        ],
        requested_scopes: ["read", "write"],
      },
      "connection-create-key",
    );
  });

  it.each([
    ["enableConnection", "connection.enable"],
    ["disableConnection", "connection.disable"],
    ["removeConnection", "connection.remove"],
  ] as const)("forwards %s through the exact canonical lifecycle command", async (method, command) => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(`/api/v1/commands/${command}`);
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "connection_123",
        approval_id: "approval_123",
      });
      return jsonResponse({ id: "connection_123", type: "connection" });
    });
    const client = new IntegrationsClient({ fetchImpl });

    await client[method]("connection_123", "approval_123", "integration-key");
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("checks health through connection.health", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/connection.health");
      expect(JSON.parse(String(init?.body))).toEqual({ resource_ref: "connection_123" });
      return jsonResponse({ id: "connection_123", health: "healthy" });
    });
    const client = new IntegrationsClient({ fetchImpl });

    await client.checkConnectionHealth("connection_123", "health-key");
  });

  it("synchronizes only through connector.sync and preserves explicit sync mode", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/connector.sync");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "connection_123",
        stream: "records",
        mode: "rebuild",
      });
      return jsonResponse({
        connection_id: "connection_123",
        stream: "records",
        mode: "rebuild",
        cursor: "2",
        status: "succeeded",
        last_successful_sync: "2026-09-04T00:00:00+00:00",
        resource_refs: [],
        events: [],
      });
    });
    const client = new IntegrationsClient({ fetchImpl });

    await client.synchronize("connection_123", "records", "rebuild", "sync-key");
  });

  it("rejects incomplete create input and blank sync stream before transport", () => {
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({}));
    const client = new IntegrationsClient({ fetchImpl });

    expect(() => client.createConnection({
      connector_type_id: "",
      connector_version: "1.0",
      owner_type: "human",
      owner_id: "user:test",
      display_name: "Test",
    })).toThrow("connector type is required");
    expect(() => client.synchronize("connection_123", " ")).toThrow("connector sync stream is required");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
