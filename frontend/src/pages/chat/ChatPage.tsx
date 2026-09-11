import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { listConversationAttentionNotifications } from "../../api/conversationAttention";
import {
  ConversationClient,
  ConversationEventStream,
  type CanonicalConversation,
  type CanonicalConversationMessage,
  type ConversationReferenceKind,
  type ConversationTargetKind,
  type ConversationTaskEvent,
} from "../../api/conversations";
import { NotificationEventStream, type LiveConnectionState } from "../../api/live";
import { NotificationClient, type CanonicalNotification } from "../../api/notifications";
import type { JsonValue } from "../../api/types";
import { ErrorState, LoadingState } from "../../components/States";
import "../ChatPage.css";
import { attentionErrorMessage } from "./attention";
import { ConversationContext } from "./ConversationContext";
import { ConversationList } from "./ConversationList";
import { ConversationThread } from "./ConversationThread";
import {
  applyResponseActivity,
  applyResponseDelta,
  upsertMessage,
  type TentativeResponseState,
} from "./state";
import { buildConversationTarget, buildOptionalReference } from "./targets";

export function ChatPage({ client }: { client: ConversationClient }) {
  const notificationClient = useMemo(
    () => new NotificationClient({ baseUrl: client.baseUrl }),
    [client.baseUrl],
  );
  const [conversations, setConversations] = useState<CanonicalConversation[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<CanonicalConversationMessage[]>([]);
  const [activity, setActivity] = useState<ConversationTaskEvent[]>([]);
  const [attentionNotifications, setAttentionNotifications] = useState<CanonicalNotification[]>([]);
  const [attentionError, setAttentionError] = useState<string | null>(null);
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

  const loadAttention = useCallback(async () => {
    const taskIds = new Set(selectedTaskKey ? selectedTaskKey.split("|") : []);
    if (!selectedId || taskIds.size === 0) {
      setAttentionNotifications([]);
      setAttentionError(null);
      return;
    }
    try {
      setAttentionNotifications(
        await listConversationAttentionNotifications(notificationClient, taskIds),
      );
      setAttentionError(null);
    } catch (nextError) {
      setAttentionNotifications([]);
      setAttentionError(attentionErrorMessage(nextError));
    }
  }, [notificationClient, selectedId, selectedTaskKey]);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    setActivity([]);
    setAttentionNotifications([]);
    setAttentionError(null);
    setTentative(null);
    setExported(null);
    setDeleteArmed(false);
    void loadSelected();
  }, [loadSelected]);

  useEffect(() => {
    void loadAttention();
  }, [loadAttention]);

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
        void loadAttention();
      },
    });
    stream.open();
    return () => stream.close();
  }, [client.baseUrl, loadAttention, loadSelected, selectedId, selectedStatus, selectedTaskKey]);

  useEffect(() => {
    if (!selectedId || selectedStatus === "tombstoned" || !selectedTaskKey) return;
    const stream = new NotificationEventStream({
      baseUrl: client.baseUrl,
      onEvent: () => {
        void loadAttention();
      },
      onError: (nextError) => setAttentionError(attentionErrorMessage(nextError)),
    });
    stream.open();
    return () => stream.close();
  }, [client.baseUrl, loadAttention, selectedId, selectedStatus, selectedTaskKey]);

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
        <ConversationList
          conversations={conversations ?? []}
          selectedId={selectedId}
          busy={busy}
          onCreate={createConversation}
          onSelect={setSelectedId}
        />
        <ConversationThread
          selected={selected}
          messages={messages}
          tentative={tentative}
          selectedMessageId={selectedMessageId}
          liveState={liveState}
          busy={busy}
          composer={composer}
          referenceKind={referenceKind}
          referenceId={referenceId}
          deleteArmed={deleteArmed}
          onSelectMessage={setSelectedMessageId}
          onComposerChange={setComposer}
          onReferenceKindChange={setReferenceKind}
          onReferenceIdChange={setReferenceId}
          onSend={sendMessage}
          onArchive={() => void mutateConversation("archive")}
          onReopen={() => void mutateConversation("reopen")}
          onExport={() => void exportConversation()}
          onTombstone={() => void tombstone()}
        />
        <ConversationContext
          selected={selected}
          activity={activity}
          attentionNotifications={attentionNotifications}
          attentionError={attentionError}
          selectedMessageId={selectedMessageId}
          busy={busy}
          exported={exported}
          onCreateTask={createTask}
          onAttachTask={attachTask}
          onResumeTask={resumeTask}
        />
      </div>
    </div>
  );
}
