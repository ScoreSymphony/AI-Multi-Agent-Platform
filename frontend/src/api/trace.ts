import type { JsonValue, Page } from "./types";

export interface TraceUsageRecord {
  id: string;
  metric_type: string;
  unit: string;
  quality: "measured" | "reported" | "estimated" | "unavailable";
  source: string;
  timestamp: string;
  scope: Record<string, string>;
  quantity: number | null;
  provider: string | null;
  cost_amount: number | null;
  currency: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  precision: number | null;
  confidence: number | null;
  provenance: Record<string, JsonValue>;
}

export interface TraceResourceLink {
  type: string;
  id: string;
  href: string;
}

export interface TraceFailure {
  component: string;
  code: string;
  retryable: boolean;
}

export interface TraceNode {
  id: string;
  type: "trace-node";
  kind: "span" | "event";
  name: string;
  trace_id: string | null;
  span_id: string | null;
  parent_id: string | null;
  parent_span_id: string | null;
  timestamp: string;
  finished_at: string | null;
  duration_seconds: number | null;
  outcome: string;
  failure: TraceFailure | null;
  context: Record<string, string>;
  attributes: Record<string, JsonValue>;
  async_links: Array<Record<string, JsonValue>>;
  parallel_with: string[];
  usage: TraceUsageRecord[];
  resources: TraceResourceLink[];
}

export interface TracePage extends Page<TraceNode> {
  telemetry_state: "available" | "degraded" | "missing";
  missing_sources: string[];
  root_ids: string[];
  task_usage: TraceUsageRecord[];
  has_hierarchy: boolean;
}
