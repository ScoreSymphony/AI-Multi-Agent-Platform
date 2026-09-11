import { describe, expect, it } from "vitest";
import type {
  ConversationResponseActivityEvent,
  ConversationResponseDeltaEvent,
} from "../../api/conversationResponses";
import type { CanonicalConversationMessage } from "../../api/conversations";
import { applyResponseActivity, applyResponseDelta, upsertMessage } from "./state";

function delta(sourceMessageId: string, text: string): ConversationResponseDeltaEvent {
  return {
    id: `response-delta-${sourceMessageId}-${text}`,
    type: "conversation.response.delta",
    conversation_id: "conversation-1",
    source_message_id: sourceMessageId,
    authoritative: false,
    tentative: true,
    delta: { kind: "text", text },
    model_config_id: `model-${sourceMessageId}`,
  };
}

function activity(sourceMessageId: string, summary: string): ConversationResponseActivityEvent {
  return {
    id: `response-activity-${sourceMessageId}`,
    type: "conversation.response.activity",
    conversation_id: "conversation-1",
    source_message_id: sourceMessageId,
    authoritative: false,
    tentative: true,
    summary,
    model_config_id: `model-${sourceMessageId}`,
  };
}

function message(id: string, text: string): CanonicalConversationMessage {
  return {
    id,
    type: "conversation-message",
    conversation_id: "conversation-1",
    sender_ref: "user:test",
    role: "user",
    content: [{ kind: "text", text }],
    references: [],
    model_config_id: null,
    model_provider_ref: null,
    created_at: "2026-09-11T00:00:00+00:00",
    edited_at: null,
    status: "active",
    revision: 1,
    correlation_id: null,
    causation_id: null,
    metadata: {},
  };
}

describe("chat tentative response state", () => {
  it("never merges tentative response state across different source messages", () => {
    const first = applyResponseDelta(null, delta("message-a", "partial answer"));
    const second = applyResponseActivity(first, activity("message-b", "new activity"));

    expect(second).toEqual({
      sourceMessageId: "message-b",
      text: "",
      activity: "new activity",
      modelConfigId: "model-message-b",
    });
  });

  it("replaces an authoritative message in place without disturbing sibling order", () => {
    const first = message("message-a", "first");
    const second = message("message-b", "second");
    const replacement = message("message-b", "updated second");

    expect(upsertMessage([first, second], replacement)).toEqual([first, replacement]);
  });
});
