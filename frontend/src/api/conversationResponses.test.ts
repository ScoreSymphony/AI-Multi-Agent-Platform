import { describe, expect, it, vi } from "vitest";
import {
  ConversationResponseClient,
  parseSSE,
  type ConversationResponseCommittedEvent,
} from "./conversationResponses";
import type { CanonicalConversationMessage } from "./conversations";

function assistantMessage(): CanonicalConversationMessage {
  return {
    id: "message_assistant",
    type: "conversation-message",
    conversation_id: "conversation_123",
    sender_ref: "agent:agent_123",
    role: "assistant",
    content: [{ kind: "text", text: "Hello world" }],
    references: [],
    model_config_id: "model_config_123",
    model_provider_ref: null,
    created_at: "2026-09-04T02:00:00+00:00",
    edited_at: null,
    status: "active",
    revision: 1,
    correlation_id: "correlation_123",
    causation_id: "response-key",
    metadata: { response_to: "message_user" },
  };
}

function streamResponseBody(): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const committed: ConversationResponseCommittedEvent = {
    id: "response_event_committed",
    type: "conversation.response.committed",
    conversation_id: "conversation_123",
    source_message_id: "message_user",
    authoritative: false,
    tentative: false,
    durable: true,
    replayed: false,
    message: assistantMessage(),
  };
  const frames = [
    `event: conversation.response.delta\ndata: ${JSON.stringify({
      id: "response_event_delta",
      type: "conversation.response.delta",
      conversation_id: "conversation_123",
      source_message_id: "message_user",
      authoritative: false,
      tentative: true,
      delta: { kind: "text", text: "Hello " },
      model_config_id: "model_config_123",
    })}\n\n`,
    `event: conversation.response.activity\ndata: ${JSON.stringify({
      id: "response_event_activity",
      type: "conversation.response.activity",
      conversation_id: "conversation_123",
      source_message_id: "message_user",
      authoritative: false,
      tentative: true,
      summary: "Reasoning summary allowed by policy",
      model_config_id: "model_config_123",
    })}\n\n`,
    `event: conversation.response.delta\ndata: ${JSON.stringify({
      id: "response_event_delta_2",
      type: "conversation.response.delta",
      conversation_id: "conversation_123",
      source_message_id: "message_user",
      authoritative: false,
      tentative: true,
      delta: { kind: "text", text: "world" },
      model_config_id: "model_config_123",
    })}\n\n`,
    `event: conversation.response.committed\ndata: ${JSON.stringify(committed)}\n\n`,
  ].join("");

  return new ReadableStream<Uint8Array>({
    start(controller) {
      const bytes = encoder.encode(frames);
      controller.enqueue(bytes.slice(0, 47));
      controller.enqueue(bytes.slice(47, 193));
      controller.enqueue(bytes.slice(193));
      controller.close();
    },
  });
}

describe("ConversationResponseClient", () => {
  it("parses SSE frames split across transport chunks", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("event: one\ndata: {\"a\":"));
        controller.enqueue(encoder.encode("1}\n\nevent: two\ndata: line"));
        controller.enqueue(encoder.encode("\ndata: two\n\n"));
        controller.close();
      },
    });

    const frames = [];
    for await (const frame of parseSSE(body)) frames.push(frame);

    expect(frames).toEqual([
      { event: "one", data: "{\"a\":1}" },
      { event: "two", data: "line\ntwo" },
    ]);
  });

  it("uses explicit POST-SSE idempotency and emits tentative output before the durable commit", async () => {
    const deltas: string[] = [];
    const activities: string[] = [];
    const committed: string[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(String(input)).toBe(
        "/api/v1/conversation-messages/message_user/response/stream",
      );
      expect(init?.method).toBe("POST");
      expect(init?.credentials).toBe("include");
      const headers = new Headers(init?.headers);
      expect(headers.get("accept")).toBe("text/event-stream");
      expect(headers.get("idempotency-key")).toBe("response-key");
      expect(headers.has("x-correlation-id")).toBe(true);
      expect(headers.has("authorization")).toBe(false);
      expect(headers.has("x-provider-session-id")).toBe(false);
      return new Response(streamResponseBody(), {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });
    const client = new ConversationResponseClient({ fetchImpl });

    const result = await client.stream(
      "message_user",
      {
        onDelta: (event) => deltas.push(event.delta.text),
        onActivity: (event) => activities.push(event.summary),
        onCommitted: (event) => committed.push(event.message.id),
      },
      "response-key",
    );

    expect(deltas).toEqual(["Hello ", "world"]);
    expect(activities).toEqual(["Reasoning summary allowed by policy"]);
    expect(committed).toEqual(["message_assistant"]);
    expect(result.message.content[0]?.text).toBe("Hello world");
    expect(result.authoritative).toBe(false);
    expect(result.durable).toBe(true);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("rejects a stream that ends without a durable committed message", async () => {
    const encoder = new TextEncoder();
    const fetchImpl = vi.fn(async () => new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(
            `event: conversation.response.delta\ndata: ${JSON.stringify({
              id: "response_event_delta",
              type: "conversation.response.delta",
              conversation_id: "conversation_123",
              source_message_id: "message_user",
              authoritative: false,
              tentative: true,
              delta: { kind: "text", text: "partial" },
              model_config_id: null,
            })}\n\n`,
          ));
          controller.close();
        },
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    ));
    const client = new ConversationResponseClient({ fetchImpl });

    await expect(client.stream("message_user", {}, "response-key")).rejects.toThrow(
      /without a committed message/i,
    );
  });
});