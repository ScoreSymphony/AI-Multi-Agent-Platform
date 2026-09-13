import {
  type CanonicalTemplate,
  type TemplateClient,
  type TemplateContent,
} from "../../api/templates";

export type ExportKind =
  | "agent"
  | "agent_team"
  | "workflow"
  | "capability_assignment"
  | "model_routing_profile"
  | "automation"
  | "project"
  | "workspaces";

export interface ExistingTemplateDraft {
  exportKind: ExportKind;
  sourceRef: string;
  templateName: string;
  projectTemplateRef: string;
  projectTemplateRevision: string;
}

export async function createTemplateFromExisting(
  client: TemplateClient,
  draft: ExistingTemplateDraft,
): Promise<CanonicalTemplate> {
  const name = optionalText(draft.templateName);
  if (draft.exportKind === "agent") return client.createFromAgent(draft.sourceRef, { name });
  if (draft.exportKind === "agent_team") return client.createFromAgentTeam(draft.sourceRef, { name });
  if (draft.exportKind === "workflow") return client.createFromWorkflow(draft.sourceRef, { name });
  if (draft.exportKind === "capability_assignment") return client.createFromCapabilityAssignment(draft.sourceRef, { name });
  if (draft.exportKind === "model_routing_profile") return client.createFromModelRoutingProfile(draft.sourceRef, { name });
  if (draft.exportKind === "automation") return client.createFromAutomation(draft.sourceRef, { name });
  if (draft.exportKind === "project") return client.createFromProject(draft.sourceRef, { name });

  const workspaceIds = draft.sourceRef
    .split(/[\n,]/)
    .map((value) => value.trim())
    .filter(Boolean);
  return client.createFromWorkspaces(workspaceIds, {
    name: draft.templateName.trim() || "Workspace structure",
    project_template_id: optionalText(draft.projectTemplateRef),
    project_template_revision: optionalRevision(draft.projectTemplateRevision),
  });
}

export function parseTemplateContent(value: string): TemplateContent {
  const parsed: unknown = JSON.parse(value);
  if (!isRecord(parsed) || typeof parsed.name !== "string" || typeof parsed.template_type !== "string") {
    throw new Error("Template content must be a canonical JSON object with name and template_type");
  }
  return parsed as unknown as TemplateContent;
}

export function optionalRevision(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const revision = Number(trimmed);
  if (!Number.isInteger(revision) || revision < 1) {
    throw new Error("Project Template revision must be a positive integer");
  }
  return revision;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function optionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}
