import { describe, expect, it, vi } from "vitest";
import { MemoryKnowledgeClient } from "./memoryKnowledge";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("MemoryKnowledgeClient", () => {
  it("forwards scoped Memory reads and opaque cursors through the canonical collection", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v1/memory?limit=25&cursor=opaque-memory-cursor&q=cadence&filter%5Bscope%5D=workspace&filter%5Bscope_id%5D=project-1&filter%5Bproject_id%5D=project-1&filter%5Binclude_expired%5D=true&filter%5Binclude_superseded%5D=true",
      );
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 25 });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.listMemory({
      scope: "workspace",
      scopeId: "project-1",
      projectId: "project-1",
      search: "cadence",
      includeExpired: true,
      includeSuperseded: true,
      limit: 25,
      cursor: "opaque-memory-cursor",
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("keeps Knowledge source inventory distinct from query-scoped retrieval results", async () => {
    const calls: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 50 });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.listKnowledge({ projectId: "project-1", limit: 50 });
    await client.searchKnowledge({
      query: "voice leading",
      mode: "hybrid",
      sourceId: "knowledge-source-1",
      projectId: "project-1",
      cursor: "opaque-result-cursor",
      limit: 20,
    });

    expect(calls).toEqual([
      "/api/v1/knowledge?limit=50&filter%5Bproject_id%5D=project-1",
      "/api/v1/knowledge-results?limit=20&cursor=opaque-result-cursor&q=voice+leading&filter%5Bmode%5D=hybrid&filter%5Bsource_id%5D=knowledge-source-1&filter%5Bproject_id%5D=project-1",
    ]);
  });

  it("creates Memory through memory.create with canonical scope identity and idempotency", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/memory.create");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.get("idempotency-key")).toBe("memory-create-key");
      expect(headers.has("x-correlation-id")).toBe(true);
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "project-1",
        scope: "workspace",
        scope_id: "project-1",
        origin: "user-authored",
        value: { note: "Remember this" },
        retention: "project_lifetime",
        project_id: "project-1",
        classification: "private",
        metadata: { source: "user" },
      });
      return jsonResponse({ id: "memory-1", type: "memory" });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.createMemory(
      {
        scope: "workspace",
        scopeId: "project-1",
        origin: "user-authored",
        value: { note: "Remember this" },
        retention: "project_lifetime",
        projectId: "project-1",
        classification: "private",
        metadata: { source: "user" },
      },
      "memory-create-key",
    );
  });

  it("updates Memory through supersession without allowing an empty mutation", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/memory.update");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "memory-1",
        value: "replacement",
        classification: null,
      });
      return jsonResponse({ id: "memory-2", type: "memory", supersedes_memory_id: "memory-1" });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    expect(() => client.updateMemory("memory-1", {})).toThrow("at least one mutable field");
    expect(fetchImpl).not.toHaveBeenCalled();

    await client.updateMemory(
      "memory-1",
      { value: "replacement", classification: null },
      "memory-update-key",
    );
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("registers project-scoped Knowledge without carrying secret or provider-private identity", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/knowledge.register");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "project-1",
        title: "Harmony notes",
        revision: "1",
        project_id: "project-1",
        metadata: { kind: "notes" },
      });
      return jsonResponse({ id: "knowledge-source-1", type: "knowledge-source" });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.registerKnowledge(
      {
        targetRef: "project-1",
        projectId: "project-1",
        title: "Harmony notes",
        revision: "1",
        metadata: { kind: "notes" },
      },
      "knowledge-register-key",
    );
  });

  it("reindexes and removes Knowledge only through canonical lifecycle commands", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: JSON.parse(String(init?.body)) });
      return jsonResponse({ id: "knowledge-source-1", type: "knowledge-source" });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.reindexKnowledge(
      "knowledge-source-1",
      { revision: "2", content: "updated content", location: "notes.md" },
      "knowledge-reindex-key",
    );
    await client.detachKnowledge("knowledge-source-1", "knowledge-detach-key");

    expect(calls).toEqual([
      {
        url: "/api/v1/commands/knowledge.reindex",
        body: {
          resource_ref: "knowledge-source-1",
          revision: "2",
          content: "updated content",
          location: "notes.md",
        },
      },
      {
        url: "/api/v1/commands/knowledge.detach",
        body: { resource_ref: "knowledge-source-1" },
      },
    ]);
  });

  it("rejects blank retrieval queries before transport", () => {
    const fetchImpl = vi.fn(async () => jsonResponse({}));
    const client = new MemoryKnowledgeClient({ fetchImpl });

    expect(() => client.searchKnowledge({ query: "   " })).toThrow("knowledge query is required");
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
