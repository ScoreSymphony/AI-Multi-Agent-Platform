import type {
  ConversationReferenceKind,
  ConversationTarget,
  ConversationTargetKind,
} from "../../api/conversations";

export const REFERENCE_KINDS: ConversationReferenceKind[] = [
  "file",
  "artifact",
  "knowledge",
  "task",
  "run",
  "result",
  "agent",
  "agent_team",
];

export const TARGET_KINDS: ConversationTargetKind[] = [
  "orchestrator",
  "agent",
  "agent_team",
  "project",
  "task",
];

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
