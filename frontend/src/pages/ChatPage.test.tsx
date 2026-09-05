import type { AnchorHTMLAttributes } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  ConversationResponseActivityEvent,
  ConversationResponseDeltaEvent,
} from "../api/conversationResponses";
import type {
  CanonicalConversationMessage,
  ConversationReference,
  ConversationTaskEvent,
} from "../api/conversations";
import type { CanonicalNotification } from "../api/notifications";
import {
  ActivityItem,
  AttentionNotificationItem,
  ConversationMessageView,
  TentativeResponseView,
  applyResponseActivity,
  applyResponseDelta,
  buildConversationTarget,
  buildOptionalReference,
  conversationReferenceHref,
  upsertMessage,
} from "./ChatPage";

vi.mock("../app/router", () => ({
  AppLink: ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

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
    expect(conversationReferenceHref({ ...base, kind: "result", id: "result_123" })).toBe(
      "/results/result_123",
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

  it("renders authoritative lifecycle references and waiting attention separately from model text", () => {
    const lifecycle: ConversationTaskEvent = {
      id: "cursor_123",
      type: "conversation.task-event",
      conversation_id: "conversation_123",
      task_id: "task_123",
      authoritative: true,
      event: {
        id: "event_123",
        event_type: "task.waiting",
        occurred_at: "2026-09-04T02:00:00+00:00",
      },
      references: [
        { kind: "result", id: "result_123", label: null, metadata: {} },
      ],
      attention: {
        kind: "task_waiting",
        task_id: "task_123",
        blocked: true,
        reason: "Choose the canonical deployment target",
      },
    };

    const html = renderToStaticMarkup(<ActivityItem item={lifecycle} />);
    expect(html).toContain("task.waiting");
    expect(html).toContain("authoritative");
    expect(html).toContain("result_123");
    expect(html).toContain("User input required");
    expect(html).toContain("Choose the canonical deployment target");
  });

  it("renders canonical approval notifications without inventing a chat-owned approval action", () => {
    const notification: CanonicalNotification = {
      id: "notification_123",
      type: "notification",
      category: "approval",
      severity: "warning",
      title: "Approval required",
      summary: {
        approval_id: "approval_123",
        action: "deploy.release",
        risk: "high",
      },
      state: "unread",
      recipient: { type: "user", id: "alice" },
      source: { resource_type: "approval", resource_id: "approval_123" },
      project_id: "project_123",
      workspace_id: null,
      task_id: "task_123",
      run_id: "run_123",
      approval_id: "approval_123",
      verification_id: null,
      node_id: null,
      automation_id: null,
      membership_id: null,
      resource_ref: { resource_type: "approval", resource_id: "approval_123" },
      actions: [
        {
          action_id: "review",
          label: "Review approval",
          command: null,
          resource_type: "approval",
          resource_id: "approval_123",
          href: "/approvals/approval_123",
        },
      ],
      aggregation_key: "approval:approval_123:pending",
      occurrence_count: 1,
      created_at: "2026-09-04T02:00:00+00:00",
      updated_at: "2026-09-04T02:00:00+00:00",
      read_at: null,
      acknowledged_at: null,
      dismissed_at: null,
      archived_at: null,
      expires_at: null,
      correlation_id: null,
      causation_id: null,
      delivery: { metadata: {}, attempts: [] },
    };

    const html = renderToStaticMarkup(<AttentionNotificationItem item={notification} />);
    expect(html).toContain("Approval required");
    expect(html).toContain("canonical approval");
    expect(html).toContain("approval_123");
    expect(html).toContain("Review approval");
    expect(html).toContain("Action: deploy.release");
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
