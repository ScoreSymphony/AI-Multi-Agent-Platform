import type { CanonicalNotification } from "../../api/notifications";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState } from "../../components/States";
import { formatDate } from "./formatting";

export function AttentionNotifications({
  notifications,
  error,
}: {
  notifications: CanonicalNotification[];
  error: string | null;
}) {
  return (
    <section className="card">
      <h2>Approval &amp; input attention</h2>
      <p className="chat-secondary">
        Canonical Notifications project approval and agent-input requests for Tasks linked to this
        Conversation. Approval state remains owned by the platform approval domain.
      </p>
      {error && <p className="chat-secondary">{error}</p>}
      <div className="chat-activity-list" aria-live="polite">
        {notifications.length === 0
          ? <EmptyState title="No approval or input requests" />
          : notifications.map((item) => (
            <AttentionNotificationItem key={item.id} item={item} />
          ))}
      </div>
    </section>
  );
}

export function AttentionNotificationItem({ item }: { item: CanonicalNotification }) {
  const summary = notificationAttentionSummary(item);
  return (
    <div className="chat-activity-item">
      <div>
        <strong>{item.title}</strong>
        <span className="chat-authoritative">canonical {item.category}</span>
      </div>
      {item.task_id && (
        <AppLink href={`/tasks/${item.task_id}`}><CanonicalId value={item.task_id} /></AppLink>
      )}
      {item.approval_id && (
        <AppLink href={`/approvals/${item.approval_id}`}><CanonicalId value={item.approval_id} /></AppLink>
      )}
      {summary && <small>{summary}</small>}
      {item.actions.filter((action) => action.href !== null).map((action) => (
        <AppLink href={action.href ?? "#"} key={action.action_id}>{action.label}</AppLink>
      ))}
      <small>{formatDate(item.updated_at)}</small>
    </div>
  );
}

function notificationAttentionSummary(item: CanonicalNotification): string | null {
  const attention = item.summary.attention;
  if (typeof attention === "string" && attention.trim()) return attention;
  const action = item.summary.action;
  const risk = item.summary.risk;
  const parts = [
    typeof action === "string" && action.trim() ? `Action: ${action}` : null,
    typeof risk === "string" && risk.trim() ? `Risk: ${risk}` : null,
  ].filter((value): value is string => value !== null);
  return parts.length > 0 ? parts.join(" · ") : null;
}
