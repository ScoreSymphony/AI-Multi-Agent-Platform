import type { FormEvent } from "react";
import type { CanonicalConversation, ConversationTaskEvent } from "../../api/conversations";
import type { CanonicalNotification } from "../../api/notifications";
import type { JsonValue } from "../../api/types";
import { AppLink } from "../../app/router";
import { CanonicalId, EmptyState } from "../../components/States";
import { ActivityFeed } from "./ActivityFeed";
import { AttentionNotifications } from "./AttentionNotifications";
import { ReferenceGroup } from "./references";
import { TaskBridge } from "./TaskBridge";

export function ConversationContext({
  selected,
  activity,
  attentionNotifications,
  attentionError,
  selectedMessageId,
  busy,
  exported,
  onCreateTask,
  onAttachTask,
  onResumeTask,
}: {
  selected: CanonicalConversation | null;
  activity: ConversationTaskEvent[];
  attentionNotifications: CanonicalNotification[];
  attentionError: string | null;
  selectedMessageId: string | null;
  busy: boolean;
  exported: Record<string, JsonValue> | null;
  onCreateTask: (event: FormEvent<HTMLFormElement>) => void;
  onAttachTask: (event: FormEvent<HTMLFormElement>) => void;
  onResumeTask: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <aside className="chat-context" aria-label="Canonical context and activity">
      <section className="card">
        <h2>Canonical context</h2>
        {!selected ? <EmptyState title="No conversation selected" /> : (
          <div className="chat-context-groups">
            <ReferenceGroup label="Tasks" kind="task" ids={selected.task_ids} />
            <ReferenceGroup label="Runs" kind="run" ids={selected.run_ids} />
            <ReferenceGroup label="Artifacts" kind="artifact" ids={selected.artifact_ids} />
            <ReferenceGroup label="Results" kind="result" ids={selected.result_ids} />
            {selected.project_id && (
              <div className="chat-context-group">
                <strong>Project</strong>
                <AppLink href={`/projects/${selected.project_id}`}>
                  <CanonicalId value={selected.project_id} />
                </AppLink>
              </div>
            )}
          </div>
        )}
      </section>

      <ActivityFeed activity={activity} />
      <AttentionNotifications notifications={attentionNotifications} error={attentionError} />
      <TaskBridge
        selectedMessageId={selectedMessageId}
        busy={busy}
        onCreateTask={onCreateTask}
        onAttachTask={onAttachTask}
        onResumeTask={onResumeTask}
      />

      {exported && (
        <section className="card">
          <h2>Portable conversation export</h2>
          <pre className="chat-export">{JSON.stringify(exported, null, 2)}</pre>
        </section>
      )}
    </aside>
  );
}
