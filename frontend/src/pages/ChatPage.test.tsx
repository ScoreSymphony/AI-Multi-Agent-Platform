import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type {
  ConversationResponseActivityEvent,
  ConversationResponseDeltaEvent,
} from "../api/conversationResponses";
import type { CanonicalConversationMessage, ConversationReference } from "../api/conversations";
import {
  ConversationMessageView,
  TentativeResponseView,
  applyResponseActivity,
  applyResponseDelta,
  buildConversationTarget,
  buildOptionalReference,
  conversationReferenceHref,
  upsertMessage,
} from "./ChatPage";

function message(overrides: Partial<CanonicalConversationMessage> = {}): CanonicalConversationMessage {
  return {
    id: "message_123",
    type: "conversation-message",
    conversation_id: "conversation_123",
    sender_ref: "user:alice",
    role: "user",
    content: [{ kind: "text", text: "Visible conversation text" }],
    references: [],
    model_config_id: null,
    model_provider_ref: null,
    created_at: "2026-09-04T01:00:00+00:00",
    edited_at: null,
    status: "active",
    revision: 1,
    correlation_id: null,
    causation_id: null,
    metadata: {},
    ...overrides,
  };
}

function delta(text: string): ConversationResponseDeltaEvent {
  return {
    id: `response_event_${text}`,
    type: "conversation.response.delta",
    conversation_id: "conversation_123",
    source_message_id: "message_123",
    authoritative: false,
    tentative: true,
    delta: { kind: "text", text },
    model_config_id: "model_config_123",
  };
}

function activity(summary: string): ConversationResponseActivityEvent {
  return {
    id: `response_event_${summary}`,
    type: "conversation.response.activity",
    conversation_id: "conversation_123",
    source_message_id: "message_123",
    authoritative: false,
    tentative: true,
    summary,
    model_config_id: "model_config_123",
  };
}

describe("ChatPage canonical helpers", () => {
  it("normalizes orchestrator and versioned Agent/Team targets without provider sessions", () => {
    expect(buildConversationTarget("orchestrator", "ignored")).toEqual({
      kind: "orchestrator",
      id: "platform",
    });
    expect(buildConversationTarget("agent", " agent_123 ", "4")).toEqual({
      kind: "agent",
      id: "agent_123",
      revision: 4,
    });
    expect(buildConversationTarget("agent_team", "team_123", "2")).toEqual({
      kind: "agent_team",
      id: "team_123",
      revision: 2,
    });
  });

  it("requires explicit IDs for canonical attachment references", () => {
    expect(buildOptionalReference("", "")).toBeNull();
    expect(buildOptionalReference("knowledge", " knowledge_source_123 ")).toEqual({
      kind: "knowledge",
      id: "knowledge_source_123",
    });
    expect(() => buildOptionalReference("file", " ")).toThrow(/reference ID/i);
  });

  it("maps only resources with stable frontend detail routes", () => {
    const base: Omit<ConversationReference, "kind" | "id"> = { label: null, metadata: {} };
    expect(conversationReferenceHref({ ...base, kind: "task", id: "task_123" })).toBe(
      "/tasks/task_123",
    );
    expect(conversationReferenceHref({ ...base, kind: "run", id: "run_123" })).toBe(
      "/runs/run_123",
    );
    expect(conversationReferenceHref({ ...base, kind: "knowledge", id: "knowledge_source_123" }))
      .toBeNull();
  });

  it("never renders tombstoned message content", () => {
    const html = renderToStaticMarkup(
      <ConversationMessageView
        message={message({
          status: "tombstoned",
          content: [{ kind: "text", text: "Sensitive text that must not render" }],
        })}
      />,
    );

    expect(html).toContain("Message redacted by Conversation retention/deletion policy.");
    expect(html).not.toContain("Sensitive text that must not render");
  });

  it("accumulates response deltas while retaining their non-authoritative status", () => {
    const first = applyResponseDelta(null, delta("Hello "));
    const second = applyResponseDelta(first, delta("world"));
    const withActivity = applyResponseActivity(second, activity("Thinking summary"));

    expect(withActivity).toEqual({
      sourceMessageId: "message_123",
      text: "Hello world",
      activity: "Thinking summary",
      modelConfigId: "model_config_123",
    });

    const html = renderToStaticMarkup(<TentativeResponseView response={withActivity} />);
    expect(html).toContain("tentative · not authoritative");
    expect(html).toContain("Hello world");
    expect(html).toContain("Thinking summary");
    expect(html).toContain("data-response-state=\"tentative\"");
  });

  it("upserts the durable committed Assistant message without duplicating it", () => {
    const user = message();
    const assistant = message({
      id: "message_assistant",
      sender_ref: "agent:agent_123",
      role: "assistant",
      content: [{ kind: "text", text: "Committed answer" }],
    });
    const updatedAssistant = message({
      ...assistant,
      content: [{ kind: "text", text: "Committed answer revision" }],
    });

    expect(upsertMessage([user], assistant)).toEqual([user, assistant]);
    expect(upsertMessage([user, assistant], updatedAssistant)).toEqual([user, updatedAssistant]);
  });
});