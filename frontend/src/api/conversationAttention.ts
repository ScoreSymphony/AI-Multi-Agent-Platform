import type { NotificationClient, CanonicalNotification } from "./notifications";

const PAGE_LIMIT = 100;

export async function listConversationAttentionNotifications(
  client: Pick<NotificationClient, "list">,
  taskIds: ReadonlySet<string>,
): Promise<CanonicalNotification[]> {
  if (taskIds.size === 0) return [];

  const matched = new Map<string, CanonicalNotification>();
  const seenCursors = new Set<string>();
  let cursor: string | undefined;

  while (true) {
    const page = await client.list({
      limit: PAGE_LIMIT,
      cursor,
      sort: "updated_at",
      direction: "desc",
    });
    for (const item of page.items) {
      if (
        item.task_id !== null
        && taskIds.has(item.task_id)
        && (item.category === "approval" || item.category === "agent_input")
        && item.state !== "dismissed"
        && item.state !== "archived"
      ) {
        matched.set(item.id, item);
      }
    }

    const next = page.next_cursor ?? undefined;
    if (!next) break;
    if (seenCursors.has(next)) {
      throw new Error("Canonical notification pagination returned a repeated cursor");
    }
    seenCursors.add(next);
    cursor = next;
  }

  return [...matched.values()].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}
