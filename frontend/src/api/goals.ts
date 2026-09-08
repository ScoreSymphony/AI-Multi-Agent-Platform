import { ControlPlaneError } from "./client";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export type GoalStatus =
  | "draft"
  | "active"
  | "waiting"
  | "paused"
  | "satisfied"
  | "failed"
  | "cancelled"
  | "superseded";

export type GoalProgress =
  | "unknown"
  | "monitoring"
  | "active_work"
  | "blocked"
  | "partial"
  | "satisfied"
  | "degraded";

export type GoalCriterionKind =
  | "human_acceptance"
  | "metric"
  | "canonical_state"
  | "verified_assertion"
  | "linked_tasks";

export type GoalCriterionOperator = "eq" | "gte" | "lte" | "gt" | "lt" | "truthy";
export type GoalTaskState = "active" | "succeeded" | "failed" | "cancelled" | "superseded";
export type GoalTaskRevisionPolicy = "retain" | "supersede";

export interface CanonicalGoalOwnerRef {
  type: string;
  id: string;
}

export interface CanonicalGoalCriterion {
  criterion_id: string;
  kind: GoalCriterionKind;
  description: string;
  operator: GoalCriterionOperator;
  target: JsonValue;
  required: boolean;
}

export interface CanonicalGoalConstraints {
  requirements: string[];
  out_of_scope: string[];
  risk_requirements: string[];
  data_requirements: string[];
  security_requirements: string[];
}

export interface CanonicalGoalObservationPolicy {
  automation_id: string | null;
  review_interval_seconds: number | null;
  event_types: string[];
}

export interface CanonicalGoalTaskGenerationPolicy {
  enabled: boolean;
  task_title: string | null;
  proposal_required: boolean;
}

export interface CanonicalGoalAutonomyPolicy {
  max_tasks_per_review: number;
  max_consecutive_failed_cycles: number;
  human_checkpoint_required: boolean;
}

export interface CanonicalGoalTaskLink {
  task_id: string;
  goal_revision: number;
  review_id: string | null;
  task_state: GoalTaskState;
  valid_for_current_revision: boolean;
  created_at: string;
}

export interface CanonicalGoalEvidence {
  evidence_id: string;
  criterion_id: string;
  kind: string;
  value: JsonValue;
  source_ref: string;
  verified: boolean;
  actor_ref: string | null;
  observed_at: string;
}

export interface CanonicalGoalCriterionEvaluation {
  criterion_id: string;
  state: "unknown" | "unsatisfied" | "satisfied";
  evidence_ids: string[];
  reason: string;
}

export interface CanonicalGoalReview {
  review_id: string;
  goal_revision: number;
  trigger_ref: string;
  criterion_evaluations: CanonicalGoalCriterionEvaluation[];
  generated_task_ids: string[];
  work_required: boolean;
  decision_reason: string;
  reviewed_at: string;
}

export interface CanonicalGoal {
  id: string;
  type: "goal";
  version: string;
  goal_id: string;
  title: string;
  objective: string;
  owner_ref: CanonicalGoalOwnerRef;
  project_id: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
  digest: string;
  status: GoalStatus;
  progress: GoalProgress;
  success_criteria: CanonicalGoalCriterion[];
  constraints: CanonicalGoalConstraints;
  observation_policy: CanonicalGoalObservationPolicy;
  task_generation_policy: CanonicalGoalTaskGenerationPolicy;
  autonomy_policy: CanonicalGoalAutonomyPolicy;
  deadline: string | null;
  linked_tasks: CanonicalGoalTaskLink[];
  active_task_ids: string[];
  evidence: CanonicalGoalEvidence[];
  reviews: CanonicalGoalReview[];
  consecutive_failed_cycles: number;
  next_review_at: string | null;
  terminal_reason: string | null;
  created_actor_ref: string | null;
  updated_actor_ref: string | null;
  stream_revision: number;
}

export interface CreateGoalInput {
  title: string;
  objective: string;
  project_id?: string;
  success_criteria: CanonicalGoalCriterion[];
  constraints?: Partial<CanonicalGoalConstraints>;
  observation_policy?: Partial<CanonicalGoalObservationPolicy>;
  task_generation_policy?: Partial<CanonicalGoalTaskGenerationPolicy>;
  autonomy_policy?: Partial<CanonicalGoalAutonomyPolicy>;
  deadline?: string;
  goal_id?: string;
}

export interface ReviseGoalInput {
  expected_revision: number;
  title?: string;
  objective?: string;
  success_criteria?: CanonicalGoalCriterion[];
  constraints?: CanonicalGoalConstraints;
  observation_policy?: CanonicalGoalObservationPolicy;
  task_generation_policy?: CanonicalGoalTaskGenerationPolicy;
  autonomy_policy?: CanonicalGoalAutonomyPolicy;
  deadline?: string | null;
  active_task_policy?: GoalTaskRevisionPolicy;
  reopen_terminal?: boolean;
}

export interface GoalEvidenceInput {
  evidence_id?: string;
  criterion_id: string;
  kind: string;
  value: JsonValue;
  source_ref: string;
  verified: boolean;
  actor_ref?: string;
  observed_at?: string;
}

export interface ReviewGoalInput {
  expected_revision: number;
  trigger_ref: string;
  evidence?: GoalEvidenceInput[];
  next_review_at?: string;
}

export interface GoalClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export class GoalClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: GoalClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  list(query: ListQuery = {}): Promise<Page<CanonicalGoal>> {
    return this.request<Page<CanonicalGoal>>(`/goals${toQuery(query)}`);
  }

  get(goalId: string): Promise<CanonicalGoal> {
    return this.request<CanonicalGoal>(`/goals/${encodeURIComponent(goalId)}`);
  }

  create(input: CreateGoalInput): Promise<CanonicalGoal> {
    return this.command("goal.create", "goals", input);
  }

  activate(goalId: string): Promise<CanonicalGoal> {
    return this.command("goal.activate", goalId);
  }

  pause(goalId: string): Promise<CanonicalGoal> {
    return this.command("goal.pause", goalId);
  }

  resume(goalId: string): Promise<CanonicalGoal> {
    return this.command("goal.resume", goalId);
  }

  cancel(goalId: string, reason: string): Promise<CanonicalGoal> {
    return this.command("goal.cancel", goalId, { reason });
  }

  revise(goalId: string, input: ReviseGoalInput): Promise<CanonicalGoal> {
    return this.command("goal.revise", goalId, input);
  }

  review(goalId: string, input: ReviewGoalInput): Promise<CanonicalGoal> {
    return this.command("goal.review", goalId, input);
  }

  attachTask(goalId: string, taskId: string, expectedRevision: number): Promise<CanonicalGoal> {
    return this.command("goal.attach-task", goalId, {
      task_id: taskId,
      expected_revision: expectedRevision,
    });
  }

  recordTaskOutcome(
    goalId: string,
    taskId: string,
    taskState: GoalTaskState,
  ): Promise<CanonicalGoal> {
    return this.command("goal.record-task-outcome", goalId, {
      task_id: taskId,
      task_state: taskState,
    });
  }

  private command(
    command: string,
    resourceRef: string,
    payload: object = {},
  ): Promise<CanonicalGoal> {
    return this.request<CanonicalGoal>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: resourceRef, ...payload },
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
  if (query.fields?.length) params.set("fields", query.fields.join(","));
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
