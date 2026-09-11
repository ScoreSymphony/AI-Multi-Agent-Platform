import type { ConversationTaskEvent } from "../../api/conversations";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState } from "../../components/States";
import { formatDate } from "./formatting";
import { ReferenceChip } from "./references";

export function ActivityFeed({ activity }: { activity: ConversationTaskEvent[] }) {
  return (
    <section className="card">
      <h2>Authoritative task/run activity</h2>
      <p className="chat-secondary">
        These events are projections of canonical Task/Run streams, not chat-owned lifecycle state.
      </p>
      <div className="chat-activity-list" aria-live="polite">
        {activity.length === 0
          ? <EmptyState title="No projected activity yet" />
          : activity.map((item) => <ActivityItem key={item.id} item={item} />)}
      </div>
    </section>
  );
}

export function ActivityItem({ item }: { item: ConversationTaskEvent }) {
  const occurredAt = typeof item.event.occurred_at === "string"
    ? item.event.occurred_at
    : typeof item.event.timestamp === "string"
      ? item.event.timestamp
      : null;
  return (
    <div className="chat-activity-item">
      <div>
        <strong>{item.event.event_type}</strong>
        <span className="chat-authoritative">authoritative</span>
      </div>
      <AppLink href={`/tasks/${item.task_id}`}><CanonicalId value={item.task_id} /></AppLink>
      {item.references.length > 0 && (
        <div className="chat-reference-chips">
          {item.references.map((reference) => (
            <ReferenceChip key={`${item.id}:${reference.kind}:${reference.id}`} reference={reference} />
          ))}
        </div>
      )}
      {item.attention && (
        <div role="status">
          <strong>User input required</strong>
          {item.attention.reason && <p>{item.attention.reason}</p>}
          {item.attention.verification_state && (
            <small>Verification: {item.attention.verification_state}</small>
          )}
        </div>
      )}
      {occurredAt && <small>{formatDate(occurredAt)}</small>}
    </div>
  );
}
