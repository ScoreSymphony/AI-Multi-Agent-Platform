import type {
  AgentAssignment as GeneratedAgentAssignment,
  APIError as GeneratedAPIError,
  APIManifest as GeneratedAPIManifest,
  CanonicalEvent as GeneratedCanonicalEvent,
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
  SearchPage as GeneratedSearchPage,
  SearchQueryParameters as GeneratedSearchQueryParameters,
  SearchResult as GeneratedSearchResult,
  Task as GeneratedTask,
  TaskDependency as GeneratedTaskDependency,
  TaskResponsibility as GeneratedTaskResponsibility,
  TelemetryTimelineEntry as GeneratedTelemetryTimelineEntry,
  TimelineItem as GeneratedTimelineItem,
  UsageAggregate as GeneratedUsageAggregate,
  UsageBudget as GeneratedUsageBudget,
  UsageRecord as GeneratedUsageRecord,
  UsageTrendPoint as GeneratedUsageTrendPoint,
  Workspace as GeneratedWorkspace,
  WorkspaceSourceRef as GeneratedWorkspaceSourceRef,
} from "./generated/control-plane-v1";

export type JsonPrimitive = GeneratedJsonPrimitive;
export type JsonValue = GeneratedJsonValue;

export type OwnerType = GeneratedOwner["type"];
export type TaskStatus = GeneratedTask["status"];
export type RunStatus = GeneratedRun["status"];
export type MeasurementQuality = GeneratedUsageRecord["quality"];
export type AggregationMode = GeneratedUsageRecord["aggregation_mode"];
export type TaskPriority = GeneratedTask["priority"];
export type TaskResponsibilityKind = GeneratedTaskResponsibility["kind"];
export type AgentAssignmentKind = GeneratedAgentAssignment["kind"];
export type TaskDependencyKind = GeneratedTaskDependency["kind"];
export type WorkspaceType = GeneratedCanonicalWorkspace["workspace_type"];
export type WorkspaceAccessMode = GeneratedCanonicalWorkspace["access_mode"];
export type WorkspaceRetention = GeneratedCanonicalWorkspace["retention"];
export type SearchMode = NonNullable<GeneratedSearchQueryParameters["mode"]>;
export type SearchSort = NonNullable<GeneratedSearchQueryParameters["sort"]>;

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
  limit: number;
}

/**
 * Frontend query-builder model. Array fields are intentionally mapped to the
 * canonical comma-separated OpenAPI query parameters by `toSearchQuery()`.
 * Wire response DTOs and scalar query semantics come from generated OpenAPI types.
 */
export interface SearchRequest {
  q?: GeneratedSearchQueryParameters["q"];
  id?: GeneratedSearchQueryParameters["id"];
  types?: string[];
  project_id?: GeneratedSearchQueryParameters["project_id"];
  workspace_id?: GeneratedSearchQueryParameters["workspace_id"];
  statuses?: string[];
  tags?: string[];
  sources?: string[];
  providers?: string[];
  updated_after?: GeneratedSearchQueryParameters["updated_after"];
  updated_before?: GeneratedSearchQueryParameters["updated_before"];
  priorities?: string[];
  due_after?: GeneratedSearchQueryParameters["due_after"];
  due_before?: GeneratedSearchQueryParameters["due_before"];
  assignment_state?: GeneratedSearchQueryParameters["assignment_state"];
  responsible_id?: GeneratedSearchQueryParameters["responsible_id"];
  agent_assignment_id?: GeneratedSearchQueryParameters["agent_assignment_id"];
  blocked?: GeneratedSearchQueryParameters["blocked"];
  overdue?: GeneratedSearchQueryParameters["overdue"];
  dependency_id?: GeneratedSearchQueryParameters["dependency_id"];
  mode?: SearchMode;
  limit?: GeneratedSearchQueryParameters["limit"];
  cursor?: GeneratedSearchQueryParameters["cursor"];
  sort?: SearchSort;
  direction?: GeneratedSearchQueryParameters["direction"];
}

export type SearchResult = GeneratedSearchResult;
export type SearchPage = GeneratedSearchPage;

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

export type CanonicalEvent = GeneratedCanonicalEvent;
export type TelemetryTimelineEntry = GeneratedTelemetryTimelineEntry;
export type TimelineItem = GeneratedTimelineItem;

export type APIErrorBody = GeneratedAPIError;
export type APImanifest = GeneratedAPIManifest;
export type HealthStatus = GeneratedHealthStatus;
export type ModelCapabilities = GeneratedModelCapabilities;
export type CanonicalModel = GeneratedModel;
export type CanonicalModelProvider = GeneratedModelProvider;

export type CanonicalUsageRecord = GeneratedUsageRecord;
export type CanonicalUsageTrendPoint = GeneratedUsageTrendPoint;
export type CanonicalUsageAggregate = GeneratedUsageAggregate;
export type CanonicalUsageBudget = GeneratedUsageBudget;

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
