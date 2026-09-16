import type {
  AgentAssignment as GeneratedAgentAssignment,
  APIError as GeneratedAPIError,
  APIManifest as GeneratedAPIManifest,
  CanonicalWorkspace as GeneratedCanonicalWorkspace,
  CreateProjectRequest as GeneratedCreateProjectRequest,
  CreateTaskRequest as GeneratedCreateTaskRequest,
  CreateWorkspaceRequest as GeneratedCreateWorkspaceRequest,
  HealthStatus as GeneratedHealthStatus,
  IdentityOnlyWorkspace as GeneratedIdentityOnlyWorkspace,
  JsonPrimitive as GeneratedJsonPrimitive,
  JsonValue as GeneratedJsonValue,
  Model as GeneratedModel,
  ModelCapabilities as GeneratedModelCapabilities,
  ModelProvider as GeneratedModelProvider,
  Owner as GeneratedOwner,
  Project as GeneratedProject,
  Run as GeneratedRun,
  RunError as GeneratedRunError,
  Task as GeneratedTask,
  TaskDependency as GeneratedTaskDependency,
  TaskResponsibility as GeneratedTaskResponsibility,
  Workspace as GeneratedWorkspace,
  WorkspaceSourceRef as GeneratedWorkspaceSourceRef,
} from "./generated/control-plane-v1";

export type JsonPrimitive = GeneratedJsonPrimitive;
export type JsonValue = GeneratedJsonValue;

export type OwnerType = GeneratedOwner["type"];
export type TaskStatus = GeneratedTask["status"];
export type RunStatus = GeneratedRun["status"];
export type MeasurementQuality = "measured" | "reported" | "estimated" | "unavailable";
export type AggregationMode = "additive" | "latest";
export type TaskPriority = GeneratedTask["priority"];
export type TaskResponsibilityKind = GeneratedTaskResponsibility["kind"];
export type AgentAssignmentKind = GeneratedAgentAssignment["kind"];
export type TaskDependencyKind = GeneratedTaskDependency["kind"];
export type WorkspaceType = GeneratedCanonicalWorkspace["workspace_type"];
export type WorkspaceAccessMode = GeneratedCanonicalWorkspace["access_mode"];
export type WorkspaceRetention = GeneratedCanonicalWorkspace["retention"];
export type SearchMode = "exact" | "keyword" | "metadata" | "semantic" | "hybrid";
export type SearchSort = "relevance" | "id" | "updated_at";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
  limit: number;
}

export interface SearchRequest {
  q?: string;
  id?: string;
  types?: string[];
  project_id?: string;
  workspace_id?: string;
  statuses?: string[];
  tags?: string[];
  sources?: string[];
  providers?: string[];
  updated_after?: string;
  updated_before?: string;
  mode?: SearchMode;
  limit?: number;
  cursor?: string;
  sort?: SearchSort;
  direction?: "asc" | "desc";
}

export interface SearchResult {
  resource_type: string;
  resource_id: string;
  title: string;
  summary: string;
  project_id: string | null;
  workspace_id: string | null;
  owner_type: string | null;
  owner_id: string | null;
  status: string | null;
  tags: string[];
  relevance: number;
  matched_fields: string[];
  source: string;
  provider: string;
  version: string | null;
  updated_at: string | null;
  canonical_ref: string | null;
  provenance: Record<string, JsonValue>;
  access: "authorized";
  redacted: boolean;
}

export type SearchPage = Page<SearchResult>;

export type CanonicalProject = GeneratedProject;
export type WorkspaceSourceRef = GeneratedWorkspaceSourceRef;
export type IdentityOnlyWorkspace = GeneratedIdentityOnlyWorkspace;
export type CanonicalWorkspace = GeneratedCanonicalWorkspace;
export type CanonicalWorkspaceIdentity = GeneratedWorkspace;

export type TaskResponsibility = GeneratedTaskResponsibility;
export type AgentAssignment = GeneratedAgentAssignment;
export type TaskDependency = GeneratedTaskDependency;

type TaskManagementField =
  | "priority"
  | "due_at"
  | "deadline_timezone"
  | "not_before"
  | "responsibility"
  | "agent_assignment"
  | "labels"
  | "workspace_id"
  | "parent_task_id"
  | "dependencies"
  | "blocking_reason"
  | "effort_hint"
  | "resource_hints"
  | "archived"
  | "hidden";

export type TaskManagementChanges = Pick<GeneratedCreateTaskRequest, TaskManagementField>;
export type CanonicalTask = GeneratedTask;

export interface BulkTaskManagementResult {
  id: string;
  type: "task-management-bulk-result";
  atomic: boolean;
  authorization_preflighted: boolean;
  count: number;
  items: Array<{ task_id: string; eligible: boolean }>;
}

export type RunError = GeneratedRunError;

/**
 * `error` remains optional at this compatibility export because older frontend
 * fixtures predate the canonical Run-error projection. The generated wire DTO
 * itself requires the field and new transport code should use that shape.
 */
export type CanonicalRun = Omit<GeneratedRun, "error"> & {
  error?: GeneratedRun["error"];
};

export interface CanonicalEvent {
  id: string;
  type: "event";
  schema_version: string;
  event_type: string;
  occurred_at: string;
  subject_type: string;
  subject_id: string;
  project_id?: string | null;
  correlation_id: string;
  causation_id?: string | null;
  trace_id?: string | null;
  payload: Record<string, JsonValue>;
}

export interface TelemetryTimelineEntry {
  id: string;
  type: "telemetry";
  event_name: string;
  component: string;
  timestamp: string;
  outcome: string;
  duration_seconds: number | null;
  failure: JsonValue;
  context: Record<string, JsonValue>;
  attributes: Record<string, JsonValue>;
}

export type TimelineItem = CanonicalEvent | TelemetryTimelineEntry;

export type APIErrorBody = GeneratedAPIError;
export type APImanifest = GeneratedAPIManifest;
export type HealthStatus = GeneratedHealthStatus;
export type ModelCapabilities = GeneratedModelCapabilities;
export type CanonicalModel = GeneratedModel;
export type CanonicalModelProvider = GeneratedModelProvider;

export interface CanonicalUsageRecord {
  id: string;
  metric_type: string;
  quantity: number | null;
  unit: string;
  quality: MeasurementQuality;
  aggregation_mode: AggregationMode;
  source: string;
  provider: string | null;
  timestamp: string;
  started_at: string | null;
  ended_at: string | null;
  scope: Record<string, string>;
  correlation_id: string | null;
  causation_id: string | null;
  cost_amount: number | null;
  currency: string | null;
  precision: number | null;
  confidence: number | null;
  provenance: Record<string, JsonValue>;
}

export interface CanonicalUsageTrendPoint {
  start: string;
  end: string;
  value: number | null;
  record_count: number;
  unavailable_count: number;
  quality_counts: Record<MeasurementQuality, number>;
}

export interface CanonicalUsageAggregate {
  id: string;
  metric_type: string;
  unit: string;
  total: number | null;
  record_count: number;
  unavailable_count: number;
  quality_counts: Record<MeasurementQuality, number>;
  aggregation_mode: AggregationMode;
  scope: Record<string, string>;
  trend_window_start: string | null;
  trend_window_end: string | null;
  trend_bucket_seconds: number | null;
  trend: CanonicalUsageTrendPoint[];
}

export interface CanonicalUsageBudget {
  id: string;
  metric_type: string;
  unit: string;
  scope_type: string;
  scope_id: string;
  limit: number;
  kind: "soft" | "hard";
  action: "record_only" | "warn" | "deny" | "require_approval" | "notify";
  warning_fraction: number;
  window_seconds: number | null;
  window_mode: "lifetime" | "rolling";
  window_start: string | null;
  window_end: string | null;
  include_estimated: boolean;
  owner_type: string | null;
  owner_id: string | null;
  version: number;
  consumed: number | null;
  remaining: number | null;
  fraction: number | null;
  threshold_level: "warning" | "exceeded" | null;
}

export type CreateProjectInput = Omit<GeneratedCreateProjectRequest, "project_id">;
export type CreateWorkspaceInput = GeneratedCreateWorkspaceRequest;
export type CreateTaskInput = Omit<GeneratedCreateTaskRequest, "task_id">;

export interface ListQuery {
  limit?: number;
  cursor?: string;
  sort?: string;
  direction?: "asc" | "desc";
  q?: string;
  filters?: Record<string, string>;
  fields?: string[];
}
