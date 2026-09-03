export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type OwnerType = "user" | "organization" | "team" | "service";
export type TaskStatus =
  | "draft"
  | "ready"
  | "running"
  | "waiting"
  | "succeeded"
  | "failed"
  | "cancelled";
export type RunStatus =
  | "queued"
  | "starting"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timed_out";
export type MeasurementQuality = "measured" | "reported" | "estimated" | "unavailable";
export type AggregationMode = "additive" | "latest";
export type TaskPriority = "low" | "normal" | "high" | "urgent";
export type TaskResponsibilityKind = "user" | "team" | "organization";
export type AgentAssignmentKind = "agent" | "agent_team";
export type TaskDependencyKind = "depends_on" | "related_to";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
  limit: number;
}

export interface TaskResponsibility {
  kind: TaskResponsibilityKind;
  id: string;
}

export interface AgentAssignment {
  kind: AgentAssignmentKind;
  id: string;
  revision: number | null;
  required: boolean;
  policy_ref: string | null;
}

export interface TaskDependency {
  task_id: string;
  kind: TaskDependencyKind;
}

export interface TaskManagementChanges {
  priority?: TaskPriority;
  due_at?: string | null;
  deadline_timezone?: string | null;
  not_before?: string | null;
  responsibility?: TaskResponsibility | null;
  agent_assignment?: AgentAssignment | null;
  labels?: string[];
  workspace_id?: string | null;
  parent_task_id?: string | null;
  dependencies?: TaskDependency[];
  blocking_reason?: string | null;
  effort_hint?: number | null;
  resource_hints?: Record<string, JsonValue>;
  archived?: boolean;
  hidden?: boolean;
}

export interface CanonicalTask {
  id: string;
  type: "task";
  title: string;
  objective: string;
  status: TaskStatus;
  owner: { type: OwnerType; id: string };
  project_id: string | null;
  revision: number;
  plan_ref: string | null;
  step_ids: string[];
  run_ids: string[];
  artifact_ids: string[];
  result_ids: string[];
  wait_reason: string | null;
  blocked: boolean;
  correlation_id: string | null;
  causation_id: string | null;
  created_at: string;
  updated_at: string;
  priority: TaskPriority;
  priority_rank: number;
  due_at: string | null;
  deadline_timezone: string | null;
  not_before: string | null;
  responsibility: TaskResponsibility | null;
  responsible_type: TaskResponsibilityKind | null;
  responsible_id: string | null;
  agent_assignment: AgentAssignment | null;
  agent_assignment_type: AgentAssignmentKind | null;
  agent_assignment_id: string | null;
  labels: string[];
  workspace_id: string | null;
  parent_task_id: string | null;
  dependencies: TaskDependency[];
  blocking_reason: string | null;
  effort_hint: number | null;
  resource_hints: Record<string, JsonValue>;
  archived: boolean;
  hidden: boolean;
  blocking_task_ids: string[];
  failed_dependency_ids: string[];
  overdue: boolean;
  not_before_blocked: boolean;
  management_blocked: boolean;
  eligible: boolean;
  effective_blocking_reason: string | null;
}

export interface BulkTaskManagementResult {
  id: string;
  type: "task-management-bulk-result";
  atomic: boolean;
  authorization_preflighted: boolean;
  count: number;
  items: Array<{ task_id: string; eligible: boolean }>;
}

export interface RunError {
  code: string;
  category: string;
  message: string;
  retryable: boolean;
}

export interface CanonicalRun {
  id: string;
  type: "run";
  task_id: string;
  subject_type: "task" | "step";
  subject_id: string;
  attempt: number;
  status: RunStatus;
  project_id: string | null;
  correlation_id: string;
  causation_id: string | null;
  trace_id: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  output: Record<string, JsonValue>;
  artifact_ids: string[];
  result_ids: string[];
  recovery_required: boolean;
  recovery_reason: string | null;
  error?: RunError | null;
}

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

export interface APIErrorBody {
  code: string;
  category: string;
  message: string;
  request_id: string;
  correlation_id: string;
  retryable: boolean;
  details?: Record<string, JsonValue>;
  diagnostics?: Record<string, Record<string, JsonValue>>;
}

export interface APImanifest {
  api_version: string;
  resources: string[];
  commands?: string[];
  openapi: string;
  live_updates: string;
}

export interface HealthStatus {
  status: string;
  ready: boolean;
  api_version: string;
  providers: Array<{
    id: string;
    type: string;
    status: string;
    available: boolean;
  }>;
}

export interface ModelCapabilities {
  context_window: number | null;
  tool_calling: boolean;
  structured_output: boolean;
  streaming: boolean;
  modalities: string[];
  reasoning: string[];
}

export interface CanonicalModel {
  id: string;
  config_id: string;
  type: "model";
  display_name: string;
  provider_id: string;
  capabilities: ModelCapabilities;
  revision: number;
  aliases: string[];
  location: "local" | "self_hosted" | "remote";
  node_ref: string | null;
  health: string;
  enabled: boolean;
  priority: number;
  resource_hints: Record<string, JsonValue>;
  cost_metadata: Record<string, JsonValue>;
  adapter_metadata: Array<Record<string, JsonValue>>;
  effective_health: string;
}

export interface CanonicalModelProvider {
  id: string;
  type: "model-provider";
  provider_type: string;
  contract_version: string;
  supported_operations: string[];
  capabilities: Array<Record<string, JsonValue>>;
  health: string;
  enabled: boolean;
  available: boolean;
  limits: Record<string, JsonValue>;
  resources: Record<string, JsonValue>;
  adapter_metadata: Array<Record<string, JsonValue>>;
}

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
  include_estimated: boolean;
  owner_type: string | null;
  owner_id: string | null;
  version: number;
  consumed: number | null;
  remaining: number | null;
  fraction: number | null;
  threshold_level: "warning" | "exceeded" | null;
}

export interface CreateTaskInput extends TaskManagementChanges {
  title: string;
  objective: string;
  owner_type: OwnerType;
  owner_id: string;
  project_id?: string;
}

export interface ListQuery {
  limit?: number;
  cursor?: string;
  sort?: string;
  direction?: "asc" | "desc";
  q?: string;
  filters?: Record<string, string>;
  fields?: string[];
}
