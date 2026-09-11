import type { FormEvent } from "react";
import type {
  CanonicalConversation,
  CanonicalConversationMessage,
  ConversationReferenceKind,
} from "../../api/conversations";
import type { LiveConnectionState } from "../../api/live";
import { CanonicalId, EmptyState, StatusBadge } from "../../components/States";
import { ConversationMessageView, TentativeResponseView } from "./ConversationMessage";
import type { TentativeResponseState } from "./state";
import { REFERENCE_KINDS } from "./targets";

export function ConversationThread({
  selected,
  messages,
  tentative,
  selectedMessageId,
  liveState,
  busy,
  composer,
  referenceKind,
  referenceId,
  deleteArmed,
  onSelectMessage,
  onComposerChange,
  onReferenceKindChange,
  onReferenceIdChange,
  onSend,
  onArchive,
  onReopen,
  onExport,
  onTombstone,
}: {
  selected: CanonicalConversation | null;
  messages: CanonicalConversationMessage[];
  tentative: TentativeResponseState | null;
  selectedMessageId: string | null;
  liveState: LiveConnectionState;
  busy: boolean;
  composer: string;
  referenceKind: ConversationReferenceKind | "";
  referenceId: string;
  deleteArmed: boolean;
  onSelectMessage: (messageId: string) => void;
  onComposerChange: (value: string) => void;
  onReferenceKindChange: (kind: ConversationReferenceKind | "") => void;
  onReferenceIdChange: (value: string) => void;
  onSend: (event: FormEvent<HTMLFormElement>) => void;
  onArchive: () => void;
  onReopen: () => void;
  onExport: () => void;
  onTombstone: () => void;
}) {
  return (
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
                <button disabled={busy} onClick={onArchive}>Archive</button>
              )}
              {selected.status === "archived" && (
                <button disabled={busy} onClick={onReopen}>Reopen</button>
              )}
              <button disabled={busy} onClick={onExport}>Export</button>
              {selected.status !== "tombstoned" && (
                <button className="danger-action" disabled={busy} onClick={onTombstone}>
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
                  onSelect={() => onSelectMessage(message.id)}
                />
              ))}
            {tentative && <TentativeResponseView response={tentative} />}
          </div>

          {selected.status === "open" && (
            <form className="chat-composer" onSubmit={onSend}>
              <label>
                Message
                <textarea
                  value={composer}
                  onChange={(event) => onComposerChange(event.target.value)}
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
                      onReferenceKindChange(event.target.value as ConversationReferenceKind | "")}
                  >
                    <option value="">none</option>
                    {REFERENCE_KINDS.map((kind) => <option key={kind}>{kind}</option>)}
                  </select>
                </label>
                <label>
                  Reference ID
                  <input
                    value={referenceId}
                    onChange={(event) => onReferenceIdChange(event.target.value)}
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
  );
}
