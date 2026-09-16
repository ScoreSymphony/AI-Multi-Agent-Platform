// GENERATED FILE - DO NOT EDIT.
// Source: ai_multi_agent_platform.control_plane.build_openapi()
// Generator: scripts/generate_frontend_contracts.py v1

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type APIError = {
  category: string;
  code: string;
  correlation_id: string;
  details?: Record<string, JsonValue>;
  diagnostics?: Record<string, JsonValue>;
  message: string;
  request_id: string;
  retryable: boolean;
};

export type Owner = {
  id: string;
  type: "user" | "organization" | "team" | "service";
};

export type APIManifest = {
  api_version: string;
  commands?: string[];
  live_updates: string;
  openapi: string;
  resources: string[];
};

export type HealthProviderStatus = {
  available: boolean;
  id: string;
  status: string;
  type: string;
};

export type HealthStatus = {
  api_version: string;
  providers: HealthProviderStatus[];
  ready: boolean;
  status: string;
};

export type Project = {
  created_at: string;
  id: string;
  name: string;
  owner: Owner;
  type: "project";
  updated_at: string;
};

export type WorkspaceSourceRef = {
  checksum: string | null;
  kind: string;
  metadata: Record<string, JsonValue>;
  ref: string;
  revision: string | null;
};

export type WorkspaceSourceRefInput = {
  checksum?: string | null;
  kind: string;
  metadata?: Record<string, JsonValue>;
  ref: string;
  revision?: string | null;
};

export type WorkspaceFileInput = {
  file_id: string;
  relative_path: string;
  sha256: string;
};

export type IdentityOnlyWorkspace = {
  created_at: string | null;
  id: string;
  lifecycle: "identity_only";
  owner: Owner;
  project_id: string;
  type: "workspace";
};

export type CanonicalWorkspace = {
  access_mode: "read_write" | "read_only";
  active_run_ids: string[];
  active_task_ids: string[];
  base_snapshot_id: string | null;
  created_at: string;
  expires_at: string | null;
  id: string;
  last_used_at: string;
  lifecycle: "canonical";
  owner: Owner;
  policy_labels: string[];
  project_id: string;
  retention: "persistent" | "ephemeral" | "until";
  revision: number;
  source_refs: WorkspaceSourceRef[];
  status: string;
  type: "workspace";
  updated_at: string;
  workspace_type: "persistent_project" | "ephemeral_task" | "isolated_run" | "read_only_source" | "cloned" | "remote";
};

export type Workspace = IdentityOnlyWorkspace | CanonicalWorkspace;

export type TaskResponsibility = {
  id: string;
  kind: "user" | "team" | "organization";
};

export type AgentAssignment = {
  id: string;
  kind: "agent" | "agent_team";
  policy_ref: string | null;
  required: boolean;
  revision: number | null;
};

export type AgentAssignmentInput = {
  id: string;
  kind: "agent" | "agent_team";
  policy_ref?: string | null;
  required?: boolean;
  revision?: number | null;
};

export type TaskDependency = {
  kind: "depends_on" | "related_to";
  task_id: string;
};

export type TaskDependencyInput = {
  kind?: "depends_on" | "related_to";
  task_id: string;
};

export type Task = {
  agent_assignment: AgentAssignment | null;
  agent_assignment_id: string | null;
  agent_assignment_type: "agent" | "agent_team" | null;
  archived: boolean;
  artifact_ids: string[];
  blocked: boolean;
  blocking_reason: string | null;
  blocking_task_ids: string[];
  causation_id: string | null;
  correlation_id: string | null;
  created_at: string;
  deadline_timezone: string | null;
  dependencies: TaskDependency[];
  due_at: string | null;
  effective_blocking_reason: string | null;
  effort_hint: number | null;
  eligible: boolean;
  failed_dependency_ids: string[];
  hidden: boolean;
  id: string;
  labels: string[];
  management_blocked: boolean;
  not_before: string | null;
  not_before_blocked: boolean;
  objective: string;
  overdue: boolean;
  owner: Owner;
  parent_task_id: string | null;
  plan_ref: string | null;
  priority: "low" | "normal" | "high" | "urgent";
  priority_rank: number;
  project_id: string | null;
  resource_hints: Record<string, JsonValue>;
  responsibility: TaskResponsibility | null;
  responsible_id: string | null;
  responsible_type: "user" | "team" | "organization" | null;
  result_ids: string[];
  revision: number;
  run_ids: string[];
  status: "draft" | "ready" | "running" | "waiting" | "succeeded" | "failed" | "cancelled";
  step_ids: string[];
  title: string;
  type: "task";
  updated_at: string;
  wait_reason: string | null;
  workspace_id: string | null;
};

export type RunError = {
  category: "execution" | "timeout";
  code: "run_failed" | "run_timed_out";
  message: string;
  retryable: boolean;
};

export type Run = {
  artifact_ids: string[];
  attempt: number;
  causation_id: string | null;
  correlation_id: string;
  created_at: string;
  error: RunError | null;
  finished_at: string | null;
  id: string;
  output: Record<string, JsonValue>;
  project_id: string | null;
  recovery_reason: string | null;
  recovery_required: boolean;
  result_ids: string[];
  started_at: string | null;
  status: "queued" | "starting" | "running" | "succeeded" | "failed" | "cancelled" | "timed_out";
  subject_id: string;
  subject_type: "task" | "step";
  task_id: string;
  trace_id: string | null;
  type: "run";
  updated_at: string;
  workspace_content_checksum?: string;
  workspace_id?: string;
  workspace_snapshot_id?: string;
};

export type ModelCapabilities = {
  context_window: number | null;
  modalities: string[];
  reasoning: string[];
  streaming: boolean;
  structured_output: boolean;
  tool_calling: boolean;
};

export type Model = {
  adapter_metadata: Record<string, JsonValue>[];
  aliases: string[];
  capabilities: ModelCapabilities;
  config_id: string;
  cost_metadata: Record<string, JsonValue>;
  display_name: string;
  effective_health: string;
  enabled: boolean;
  health: string;
  id: string;
  location: "local" | "self_hosted" | "remote";
  node_ref: string | null;
  priority: number;
  provider_id: string;
  resource_hints: Record<string, JsonValue>;
  revision: number;
  type: "model";
};

export type ModelProvider = {
  adapter_metadata: Record<string, JsonValue>[];
  available: boolean;
  capabilities: Record<string, JsonValue>[];
  contract_version: string;
  enabled: boolean;
  health: string;
  id: string;
  limits: Record<string, JsonValue>;
  provider_type: string;
  resources: Record<string, JsonValue>;
  supported_operations: string[];
  type: "model-provider";
};

export type SearchQueryParameters = {
  agent_assignment_id?: string;
  assignment_state?: "assigned" | "unassigned";
  blocked?: boolean;
  cursor?: string;
  dependency_id?: string;
  direction?: "asc" | "desc";
  due_after?: string;
  due_before?: string;
  id?: string;
  limit?: number;
  mode?: "exact" | "keyword" | "metadata" | "semantic" | "hybrid";
  overdue?: boolean;
  priority?: string;
  project_id?: string;
  provider?: string;
  q?: string;
  responsible_id?: string;
  sort?: "relevance" | "id" | "updated_at";
  source?: string;
  status?: string;
  tag?: string;
  type?: string;
  updated_after?: string;
  updated_before?: string;
  workspace_id?: string;
};

export type SearchResult = {
  access: "authorized";
  canonical_ref: string | null;
  matched_fields: string[];
  owner_id: string | null;
  owner_type: string | null;
  project_id: string | null;
  provenance: Record<string, JsonValue>;
  provider: string;
  redacted: boolean;
  relevance: number;
  resource_id: string;
  resource_type: string;
  source: string;
  status: string | null;
  summary: string;
  tags: string[];
  title: string;
  updated_at: string | null;
  version: string | null;
  workspace_id: string | null;
};

export type SearchPage = {
  items: SearchResult[];
  limit: number;
  next_cursor: string | null;
  total: number;
};

export type CanonicalEvent = {
  causation_id?: string | null;
  correlation_id: string;
  event_type: string;
  id: string;
  occurred_at: string;
  payload: Record<string, JsonValue>;
  project_id?: string | null;
  schema_version: string;
  subject_id: string;
  subject_type: string;
  trace_id?: string | null;
  type: "event";
};

export type TelemetryFailure = {
  code: string;
  component: string;
  retryable: boolean;
};

export type TelemetryTimelineEntry = {
  attributes: Record<string, JsonValue>;
  component: string;
  context: Record<string, JsonValue>;
  duration_seconds: number | null;
  event_name: string;
  failure: TelemetryFailure | null;
  id: string;
  outcome: string;
  timestamp: string;
  type: "telemetry";
};

export type TimelineItem = CanonicalEvent | TelemetryTimelineEntry;

export type UsageQualityCounts = {
  estimated: number;
  measured: number;
  reported: number;
  unavailable: number;
};

export type UsageRecord = {
  aggregation_mode: "additive" | "latest";
  causation_id: string | null;
  confidence: number | null;
  correlation_id: string | null;
  cost_amount: number | null;
  currency: string | null;
  ended_at: string | null;
  id: string;
  metric_type: string;
  precision: number | null;
  provenance: Record<string, JsonValue>;
  provider: string | null;
  quality: "measured" | "reported" | "estimated" | "unavailable";
  quantity: number | null;
  scope: Record<string, string>;
  source: string;
  started_at: string | null;
  timestamp: string;
  type: "usage-record";
  unit: string;
};

export type UsageTrendPoint = {
  end: string;
  quality_counts: UsageQualityCounts;
  record_count: number;
  start: string;
  unavailable_count: number;
  value: number | null;
};

export type UsageAggregate = {
  aggregation_mode: "additive" | "latest";
  id: string;
  metric_type: string;
  quality_counts: UsageQualityCounts;
  record_count: number;
  scope: Record<string, string>;
  total: number | null;
  trend: UsageTrendPoint[];
  trend_bucket_seconds: number | null;
  trend_window_end: string | null;
  trend_window_start: string | null;
  type: "usage-aggregate";
  unavailable_count: number;
  unit: string;
};

export type UsageBudget = {
  action: "record_only" | "warn" | "deny" | "require_approval" | "notify";
  consumed: number | null;
  fraction: number | null;
  id: string;
  include_estimated: boolean;
  kind: "soft" | "hard";
  limit: number;
  metric_type: string;
  owner_id: string | null;
  owner_type: string | null;
  remaining: number | null;
  scope_id: string;
  scope_type: string;
  threshold_level: "warning" | "exceeded" | null;
  type: "usage-budget";
  unit: string;
  version: number;
  warning_fraction: number;
  window_end: string | null;
  window_mode: "lifetime" | "rolling";
  window_seconds: number | null;
  window_start: string | null;
};

export type CreateProjectRequest = {
  name: string;
  owner_id: string;
  owner_type: "user" | "organization" | "team" | "service";
  project_id?: string;
};

export type CreateWorkspaceRequest = {
  access_mode?: "read_write" | "read_only";
  files?: WorkspaceFileInput[];
  project_id: string;
  retention?: "persistent" | "ephemeral" | "until";
  source_refs?: WorkspaceSourceRefInput[];
  workspace_id?: string;
  workspace_type?: "persistent_project" | "ephemeral_task" | "isolated_run" | "read_only_source" | "cloned" | "remote";
};

export type CreateTaskRequest = {
  agent_assignment?: AgentAssignmentInput | null;
  archived?: boolean;
  blocking_reason?: string | null;
  deadline_timezone?: string | null;
  dependencies?: TaskDependencyInput[];
  due_at?: string | null;
  effort_hint?: number | null;
  hidden?: boolean;
  labels?: string[];
  not_before?: string | null;
  objective: string;
  owner_id: string;
  owner_type: "user" | "organization" | "team" | "service";
  parent_task_id?: string | null;
  priority?: "low" | "normal" | "high" | "urgent";
  project_id?: string;
  resource_hints?: Record<string, JsonValue>;
  responsibility?: TaskResponsibility | null;
  task_id?: string;
  title: string;
  workspace_id?: string | null;
};
