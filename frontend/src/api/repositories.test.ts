import { describe, expect, it, vi } from "vitest";
import { ControlPlaneCollectionClient } from "./collections";
import { RepositoryCollectionClient } from "./repositories";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RepositoryCollectionClient", () => {
  it("uses only the canonical repositories extension collection", async () => {
    const repository = {
      id: "external_resource_repository-fixture",
      connection_id: "connection_repository-fixture",
      external_resource: {},
      default_branch: "main",
      target_revision: null,
      resolved_revision: "a".repeat(40),
      visibility: "private",
      capabilities: [],
      metadata: {},
    };
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(
        "/api/v1/repositories?limit=20&filter%5Bconnection_id%5D=connection_repository-fixture",
      );
      expect(init?.method).toBe("GET");
      expect(init?.credentials).toBe("include");
      return jsonResponse({ items: [repository], next_cursor: null, total: 1, limit: 20 });
    });
    const client = new RepositoryCollectionClient(
      new ControlPlaneCollectionClient({ fetchImpl }),
    );

    const page = await client.list({
      limit: 20,
      filters: { connection_id: "connection_repository-fixture" },
    });

    expect(page.items).toEqual([repository]);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("encodes canonical repository IDs on detail reads", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/repositories/external_resource%2Ffixture");
      return jsonResponse({ id: "external_resource/fixture" });
    });
    const client = new RepositoryCollectionClient(
      new ControlPlaneCollectionClient({ fetchImpl }),
    );

    await client.get("external_resource/fixture");
  });
});
