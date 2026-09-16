import type { JsonValue, MeasurementQuality } from "./types";

export type TaskBudgetDimension =
  | "model_tokens"
  | "external_cost"
  | "model_calls"
  | "tool_calls"
  | "runtime_seconds"
  | "replans"
  | "parallel_steps"
  | "retries"
  | "repairs";

export type TaskBudgetConsumptionSource = "accounting" | "runtime_counter" | "clock";
export type TaskBudgetExhaustionAction = "block" | "require_approval";
export type TaskBudgetUnavailablePolicy = "block" | "require_approval" | "allow";

export interface TaskBudgetLimitProjection {
  dimension: TaskBudgetDimension;
  limit: number;
  source: TaskBudgetConsumptionSource;
  metric_type: string | null;
  unit: string | null;
  warning_fraction: number;
  include_estimated: boolean;
  exhaustion_action: TaskBudgetExhaustionAction;
  unavailable_policy: TaskBudgetUnavailablePolicy;
}

export interface TaskBudgetDimensionProjection {
  dimension: TaskBudgetDimension;
  limit: number;
  consumed: number;
  reserved: number;
  remaining: number;
  fraction: number;
  source: TaskBudgetConsumptionSource;
  metric_type: string | null;
  unit: string | null;
  quality_counts: Partial<Record<MeasurementQuality, number>>;
  unavailable_count: number;
  warning: boolean;
  exhausted: boolean;
  overrun: boolean;
}

export interface TaskBudgetPolicyRevisionProjection {
  version: number;
  updated_at: string;
  limits: TaskBudgetLimitProjection[];
  provenance: Record<string, JsonValue>;
}

export interface TaskExecutionBudgetProjection {
  id: string;
  task_id: string;
  policy_version: number;
  started_at: string;
  updated_at: string;
  limits: TaskBudgetLimitProjection[];
  dimensions: TaskBudgetDimensionProjection[];
  warnings: TaskBudgetDimension[];
  blocking_dimensions: TaskBudgetDimension[];
  history: TaskBudgetPolicyRevisionProjection[];
  observed_at: string;
  trace_refs: string[];
}
