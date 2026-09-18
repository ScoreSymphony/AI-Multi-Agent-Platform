import { describe, expect, it, vi } from "vitest";
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
    const client = new RepositoryCollectionClient({ fetchImpl });

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
    const client = new RepositoryCollectionClient({ fetchImpl });

    await client.get("external_resource/fixture");
  });

  it("routes canonical repository mutations through exact Control Plane commands", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ repository_id: "external_resource_1" }));
    const client = new RepositoryCollectionClient({ fetchImpl });

    await client.createBranch(
      "external_resource_1",
      "feature/web",
      { startRevision: "HEAD", checkout: true, approvalId: "approval_1", idempotencyKey: "branch-key" },
    );
    await client.checkout("external_resource_1", "main", "approval_2", "checkout-key");
    await client.commit(
      "external_resource_1",
      {
        message: "commit from web",
        authorName: "Web User",
        authorEmail: "web@example.invalid",
        approvalId: "approval_3",
      },
      "commit-key",
    );
    await client.push(
      "external_resource_1",
      { remote: "origin", refspec: "HEAD:refs/heads/feature/web", approvalId: "approval_4", idempotencyKey: "push-key" },
    );

    const calls = fetchImpl.mock.calls as [string, RequestInit][];
    expect(calls.map(([url]) => String(url))).toEqual([
      "/api/v1/commands/repository.branch.create",
      "/api/v1/commands/repository.checkout",
      "/api/v1/commands/repository.commit",
      "/api/v1/commands/repository.push",
    ]);
    expect(calls.map(([, init]) => new Headers(init.headers).get("Idempotency-Key"))).toEqual([
      "branch-key",
      "checkout-key",
      "commit-key",
      "push-key",
    ]);
    expect(JSON.parse(String(calls[0][1].body))).toEqual({
      resource_ref: "external_resource_1",
      name: "feature/web",
      start_revision: "HEAD",
      checkout: true,
      approval_id: "approval_1",
    });
    expect(JSON.parse(String(calls[2][1].body))).toEqual({
      resource_ref: "external_resource_1",
      message: "commit from web",
      author_name: "Web User",
      author_email: "web@example.invalid",
      approval_id: "approval_3",
    });
  });

  it("supports canonical local attach, discovery/attach and detach without private provider routes", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ repositories: [], attached: true }));
    const client = new RepositoryCollectionClient({ fetchImpl });

    await client.attachLocal(
      "project_1",
      { name: "scoresymphony", initialize: true, defaultBranch: "main", approvalId: "approval_5" },
      "attach-key",
    );
    await client.discover(
      "connection_1",
      "github",
      { attach: true, approvalId: "approval_6", idempotencyKey: "discover-key" },
    );
    await client.detach("external_resource_1", "approval_7", "detach-key");

    const calls = fetchImpl.mock.calls as [string, RequestInit][];
    expect(calls.map(([url]) => String(url))).toEqual([
      "/api/v1/commands/repository.local.attach",
      "/api/v1/commands/repository.discover",
      "/api/v1/commands/repository.detach",
    ]);
    for (const [url] of calls) {
      expect(String(url)).not.toContain("github.com");
      expect(String(url)).not.toContain("gitlab");
      expect(String(url)).not.toContain("filesystem");
    }
    expect(JSON.parse(String(calls[0][1].body))).toEqual({
      resource_ref: "project_1",
      name: "scoresymphony",
      initialize: true,
      default_branch: "main",
      approval_id: "approval_5",
    });
    expect(JSON.parse(String(calls[1][1].body))).toEqual({
      resource_ref: "connection_1",
      provider_id: "github",
      attach: true,
      approval_id: "approval_6",
    });
    expect(JSON.parse(String(calls[2][1].body))).toEqual({
      resource_ref: "external_resource_1",
      approval_id: "approval_7",
    });
  });

  it("rejects blank repository mutation inputs before transport", () => {
    const fetchImpl = vi.fn();
    const client = new RepositoryCollectionClient({ fetchImpl });

    expect(() => client.createBranch("external_resource_1", " ")).toThrow();
    expect(() => client.checkout("external_resource_1", " ")).toThrow();
    expect(() => client.attachLocal("project_1", { name: " " })).toThrow();
    expect(() => client.discover("connection_1", " ")).toThrow();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

});
