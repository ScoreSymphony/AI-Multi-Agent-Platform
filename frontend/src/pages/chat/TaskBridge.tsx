import type { FormEvent } from "react";
import { CanonicalId } from "../../components/States";

export function TaskBridge({
  selectedMessageId,
  busy,
  onCreateTask,
  onAttachTask,
  onResumeTask,
}: {
  selectedMessageId: string | null;
  busy: boolean;
  onCreateTask: (event: FormEvent<HTMLFormElement>) => void;
  onAttachTask: (event: FormEvent<HTMLFormElement>) => void;
  onResumeTask: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <section className="card">
      <h2>Explicit Task bridge</h2>
      <p className="chat-secondary">
        Choose a user message first. Sending text alone never creates, attaches or resumes a Task.
      </p>
      <div className="chat-selected-message">
        {selectedMessageId ? <CanonicalId value={selectedMessageId} /> : "No user message selected"}
      </div>
      <form className="chat-task-form" onSubmit={onCreateTask}>
        <label>Task title<input name="task_title" required disabled={busy || !selectedMessageId} /></label>
        <label>
          Objective
          <textarea name="task_objective" rows={3} required disabled={busy || !selectedMessageId} />
        </label>
        <button className="primary" disabled={busy || !selectedMessageId}>Create canonical Task</button>
      </form>
      <form className="chat-task-form compact" onSubmit={onAttachTask}>
        <label>
          Existing Task
          <input name="task_id" required placeholder="task_…" disabled={busy || !selectedMessageId} />
        </label>
        <button disabled={busy || !selectedMessageId}>Attach Task</button>
      </form>
      <form className="chat-task-form compact" onSubmit={onResumeTask}>
        <label>
          Waiting Task
          <input
            name="waiting_task_id"
            required
            placeholder="task_…"
            disabled={busy || !selectedMessageId}
          />
        </label>
        <button disabled={busy || !selectedMessageId}>Provide input &amp; resume</button>
      </form>
    </section>
  );
}
