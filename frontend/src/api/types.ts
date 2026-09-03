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

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
  limit: number;
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

export interface CreateTaskInput {
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
