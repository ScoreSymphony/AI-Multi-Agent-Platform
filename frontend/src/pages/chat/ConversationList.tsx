import type { FormEvent } from "react";
import type { CanonicalConversation } from "../../api/conversations";
import { EmptyState, StatusBadge } from "../../components/States";
import { formatDate } from "./formatting";
import { TARGET_KINDS } from "./targets";

export function ConversationList({
  conversations,
  selectedId,
  busy,
  onCreate,
  onSelect,
}: {
  conversations: CanonicalConversation[];
  selectedId: string | null;
  busy: boolean;
  onCreate: (event: FormEvent<HTMLFormElement>) => void;
  onSelect: (conversationId: string) => void;
}) {
  return (
    <aside className="chat-conversations card" aria-label="Conversations">
      <h2>Conversations</h2>
      <form className="chat-new-form" onSubmit={onCreate}>
        <label>Title<input name="title" required placeholder="New conversation" /></label>
        <label>
          Target
          <select name="target_kind" defaultValue="orchestrator">
            {TARGET_KINDS.map((kind) => <option key={kind}>{kind}</option>)}
          </select>
        </label>
        <label>Target ID<input name="target_id" placeholder="platform / agent_… / task_…" /></label>
        <div className="chat-form-pair">
          <label>Revision<input name="revision" inputMode="numeric" placeholder="optional" /></label>
          <label>Project<input name="project_id" placeholder="optional project_…" /></label>
        </div>
        <button className="primary" disabled={busy}>New conversation</button>
      </form>

      <div className="chat-conversation-list">
        {conversations.length === 0
          ? <EmptyState title="No conversations yet" />
          : conversations.map((conversation) => (
            <button
              className={conversation.id === selectedId
                ? "chat-conversation-row selected"
                : "chat-conversation-row"}
              disabled={busy}
              key={conversation.id}
              onClick={() => onSelect(conversation.id)}
              type="button"
            >
              <span><strong>{conversation.title}</strong><StatusBadge value={conversation.status} /></span>
              <small>{targetLabel(conversation)}</small>
              <small>{formatDate(conversation.updated_at)}</small>
            </button>
          ))}
      </div>
    </aside>
  );
}

function targetLabel(conversation: CanonicalConversation): string {
  const target = conversation.metadata.target;
  if (target && typeof target === "object" && !Array.isArray(target)) {
    const kind = target.kind;
    const id = target.id;
    if (typeof kind === "string" && typeof id === "string") return `${kind}:${id}`;
  }
  if (conversation.default_agent) {
    return `${conversation.default_agent.kind}:${conversation.default_agent.id}`;
  }
  if (conversation.project_id) return `project:${conversation.project_id}`;
  return "private conversation";
}
