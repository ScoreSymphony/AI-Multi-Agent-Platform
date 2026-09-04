import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import type {
  ConversationResponseActivityEvent,
  ConversationResponseDeltaEvent,
} from "../api/conversationResponses";
import {
  ConversationClient,
  ConversationEventStream,
  type CanonicalConversation,
  type CanonicalConversationMessage,
  type ConversationReference,
  type ConversationReferenceKind,
  type ConversationTarget,
  type ConversationTargetKind,
  type ConversationTaskEvent,
} from "../api/conversations";
import type { LiveConnectionState } from "../api/live";
import type { JsonValue } from "../api/types";
import { AppLink } from "../app/router";
import {
  CanonicalId,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
} from "../components/States";
import "./ChatPage.css";

const REFERENCE_KINDS: ConversationReferenceKind[] = [
  "file",
  "artifact",
  "knowledge",
  "task",
  "run",
  "result",
  "agent",
  "agent_team",
];

const TARGET_KINDS: ConversationTargetKind[] = [
  "orchestrator",
  "agent",
  "agent_team",
  "project",
  "task",
];

export interface TentativeResponseState {
  sourceMessageId: string;
  text: string;
  activity: string | null;
  modelConfigId: string | null;
}

export function ChatPage({ client }: { client: ConversationClient }) {
  const [conversations, setConversations] = useState<CanonicalConversation[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<CanonicalConversationMessage[]>([]);
  const [activity, setActivity] = useState<ConversationTaskEvent[]>([]);
  const [tentative, setTentative] = useState<TentativeResponseState | null>(null);
  const [liveState, setLiveState] = useState<LiveConnectionState>("closed");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [composer, setComposer] = useState("");
  const [referenceKind, setReferenceKind] = useState<ConversationReferenceKind | "">("");
  const [referenceId, setReferenceId] = useState("");
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [exported, setExported] = useState<Record<string, JsonValue> | null>(null);

  const selected = useMemo(
    () => conversations?.find((conversation) => conversation.id === selectedId) ?? null,
    [conversations, selectedId],
  );
  const selectedStatus = selected?.status ?? null;
  const selectedTaskKey = selected?.task_ids.join("|") ?? "";

  const loadConversations = useCallback(async () => {
    try {
      const page = await client.list(true);
      setConversations(page.items);
      setSelectedId((current) => {
        if (current && page.items.some((conversation) => conversation.id === current)) return current;
        return page.items.find((conversation) => conversation.status !== "tombstoned")?.id
          ?? page.items[0]?.id
          ?? null;
      });
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client]);

  const loadSelected = useCallback(async () => {
    if (!selectedId) {
      setMessages([]);
      return;
    }
    try {
      const [conversation, page] = await Promise.all([
        client.get(selectedId),
        client.listMessages(selectedId),
      ]);
      setConversations((current) =>
        current?.map((item) => (item.id === conversation.id ? conversation : item)) ?? [conversation],
      );
      setMessages(page.items);
      setSelectedMessageId((current) => {
        if (current && page.items.some((message) => message.id === current)) return current;
        return [...page.items].reverse().find((message) => message.role === "user")?.id ?? null;
      });
      setError(null);
    } catch (nextError) {
      setError(nextError);
    }
  }, [client, selectedId]);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    setActivity([]);
    setTentative(null);
    setExported(null);
    setDeleteArmed(false);
    void loadSelected();
  }, [loadSelected]);

  useEffect(() => {
    if (!selectedId || selectedStatus === "tombstoned" || !selectedTaskKey) {
      setLiveState("closed");
      return;
    }
    const stream = new ConversationEventStream({
      baseUrl: client.baseUrl,
      conversationId: selectedId,
      onState: setLiveState,
      onError: setError,
      onEvent: (event) => {
        setActivity((current) => {
          if (current.some((item) => item.id === event.id)) return current;
          return [...current, event].slice(-100);
        });
        void loadSelected();
      },
    });
    stream.open();
    return () => stream.close();
  }, [client.baseUrl, loadSelected, selectedId, selectedStatus, selectedTaskKey]);

  const createConversation = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    setError(null);
    try {
      const kind = String(form.get("target_kind") ?? "orchestrator") as ConversationTargetKind;
      const target = buildConversationTarget(
        kind,
        String(form.get("target_id") ?? ""),
        String(form.get("revision") ?? ""),
      );
      const projectId = String(form.get("project_id") ?? "").trim();
      const created = await client.create({
        title: String(form.get("title") ?? "").trim(),
        project_id: projectId || undefined,
        target,
      });
      await loadConversations();
      setSelectedId(created.id);
      formElement.reset();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const sendMessage = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected || !composer.trim() || selected.status !== "open") return;
    setBusy(true);
    setError(null);
    setTentative(null);
    try {
      const reference = buildOptionalReference(referenceKind, referenceId);
      const created = await client.addMessage(selected.id, {
        content: [{ kind: "text", text: composer.trim() }],
        references: reference ? [reference] : undefined,
      });
      setMessages((current) => upsertMessage(current, created));
      setSelectedMessageId(created.id);
      setComposer("");
      setReferenceKind("");
      setReferenceId("");

      await client.streamResponse(created.id, {
        onDelta: (responseEvent) => setTentative((current) => applyResponseDelta(current, responseEvent)),
        onActivity: (responseEvent) =>
          setTentative((current) => applyResponseActivity(current, responseEvent)),
        onCommitted: (responseEvent) => {
          setMessages((current) => upsertMessage(current, responseEvent.message));
          setTentative(null);
        },
      });
      await Promise.all([loadConversations(), loadSelected()]);
    } catch (nextError) {
      setTentative(null);
      setError(nextError);
      await loadConversations();
    } finally {
      setBusy(false);
    }
  };

  const mutateConversation = async (action: "archive" | "reopen") => {
    if (!selected) return;
    setBusy(true);
    try {
      if (action === "archive") await client.archive(selected.id);
      else await client.reopen(selected.id);
      await Promise.all([loadConversations(), loadSelected()]);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const tombstone = async () => {
    if (!selected) return;
    if (!deleteArmed) {
      setDeleteArmed(true);
      return;
    }
    setBusy(true);
    try {
      await client.delete(selected.id);
      setDeleteArmed(false);
      await Promise.all([loadConversations(), loadSelected()]);
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const exportConversation = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      setExported(await client.export(selected.id));
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const createTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedMessageId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    try {
      await client.createTask(selectedMessageId, {
        title: String(form.get("task_title") ?? "").trim(),
        objective: String(form.get("task_objective") ?? "").trim(),
      });
      await Promise.all([loadConversations(), loadSelected()]);
      formElement.reset();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const attachTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedMessageId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    try {
      await client.attachTask(selectedMessageId, String(form.get("task_id") ?? "").trim());
      await Promise.all([loadConversations(), loadSelected()]);
      formElement.reset();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  const resumeTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedMessageId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    try {
      await client.resumeTask(selectedMessageId, String(form.get("waiting_task_id") ?? "").trim());
      await Promise.all([loadConversations(), loadSelected()]);
      formElement.reset();
    } catch (nextError) {
      setError(nextError);
    } finally {
      setBusy(false);
    }
  };

  if (!conversations && !error) return <LoadingState label="Loading conversations…" />;

  return (
    <div className="stack chat-page">
      <header className="page-header chat-page-header">
        <div>
          <p className="eyebrow">Task-centric interaction</p>
          <h1>Chat</h1>
          <p>
            Conversation history is an interaction surface. Durable work and lifecycle state remain
            canonical Tasks, Runs and events.
          </p>
        </div>
      </header>

      {error != null && <ErrorState error={error} onRetry={() => void loadSelected()} />}

      <div className="chat-layout">
        <aside className="chat-conversations card" aria-label="Conversations">
          <h2>Conversations</h2>
          <form className="chat-new-form" onSubmit={createConversation}>
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
            {(conversations ?? []).length === 0
              ? <EmptyState title="No conversations yet" />
              : (conversations ?? []).map((conversation) => (
                <button
                  className={conversation.id === selectedId
                    ? "chat-conversation-row selected"
                    : "chat-conversation-row"}
                  disabled={busy}
                  key={conversation.id}
                  onClick={() => setSelectedId(conversation.id)}
                  type="button"
                >
                  <span><strong>{conversation.title}</strong><StatusBadge value={conversation.status} /></span>
                  <small>{targetLabel(conversation)}</small>
                  <small>{formatDate(conversation.updated_at)}</small>
                </button>
              ))}
          </div>
        </aside>

        <section className="chat-thread card" aria-label="Conversation thread">
          {!selected ? (
            <EmptyState title="Select or create a conversation" />
          ) : (
            <>
              <div className="chat-thread-header">
                <div>
                  <p className="eyebrow">Conversation</p>
                  <h2>{selected.title}</h2>
                  <div className="chat-meta-line">
                    <CanonicalId value={selected.id} />
                    <StatusBadge value={selected.status} />
                    <span className={`live live-${liveState}`}>{liveState}</span>
                  </div>
                </div>
                <div className="actions">
                  {selected.status === "open" && (
                    <button disabled={busy} onClick={() => void mutateConversation("archive")}>Archive</button>
                  )}
                  {selected.status === "archived" && (
                    <button disabled={busy} onClick={() => void mutateConversation("reopen")}>Reopen</button>
                  )}
                  <button disabled={busy} onClick={() => void exportConversation()}>Export</button>
                  {selected.status !== "tombstoned" && (
                    <button className="danger-action" disabled={busy} onClick={() => void tombstone()}>
                      {deleteArmed ? "Confirm tombstone" : "Delete chat history"}
                    </button>
                  )}
                </div>
              </div>

              <div className="chat-boundary-note" role="note">
                Assistant deltas are tentative model output and never authoritative lifecycle state.
                Task actions remain explicit commands subject to canonical authorization.
              </div>

              <div className="chat-messages" aria-live="polite">
                {messages.length === 0 && !tentative
                  ? <EmptyState title="No messages yet" />
                  : messages.map((message) => (
                    <ConversationMessageView
                      key={message.id}
                      message={message}
                      selected={message.id === selectedMessageId}
                      onSelect={() => setSelectedMessageId(message.id)}
                    />
                  ))}
                {tentative && <TentativeResponseView response={tentative} />}
              </div>

              {selected.status === "open" && (
                <form className="chat-composer" onSubmit={sendMessage}>
                  <label>
                    Message
                    <textarea
                      value={composer}
                      onChange={(event) => setComposer(event.target.value)}
                      placeholder="Ask, refine a goal, or provide context…"
                      rows={4}
                      required
                      disabled={busy}
                    />
                  </label>
                  <div className="chat-reference-row">
                    <label>
                      Canonical reference
                      <select
                        value={referenceKind}
                        disabled={busy}
                        onChange={(event) =>
                          setReferenceKind(event.target.value as ConversationReferenceKind | "")}
                      >
                        <option value="">none</option>
                        {REFERENCE_KINDS.map((kind) => <option key={kind}>{kind}</option>)}
                      </select>
                    </label>
                    <label>
                      Reference ID
                      <input
                        value={referenceId}
                        onChange={(event) => setReferenceId(event.target.value)}
                        placeholder="file_… / knowledge_source_… / task_…"
                        disabled={busy || !referenceKind}
                      />
                    </label>
                    <button className="primary" disabled={busy || !composer.trim()}>
                      {busy ? "Working…" : "Send"}
                    </button>
                  </div>
                </form>
              )}
            </>
          )}
        </section>

        <aside className="chat-context" aria-label="Canonical context and activity">
          <section className="card">
            <h2>Canonical context</h2>
            {!selected ? <EmptyState title="No conversation selected" /> : (
              <div className="chat-context-groups">
                <ReferenceGroup label="Tasks" kind="task" ids={selected.task_ids} />
                <ReferenceGroup label="Runs" kind="run" ids={selected.run_ids} />
                <ReferenceGroup label="Artifacts" kind="artifact" ids={selected.artifact_ids} />
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

          <section className="card">
            <h2>Explicit Task bridge</h2>
            <p className="chat-secondary">
              Choose a user message first. Sending text alone never creates, attaches or resumes a Task.
            </p>
            <div className="chat-selected-message">
              {selectedMessageId ? <CanonicalId value={selectedMessageId} /> : "No user message selected"}
            </div>
            <form className="chat-task-form" onSubmit={createTask}>
              <label>Task title<input name="task_title" required disabled={busy || !selectedMessageId} /></label>
              <label>
                Objective
                <textarea name="task_objective" rows={3} required disabled={busy || !selectedMessageId} />
              </label>
              <button className="primary" disabled={busy || !selectedMessageId}>Create canonical Task</button>
            </form>
            <form className="chat-task-form compact" onSubmit={attachTask}>
              <label>
                Existing Task
                <input name="task_id" required placeholder="task_…" disabled={busy || !selectedMessageId} />
              </label>
              <button disabled={busy || !selectedMessageId}>Attach Task</button>
            </form>
            <form className="chat-task-form compact" onSubmit={resumeTask}>
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

          {exported && (
            <section className="card">
              <h2>Portable conversation export</h2>
              <pre className="chat-export">{JSON.stringify(exported, null, 2)}</pre>
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}

export function ConversationMessageView({
  message,
  selected = false,
  onSelect,
}: {
  message: CanonicalConversationMessage;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const references = uniqueReferences(message);
  return (
    <article className={`chat-message chat-message-${message.role}${selected ? " selected" : ""}`}>
      <header>
        <div><strong>{message.role}</strong><small>{message.sender_ref}</small></div>
        <time dateTime={message.created_at}>{formatDate(message.created_at)}</time>
      </header>
      {message.status === "tombstoned" ? (
        <p className="chat-redacted">Message redacted by Conversation retention/deletion policy.</p>
      ) : (
        <div className="chat-message-content">
          {message.content.map((block, index) => {
            if ((block.kind === "text" || block.kind === "markdown") && block.text) {
              return <p key={`${message.id}:content:${index}`}>{block.text}</p>;
            }
            if (block.kind === "json") {
              return <pre key={`${message.id}:content:${index}`}>{JSON.stringify(block.value, null, 2)}</pre>;
            }
            if (block.reference) {
              return <ReferenceChip key={`${message.id}:content:${index}`} reference={block.reference} />;
            }
            return null;
          })}
          {references.length > 0 && (
            <div className="chat-reference-chips">
              {references.map((reference) => (
                <ReferenceChip key={`${reference.kind}:${reference.id}`} reference={reference} />
              ))}
            </div>
          )}
        </div>
      )}
      {message.role === "user" && message.status !== "tombstoned" && onSelect && (
        <button className="chat-message-action" type="button" onClick={onSelect}>
          {selected ? "Selected for Task bridge" : "Use for Task bridge"}
        </button>
      )}
    </article>
  );
}

export function TentativeResponseView({ response }: { response: TentativeResponseState }) {
  return (
    <article className="chat-message chat-message-assistant" data-response-state="tentative">
      <header>
        <div><strong>assistant</strong><small>tentative · not authoritative</small></div>
        {response.modelConfigId && <small>{response.modelConfigId}</small>}
      </header>
      <div className="chat-message-content">
        {response.text ? <p>{response.text}</p> : <p className="chat-secondary">Preparing response…</p>}
        {response.activity && <p className="chat-secondary">{response.activity}</p>}
      </div>
    </article>
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
      {occurredAt && <small>{formatDate(occurredAt)}</small>}
    </div>
  );
}

function ReferenceGroup({
  label,
  kind,
  ids,
}: {
  label: string;
  kind: ConversationReferenceKind;
  ids: string[];
}) {
  return (
    <div className="chat-context-group">
      <strong>{label}</strong>
      {ids.length === 0 ? <span className="chat-secondary">none</span> : ids.map((id) => {
        const href = conversationReferenceHref({ kind, id, label: null, metadata: {} });
        return href
          ? <AppLink href={href} key={id}><CanonicalId value={id} /></AppLink>
          : <CanonicalId value={id} key={id} />;
      })}
    </div>
  );
}

function ReferenceChip({ reference }: { reference: ConversationReference }) {
  const href = conversationReferenceHref(reference);
  const content = <><span>{reference.kind}</span><CanonicalId value={reference.id} /></>;
  return href
    ? <AppLink className="chat-reference-chip" href={href}>{content}</AppLink>
    : <span className="chat-reference-chip">{content}</span>;
}

export function applyResponseDelta(
  current: TentativeResponseState | null,
  event: ConversationResponseDeltaEvent,
): TentativeResponseState {
  const base = current?.sourceMessageId === event.source_message_id
    ? current
    : {
        sourceMessageId: event.source_message_id,
        text: "",
        activity: null,
        modelConfigId: null,
      };
  return {
    ...base,
    text: `${base.text}${event.delta.text}`,
    modelConfigId: event.model_config_id ?? base.modelConfigId,
  };
}

export function applyResponseActivity(
  current: TentativeResponseState | null,
  event: ConversationResponseActivityEvent,
): TentativeResponseState {
  const base = current?.sourceMessageId === event.source_message_id
    ? current
    : {
        sourceMessageId: event.source_message_id,
        text: "",
        activity: null,
        modelConfigId: null,
      };
  return {
    ...base,
    activity: event.summary,
    modelConfigId: event.model_config_id ?? base.modelConfigId,
  };
}

export function upsertMessage(
  messages: CanonicalConversationMessage[],
  message: CanonicalConversationMessage,
): CanonicalConversationMessage[] {
  const index = messages.findIndex((item) => item.id === message.id);
  if (index < 0) return [...messages, message];
  return messages.map((item, itemIndex) => (itemIndex === index ? message : item));
}

export function buildConversationTarget(
  kind: ConversationTargetKind,
  rawId: string,
  rawRevision = "",
): ConversationTarget {
  if (kind === "orchestrator") return { kind, id: "platform" };
  const id = rawId.trim();
  if (!id) throw new Error(`${kind} target requires a canonical ID`);
  if (kind === "agent" || kind === "agent_team") {
    const revisionText = rawRevision.trim();
    if (!revisionText) return { kind, id };
    const revision = Number(revisionText);
    if (!Number.isInteger(revision) || revision < 1) {
      throw new Error("Agent/Team revision must be a positive integer");
    }
    return { kind, id, revision };
  }
  return { kind, id };
}

export function buildOptionalReference(
  kind: ConversationReferenceKind | "",
  rawId: string,
): { kind: ConversationReferenceKind; id: string } | null {
  if (!kind) return null;
  const id = rawId.trim();
  if (!id) throw new Error("A canonical reference kind requires a reference ID");
  return { kind, id };
}

export function conversationReferenceHref(reference: ConversationReference): string | null {
  if (reference.kind === "task") return `/tasks/${reference.id}`;
  if (reference.kind === "run") return `/runs/${reference.id}`;
  if (reference.kind === "artifact") return `/artifacts/${reference.id}`;
  if (reference.kind === "result") return `/results/${reference.id}`;
  if (reference.kind === "agent") return `/agents/${reference.id}`;
  if (reference.kind === "agent_team") return `/agent-teams/${reference.id}`;
  return null;
}

function uniqueReferences(message: CanonicalConversationMessage): ConversationReference[] {
  const values = [...message.references];
  for (const block of message.content) {
    if (block.reference) values.push(block.reference);
  }
  const unique = new Map<string, ConversationReference>();
  for (const reference of values) unique.set(`${reference.kind}:${reference.id}`, reference);
  return [...unique.values()];
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

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}