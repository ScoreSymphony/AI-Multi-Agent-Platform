import type {
  CanonicalConversationMessage,
  ConversationReference,
  ConversationReferenceKind,
} from "../../api/conversations";
import { AppLink } from "../../app/router";
import { CanonicalId } from "../../components/States";

export function conversationReferenceHref(reference: ConversationReference): string | null {
  if (reference.kind === "task") return `/tasks/${reference.id}`;
  if (reference.kind === "run") return `/runs/${reference.id}`;
  if (reference.kind === "artifact") return `/artifacts/${reference.id}`;
  if (reference.kind === "result") return `/results/${reference.id}`;
  if (reference.kind === "agent") return `/agents/${reference.id}`;
  if (reference.kind === "agent_team") return `/agent-teams/${reference.id}`;
  return null;
}

export function uniqueConversationReferences(
  message: CanonicalConversationMessage,
): ConversationReference[] {
  const values = [...message.references];
  for (const block of message.content) {
    if (block.reference) values.push(block.reference);
  }
  const unique = new Map<string, ConversationReference>();
  for (const reference of values) unique.set(`${reference.kind}:${reference.id}`, reference);
  return [...unique.values()];
}

export function ReferenceGroup({
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

export function ReferenceChip({ reference }: { reference: ConversationReference }) {
  const href = conversationReferenceHref(reference);
  const content = <><span>{reference.kind}</span><CanonicalId value={reference.id} /></>;
  return href
    ? <AppLink className="chat-reference-chip" href={href}>{content}</AppLink>
    : <span className="chat-reference-chip">{content}</span>;
}
