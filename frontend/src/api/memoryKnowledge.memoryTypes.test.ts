import { describe, expect, it, vi } from "vitest";
import { MemoryKnowledgeClient } from "./memoryKnowledge";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("MemoryKnowledgeClient Memory Type support", () => {
  it("forwards canonical memory_type filters through the Memory collection", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v1/memory?filter%5Bscope%5D=user&filter%5Bscope_id%5D=user-a&filter%5Bmemory_type%5D=procedural",
      );
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 100 });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.listMemory({
      scope: "user",
      scopeId: "user-a",
      memoryType: "procedural",
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("omits memory_type when the caller requests no type filter", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/memory?filter%5Bscope%5D=user");
      expect(decodeURIComponent(String(input))).not.toContain("filter[memory_type]");
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 100 });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.listMemory({ scope: "user" });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("sends canonical memory_type when creating Memory", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/commands/memory.create");
      expect(JSON.parse(String(init?.body))).toEqual({
        resource_ref: "user-a",
        scope: "user",
        scope_id: "user-a",
        origin: "user-authored",
        memory_type: "preference",
        value: { response_style: "compact" },
      });
      return jsonResponse({
        id: "memory-1",
        type: "memory",
        memory_type: "preference",
      });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.createMemory(
      {
        scope: "user",
        scopeId: "user-a",
        origin: "user-authored",
        memoryType: "preference",
        value: { response_style: "compact" },
      },
      "memory-type-create-key",
    );

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("does not send memory_type through ordinary update or promotion commands", async () => {
    const bodies: Record<string, unknown>[] = [];
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      bodies.push(body);
      expect(Object.prototype.hasOwnProperty.call(body, "memory_type")).toBe(false);
      return jsonResponse({
        id: `memory-${bodies.length + 1}`,
        type: "memory",
        memory_type: "procedural",
      });
    });
    const client = new MemoryKnowledgeClient({ fetchImpl });

    await client.updateMemory(
      "memory-1",
      { value: { workflow: "updated" } },
      "memory-type-update-key",
    );
    await client.promoteMemory(
      "memory-1",
      { scope: "user", scopeId: "user-a" },
      "memory-type-promote-key",
    );

    expect(bodies).toHaveLength(2);
  });
});
