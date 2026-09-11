import type { CanonicalConversationMessage } from "../../api/conversations";
import { formatDate } from "./formatting";
import { ReferenceChip, uniqueConversationReferences } from "./references";
import type { TentativeResponseState } from "./state";

export function ConversationMessageView({
  message,
  selected = false,
  onSelect,
}: {
  message: CanonicalConversationMessage;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const references = uniqueConversationReferences(message);
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
