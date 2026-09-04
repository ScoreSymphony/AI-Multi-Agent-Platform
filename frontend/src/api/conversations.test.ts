import { describe, expect, it, vi } from "vitest";
import { ConversationClient, buildConversationStreamUrl } from "./conversations";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ConversationClient", () => {
  it("creates conversations through the canonical route without private session identity", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/conversations");
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.has("idempotency-key")).toBe(true);
      expect(headers.has("x-correlation-id")).toBe(true);
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body).toEqual({
        title: "Agent conversation",
        target: { kind: "agent", id: "agent_123", revision: 4 },
      });
      expect(body).not.toHaveProperty("session_id");
      expect(body).not.toHaveProperty("provider_session_id");
      return jsonResponse({ id: "conversation_123", type: "conversation" }, 201);
    });
    const client = new ConversationClient({ fetchImpl });

    await client.create({
      title: "Agent conversation",
      target: { kind: "agent", id: "agent_123", revision: 4 },
    });

    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("lists active and archived conversations through the canonical collection", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      expect(url.pathname).toBe("/api/v1/conversations");
      expect(url.searchParams.get("filter[include_archived]")).toBe("true");
      expect(url.searchParams.get("sort")).toBe("updated_at");
      return jsonResponse({ items: [], next_cursor: null, total: 0, limit: 100 });
    });
    const client = new ConversationClient({ fetchImpl });

    await client.list(true);
  });

  it("never sends sender or assistant role when posting a user message", async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe("/api/v1/conversations/conversation_123/messages");
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      expect(body).toEqual({
        content: [{ kind: "text", text: "Please inspect this." }],
        references: [{ kind: "file", id: "file_123" }],
      });
      expect(body).not.toHaveProperty("sender_ref");
      expect(body).not.toHaveProperty("role");
      return jsonResponse({ id: "message_123", type: "conversation-message" }, 201);
    });
    const client = new ConversationClient({ fetchImpl });

    await client.addMessage("conversation_123", {
      content: [{ kind: "text", text: "Please inspect this." }],
      references: [{ kind: "file", id: "file_123" }],
    });
  });

  it("uses explicit canonical task bridge routes for create, attach and resume", async () => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        body: JSON.parse(String(init?.body)) as Record<string, unknown>,
      });
      return jsonResponse({ id: "task_123" });
    });
    const client = new ConversationClient({ fetchImpl });

    await client.createTask("message_123", { title: "Task", objective: "Do durable work" });
    await client.attachTask("message_123", "task_456");
    await client.resumeTask("message_123", "task_789");

    expect(calls).toEqual([
      {
        url: "/api/v1/conversation-messages/message_123:create-task",
        body: { title: "Task", objective: "Do durable work" },
      },
      {
        url: "/api/v1/conversation-messages/message_123:attach-task",
        body: { task_id: "task_456" },
      },
      {
        url: "/api/v1/conversation-messages/message_123:resume-task",
        body: { task_id: "task_789" },
      },
    ]);
  });

  it("builds a provider-neutral conversation SSE URL with an opaque cursor", () => {
    const url = buildConversationStreamUrl(
      "https://platform.example",
      "conversation_123",
      "opaque-cursor",
      "https://ui.example",
    );

    expect(url.origin).toBe("https://platform.example");
    expect(url.pathname).toBe("/api/v1/conversations/conversation_123/events/stream");
    expect(url.searchParams.get("after_event_id")).toBe("opaque-cursor");
  });
});
