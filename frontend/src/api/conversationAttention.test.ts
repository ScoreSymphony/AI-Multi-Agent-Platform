import { describe, expect, it, vi } from "vitest";
import type { CanonicalNotification, NotificationClient } from "./notifications";
import { listConversationAttentionNotifications } from "./conversationAttention";

function notification(
  id: string,
  overrides: Partial<CanonicalNotification> = {},
): CanonicalNotification {
  return {
    id,
    type: "notification",
    category: "approval",
    severity: "warning",
    title: "Approval required",
    summary: {},
    state: "unread",
    recipient: { type: "user", id: "alice" },
    source: { resource_type: "approval", resource_id: `approval_${id}` },
    project_id: "project_123",
    workspace_id: null,
    task_id: "task_target",
    run_id: null,
    approval_id: null,
    verification_id: null,
    node_id: null,
    automation_id: null,
    membership_id: null,
    resource_ref: null,
    actions: [],
    aggregation_key: null,
    occurrence_count: 1,
    created_at: "2026-09-05T10:00:00+00:00",
    updated_at: "2026-09-05T10:00:00+00:00",
    read_at: null,
    acknowledged_at: null,
    dismissed_at: null,
    archived_at: null,
    expires_at: null,
    correlation_id: null,
    causation_id: null,
    delivery: { metadata: {}, attempts: [] },
    ...overrides,
  };
}

describe("conversation attention pagination", () => {
  it("finds an active linked approval beyond the first notification page", async () => {
    const relevant = notification("notification_relevant", {
      updated_at: "2026-09-04T10:00:00+00:00",
    });
    const list = vi.fn()
      .mockResolvedValueOnce({
        items: [notification("notification_unrelated", { task_id: "task_other" })],
        next_cursor: "opaque-page-2",
        total: 2,
        limit: 100,
      })
      .mockResolvedValueOnce({
        items: [relevant],
        next_cursor: null,
        total: 2,
        limit: 100,
      });
    const client = { list } as Pick<NotificationClient, "list">;

    const items = await listConversationAttentionNotifications(
      client,
      new Set(["task_target"]),
    );

    expect(items).toEqual([relevant]);
    expect(list).toHaveBeenNthCalledWith(2, {
      limit: 100,
      cursor: "opaque-page-2",
      sort: "updated_at",
      direction: "desc",
    });
  });

  it("keeps only active approval and agent-input requests for linked tasks", async () => {
    const activeApproval = notification("notification_approval");
    const activeInput = notification("notification_input", { category: "agent_input" });
    const list = vi.fn().mockResolvedValue({
      items: [
        activeApproval,
        activeInput,
        notification("notification_dismissed", { state: "dismissed" }),
        notification("notification_archived", { state: "archived" }),
        notification("notification_other_task", { task_id: "task_other" }),
        notification("notification_general", { category: "general" }),
      ],
      next_cursor: null,
      total: 6,
      limit: 100,
    });

    const items = await listConversationAttentionNotifications(
      { list } as Pick<NotificationClient, "list">,
      new Set(["task_target"]),
    );

    expect(items.map((item) => item.id)).toEqual([
      "notification_approval",
      "notification_input",
    ]);
  });
});
