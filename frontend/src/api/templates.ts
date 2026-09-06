import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, OwnerType, Page } from "./types";

export type TemplateType =
  | "agent"
  | "agent_team"
  | "workflow_plan"
  | "project"
  | "workspace_structure"
  | "automation"
  | "model_routing_policy"
  | "capability_assignment"
  | "composite";

export type TemplateRevisionState = "draft" | "published";
export type TemplateTrust = "local" | "trusted" | "untrusted";

export interface TemplateRevisionRef {
  template_id: string;
  revision: number;
}

export interface TemplateDependency {
  template_id: string;
  revision: number | null;
  optional: boolean;
}

export interface TemplateCapabilityRequirement {
  capability_id: string;
  optional: boolean;
  version_constraint: string | null;
  privileged: boolean;
}

export interface TemplateRequirements {
  capabilities: TemplateCapabilityRequirement[];
  plugin_ids: string[];
  connector_ids: string[];
  model_policy_refs: string[];
  permission_actions: string[];
  workspace_prerequisites: string[];
  placeholders: string[];
  secret_reference_placeholders: string[];
}

export interface TemplateCompatibility {
  platform_version_range: string | null;
  contract_versions: Record<string, string>;
  orchestrator_agnostic: boolean;
  provider_agnostic: boolean;
  metadata: Record<string, JsonValue>;
}

export interface TemplateProvenance {
  author: string;
  source: string;
  trust: TemplateTrust;
  source_template: TemplateRevisionRef | null;
  metadata: Record<string, JsonValue>;
}

export interface TemplateConfiguration {
  payload: Record<string, JsonValue> | null;
  reference: string | null;
}

export interface TemplateContent {
  name: string;
  description: string;
  template_type: TemplateType;
  configuration: TemplateConfiguration;
  dependencies: TemplateDependency[];
  requirements: TemplateRequirements;
  compatibility: TemplateCompatibility;
  provenance: TemplateProvenance;
  tags: string[];
  categories: string[];
}

export interface TemplateRevision {
  template_id: string;
  revision: number;
  state: TemplateRevisionState;
  owner_ref: { type: OwnerType; id: string };
  content: TemplateContent;
  project_id: string | null;
  organization_id: string | null;
  created_at: string;
}

export interface CanonicalTemplate {
  id: string;
  type: "template" | string;
  current_revision: number;
  latest_published_revision: number | null;
  owner_ref: { type: OwnerType; id: string };
  project_id: string | null;
  organization_id: string | null;
  created_at: string;
  updated_at: string;
  revision: TemplateRevision;
  revisions: TemplateRevision[];
}

export interface TemplateResourceChange {
  resource_type: string;
  action: string;
  description: string | null;
}

export interface TemplatePreview {
  source: TemplateRevisionRef;
  dependency_order: TemplateRevisionRef[];
  missing_required_capability_ids: string[];
  missing_optional_capability_ids: string[];
  incompatible_capability_versions: string[];
  incompatible_optional_capability_versions: string[];
  incompatible_platform_versions: string[];
  missing_contract_versions: string[];
  incompatible_contract_versions: string[];
  missing_plugin_ids: string[];
  missing_connector_ids: string[];
  missing_model_policy_refs: string[];
  ungrantable_permissions: string[];
  missing_workspace_prerequisites: string[];
  unresolved_placeholders: string[];
  unresolved_secret_reference_placeholders: string[];
  unvalidated_configuration_refs: string[];
  missing_optional_dependencies: string[];
  missing_handler_types: string[];
  privileged_capability_ids: string[];
  resource_changes: TemplateResourceChange[];
  applicable: boolean;
}

export interface TemplateResourceRef {
  resource_type: string;
  resource_id: string;
}

export interface TemplateInstantiation {
  id: string;
  type: "template-instance" | string;
  source: TemplateRevisionRef;
  applied_by: { type: OwnerType; id: string };
  resource_refs: TemplateResourceRef[];
  instance_id: string;
  created_at: string;
}

export interface TemplateClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export interface TemplateScopeInput {
  project_id?: string;
  organization_id?: string;
}

const TEMPLATES = "templates";
const INSTANCES = "template-instances";

export class TemplateClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: TemplateClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listTemplates(query: ListQuery = {}): Promise<Page<CanonicalTemplate>> {
    return this.collections.list<CanonicalTemplate>(TEMPLATES, query);
  }

  getTemplate(templateId: string): Promise<CanonicalTemplate> {
    return this.collections.get<CanonicalTemplate>(TEMPLATES, requireRef(templateId, "Template"));
  }

  listInstances(query: ListQuery = {}): Promise<Page<TemplateInstantiation>> {
    return this.collections.list<TemplateInstantiation>(INSTANCES, query);
  }

  getInstance(instanceId: string): Promise<TemplateInstantiation> {
    return this.collections.get<TemplateInstantiation>(
      INSTANCES,
      requireRef(instanceId, "Template instance"),
    );
  }

  create(
    content: TemplateContent,
    scope: TemplateScopeInput = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create",
      TEMPLATES,
      { content: templateContentJson(content), ...scope },
      idempotencyKey,
    );
  }

  createFromAgent(
    agentId: string,
    options: { revision?: number; name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create-from-agent",
      TEMPLATES,
      compact({ agent_id: requireRef(agentId, "Agent"), ...options }),
      idempotencyKey,
    );
  }

  createFromAgentTeam(
    teamId: string,
    options: { revision?: number; name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create-from-agent-team",
      TEMPLATES,
      compact({ team_id: requireRef(teamId, "Agent Team"), ...options }),
      idempotencyKey,
    );
  }

  createFromWorkflow(
    workflowId: string,
    options: { revision?: number; name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create-from-workflow",
      TEMPLATES,
      compact({ workflow_id: requireRef(workflowId, "Workflow"), ...options }),
      idempotencyKey,
    );
  }

  createFromCapabilityAssignment(
    assignmentId: string,
    options: { revision?: number; name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create-from-capability-assignment",
      TEMPLATES,
      compact({
        assignment_id: requireRef(assignmentId, "Capability Assignment"),
        ...options,
      }),
      idempotencyKey,
    );
  }

  createFromModelRoutingProfile(
    profileId: string,
    options: { revision?: number; name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create-from-model-routing-profile",
      TEMPLATES,
      compact({
        profile_id: requireRef(profileId, "Model Routing Profile"),
        ...options,
      }),
      idempotencyKey,
    );
  }

  createFromAutomation(
    automationId: string,
    options: { name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create-from-automation",
      TEMPLATES,
      compact({ automation_id: requireRef(automationId, "Automation"), ...options }),
      idempotencyKey,
    );
  }

  createFromProject(
    projectId: string,
    options: { name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.create-from-project",
      TEMPLATES,
      compact({ project_id: requireRef(projectId, "Project"), ...options }),
      idempotencyKey,
    );
  }

  createFromWorkspaces(
    workspaceIds: string[],
    options: {
      name: string;
      project_template_id?: string;
      project_template_revision?: number;
    },
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    if (workspaceIds.length === 0) throw new Error("at least one Workspace is required");
    return this.command(
      "template.create-from-workspaces",
      TEMPLATES,
      compact({
        workspace_ids: workspaceIds.map((value) => requireRef(value, "Workspace")),
        name: requireRef(options.name, "Template name"),
        project_template_id: options.project_template_id,
        project_template_revision: options.project_template_revision,
      }),
      idempotencyKey,
    );
  }

  revise(
    templateId: string,
    expectedRevision: number,
    content: TemplateContent,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.revise",
      requireRef(templateId, "Template"),
      {
        expected_revision: requirePositiveRevision(expectedRevision),
        content: templateContentJson(content),
      },
      idempotencyKey,
    );
  }

  publish(
    templateId: string,
    expectedRevision: number,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.publish",
      requireRef(templateId, "Template"),
      { expected_revision: requirePositiveRevision(expectedRevision) },
      idempotencyKey,
    );
  }

  activateUntrusted(
    templateId: string,
    expectedRevision: number,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.publish",
      requireRef(templateId, "Template"),
      {
        expected_revision: requirePositiveRevision(expectedRevision),
        activate_untrusted: true,
      },
      idempotencyKey,
    );
  }

  clone(
    templateId: string,
    options: { revision?: number; name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.clone",
      requireRef(templateId, "Template"),
      compact(options),
      idempotencyKey,
    );
  }

  fork(
    templateId: string,
    options: { revision?: number; name?: string } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalTemplate> {
    return this.command(
      "template.fork",
      requireRef(templateId, "Template"),
      compact(options),
      idempotencyKey,
    );
  }

  preview(
    templateId: string,
    options: { revision?: number; allow_draft?: boolean } = {},
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<TemplatePreview> {
    return this.command(
      "template.preview",
      requireRef(templateId, "Template"),
      compact(options),
      idempotencyKey,
    );
  }

  apply(
    templateId: string,
    revision?: number,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<TemplateInstantiation> {
    return this.command(
      "template.apply",
      requireRef(templateId, "Template"),
      compact({ revision }),
      idempotencyKey,
    );
  }

  reapply(
    instanceId: string,
    revision?: number,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<TemplateInstantiation> {
    return this.command(
      "template.reapply",
      requireRef(instanceId, "Template instance"),
      compact({ revision }),
      idempotencyKey,
    );
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    if (!idempotencyKey.trim()) throw new Error("Template idempotency key is required");
    const headers = new Headers({
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
      "Idempotency-Key": idempotencyKey,
    });
    const response = await this.fetchImpl(
      `${this.baseUrl}/api/v1/commands/${encodeURIComponent(command)}`,
      {
        method: "POST",
        headers,
        credentials: "include",
        body: JSON.stringify({ resource_ref: resourceRef, ...payload }),
      },
    );
    const text = await response.text();
    const responsePayload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, responsePayload));
    }
    return responsePayload as T;
  }
}

export function emptyTemplateContent(): TemplateContent {
  return {
    name: "New Template",
    description: "Reusable canonical configuration",
    template_type: "composite",
    configuration: { payload: {}, reference: null },
    dependencies: [],
    requirements: {
      capabilities: [],
      plugin_ids: [],
      connector_ids: [],
      model_policy_refs: [],
      permission_actions: [],
      workspace_prerequisites: [],
      placeholders: [],
      secret_reference_placeholders: [],
    },
    compatibility: {
      platform_version_range: null,
      contract_versions: {},
      orchestrator_agnostic: true,
      provider_agnostic: true,
      metadata: {},
    },
    provenance: {
      author: "frontend",
      source: "frontend",
      trust: "local",
      source_template: null,
      metadata: {},
    },
    tags: [],
    categories: [],
  };
}

function templateContentJson(content: TemplateContent): JsonValue {
  return JSON.parse(JSON.stringify(content)) as JsonValue;
}

function compact(value: Record<string, JsonValue | undefined>): Record<string, JsonValue> {
  return Object.fromEntries(
    Object.entries(value).filter((entry): entry is [string, JsonValue] => entry[1] !== undefined),
  );
}

function requirePositiveRevision(value: number): number {
  if (!Number.isInteger(value) || value < 1) throw new Error("Template revision must be positive");
  return value;
}

function requireRef(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${label} reference is required`);
  return trimmed;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function normalizeError(response: Response, payload: unknown): APIErrorBody {
  if (isErrorBody(payload)) return payload;
  const requestId = response.headers.get("x-request-id") ?? "unknown";
  return {
    code: "invalid_response",
    category: "contract",
    message: `Control Plane returned HTTP ${response.status} without a canonical error envelope`,
    request_id: requestId,
    correlation_id: response.headers.get("x-correlation-id") ?? requestId,
    retryable: false,
  };
}

function isErrorBody(value: unknown): value is APIErrorBody {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<APIErrorBody>;
  return (
    typeof candidate.code === "string"
    && typeof candidate.category === "string"
    && typeof candidate.message === "string"
    && typeof candidate.request_id === "string"
    && typeof candidate.correlation_id === "string"
    && typeof candidate.retryable === "boolean"
  );
}
