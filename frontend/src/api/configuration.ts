import type {
  AgentProfile,
  AgentTeamProfile,
  CanonicalAgent,
  CanonicalAgentTeam,
  CanonicalOwnerRef,
} from "./agents";
import { ControlPlaneError } from "./client";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export interface ConfigurationClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export interface RoutingPolicyRequirements {
  explicit_model_id: string | null;
  min_context_window: number | null;
  tool_calling: boolean;
  structured_output: boolean;
  streaming: boolean;
  modalities: string[];
  reasoning: string[];
  local_only: boolean;
  self_hosted_only: boolean;
}

export interface ModelRoutingProfilePolicy {
  requirements: RoutingPolicyRequirements;
  preferred_model_ids: string[];
  fallback: "route" | "fail";
}

export interface CanonicalModelRoutingProfileRevision {
  revision: number;
  name: string;
  description: string;
  created_at: string;
  schema_version: string;
  provenance: JsonValue;
  policy: ModelRoutingProfilePolicy;
}

export interface CanonicalModelRoutingProfile {
  id: string;
  profile_id: string;
  exact_ref: string;
  current_revision: number;
  enabled: boolean;
  owner_ref: CanonicalOwnerRef;
  project_id: string | null;
  created_at: string;
  updated_at: string;
  schema_version: string;
  revision: CanonicalModelRoutingProfileRevision;
}

export type CapabilityAssignmentTargetType = "agent" | "agent_team" | "project";

export interface CapabilityAssignmentRule {
  capability_id: string;
  exact_version: string | null;
  compatibility: {
    minimum_version: string | null;
    maximum_version: string | null;
    include_minimum: boolean;
    include_maximum: boolean;
    required_features: string[];
  } | null;
  privileged: boolean;
  approval_required: boolean;
}

export interface CapabilityAssignmentContent {
  target: {
    subject_type: CapabilityAssignmentTargetType;
    subject_id: string;
  };
  required: CapabilityAssignmentRule[];
  allowed: CapabilityAssignmentRule[];
  denied: CapabilityAssignmentRule[];
  provenance?: {
    source: string;
    creator_ref: string;
  };
  schema_version?: string;
}

export interface CanonicalCapabilityAssignmentRevision {
  assignment_id: string;
  revision: number;
  owner_ref: CanonicalOwnerRef;
  content: CapabilityAssignmentContent;
  project_id: string | null;
  organization_id: string | null;
  created_at: string;
}

export interface CanonicalCapabilityAssignment {
  id: string;
  type: "capability_assignment";
  assignment_id: string;
  owner_ref: CanonicalOwnerRef;
  current_revision: number;
  project_id: string | null;
  organization_id: string | null;
  created_at: string;
  updated_at: string;
  revision: CanonicalCapabilityAssignmentRevision;
}

export interface AgentScopeInput {
  project_id?: string | null;
  workspace_id?: string | null;
}

export interface AgentCloneInput extends AgentScopeInput {
  revision?: number;
  name?: string;
}

export interface RoutingProfileInput {
  name: string;
  description?: string;
  policy: ModelRoutingProfilePolicy;
  project_id?: string | null;
  profile_id?: string;
}

export interface CapabilityAssignmentCreateInput {
  content: CapabilityAssignmentContent;
  project_id?: string | null;
  organization_id?: string | null;
  assignment_id?: string;
  approval_id?: string;
}

/**
 * Explicit mutation/read client for the single-node configuration product surface.
 *
 * It intentionally exposes only named canonical owner-domain operations; it is not a
 * browser-side arbitrary command executor.
 */
export class ConfigurationClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ConfigurationClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  createAgent(profile: AgentProfile, scope: AgentScopeInput = {}): Promise<CanonicalAgent> {
    return this.command<CanonicalAgent>("agent.create", "agents", {
      profile,
      ...compactScope(scope),
    });
  }

  updateAgent(
    agentId: string,
    profile: AgentProfile,
    expectedRevision: number,
    scope: AgentScopeInput = {},
  ): Promise<CanonicalAgent> {
    return this.command<CanonicalAgent>("agent.update", agentId, {
      profile,
      expected_revision: expectedRevision,
      ...compactScope(scope),
    });
  }

  cloneAgent(agentId: string, input: AgentCloneInput = {}): Promise<CanonicalAgent> {
    return this.command<CanonicalAgent>("agent.clone", agentId, {
      revision: input.revision,
      name: blankToUndefined(input.name),
      ...compactScope(input),
    });
  }

  createAgentTeam(
    profile: AgentTeamProfile,
    scope: AgentScopeInput = {},
  ): Promise<CanonicalAgentTeam> {
    return this.command<CanonicalAgentTeam>("agent-team.create", "agent-teams", {
      profile,
      ...compactScope(scope),
    });
  }

  updateAgentTeam(
    teamId: string,
    profile: AgentTeamProfile,
    expectedRevision: number,
    scope: AgentScopeInput = {},
  ): Promise<CanonicalAgentTeam> {
    return this.command<CanonicalAgentTeam>("agent-team.update", teamId, {
      profile,
      expected_revision: expectedRevision,
      ...compactScope(scope),
    });
  }

  listRoutingProfiles(query: ListQuery = {}): Promise<Page<CanonicalModelRoutingProfile>> {
    return this.request<Page<CanonicalModelRoutingProfile>>(
      `/model-routing-profiles${toQuery(query)}`,
    );
  }

  getRoutingProfile(profileId: string): Promise<CanonicalModelRoutingProfile> {
    return this.request<CanonicalModelRoutingProfile>(
      `/model-routing-profiles/${encodeURIComponent(profileId)}`,
    );
  }

  createRoutingProfile(input: RoutingProfileInput): Promise<CanonicalModelRoutingProfile> {
    return this.command<CanonicalModelRoutingProfile>(
      "model-routing-profile.create",
      "model-routing-profiles",
      {
        name: input.name,
        description: input.description ?? "",
        policy: input.policy,
        project_id: input.project_id,
        profile_id: blankToUndefined(input.profile_id),
      },
    );
  }

  versionRoutingProfile(
    profileId: string,
    expectedRevision: number,
    input: RoutingProfileInput,
  ): Promise<CanonicalModelRoutingProfile> {
    return this.command<CanonicalModelRoutingProfile>(
      "model-routing-profile.version",
      profileId,
      {
        name: input.name,
        description: input.description ?? "",
        policy: input.policy,
        expected_revision: expectedRevision,
      },
    );
  }

  setRoutingProfileEnabled(
    profileId: string,
    enabled: boolean,
  ): Promise<CanonicalModelRoutingProfile> {
    return this.command<CanonicalModelRoutingProfile>(
      enabled ? "model-routing-profile.enable" : "model-routing-profile.disable",
      profileId,
      {},
    );
  }

  listCapabilityAssignments(
    query: ListQuery = {},
  ): Promise<Page<CanonicalCapabilityAssignment>> {
    return this.request<Page<CanonicalCapabilityAssignment>>(
      `/capability-assignments${toQuery(query)}`,
    );
  }

  getCapabilityAssignment(assignmentId: string): Promise<CanonicalCapabilityAssignment> {
    return this.request<CanonicalCapabilityAssignment>(
      `/capability-assignments/${encodeURIComponent(assignmentId)}`,
    );
  }

  createCapabilityAssignment(
    input: CapabilityAssignmentCreateInput,
  ): Promise<CanonicalCapabilityAssignment> {
    return this.command<CanonicalCapabilityAssignment>(
      "capability-assignment.create",
      "capability-assignments",
      compact({
        content: stripClientProvenance(input.content),
        project_id: input.project_id,
        organization_id: input.organization_id,
        assignment_id: blankToUndefined(input.assignment_id),
        approval_id: blankToUndefined(input.approval_id),
      }),
    );
  }

  reviseCapabilityAssignment(
    assignmentId: string,
    expectedRevision: number,
    content: CapabilityAssignmentContent,
    approvalId?: string,
  ): Promise<CanonicalCapabilityAssignment> {
    return this.command<CanonicalCapabilityAssignment>(
      "capability-assignment.revise",
      assignmentId,
      compact({
        content: stripClientProvenance(content),
        expected_revision: expectedRevision,
        approval_id: blankToUndefined(approvalId),
      }),
    );
  }

  private command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, unknown>,
  ): Promise<T> {
    return this.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: compact({ resource_ref: resourceRef, ...payload }),
      idempotencyKey: crypto.randomUUID(),
    });
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown; idempotencyKey?: string } = {},
  ): Promise<T> {
    const headers = new Headers({
      Accept: "application/json",
      "X-Correlation-ID": crypto.randomUUID(),
    });
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);

    const response = await this.fetchImpl(`${this.baseUrl}/api/v1${path}`, {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const text = await response.text();
    const payload: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, payload));
    }
    return payload as T;
  }
}

function compactScope(scope: AgentScopeInput): Record<string, string | null> {
  const result: Record<string, string | null> = {};
  if (scope.project_id !== undefined) result.project_id = scope.project_id;
  if (scope.workspace_id !== undefined) result.workspace_id = scope.workspace_id;
  return result;
}

function stripClientProvenance(content: CapabilityAssignmentContent): CapabilityAssignmentContent {
  const { provenance: _ignored, ...rest } = content;
  return rest;
}

function blankToUndefined(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function compact<T extends Record<string, unknown>>(value: T): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}

function toQuery(query: ListQuery): string {
  const params = new URLSearchParams();
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.sort) params.set("sort", query.sort);
  if (query.direction) params.set("direction", query.direction);
  if (query.q) params.set("q", query.q);
  for (const [field, value] of Object.entries(query.filters ?? {})) {
    params.set(`filter[${field}]`, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
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
