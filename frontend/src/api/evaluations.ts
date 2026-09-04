import { ControlPlaneError } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import type { APIErrorBody, JsonValue, ListQuery, Page } from "./types";

export interface EvaluationCaseProjection {
  case_id: string;
  version: string;
  name: string;
  category: string | null;
  difficulty: string | null;
  tags: string[];
  fixtures: string[];
  assertion_count: number;
  metric_rule_count: number;
  rubric_criterion_count: number;
}

export interface CanonicalEvaluationSuite {
  id: string;
  type: "evaluation-suite";
  suite_id: string;
  version: string;
  name: string;
  description: string;
  tags: string[];
  cases: EvaluationCaseProjection[];
}

export interface EvaluationVersionReference {
  kind: string;
  ref_id: string;
  version: string;
  revision?: string | null;
}

export interface EvaluationSnapshotValue {
  key: string;
  value: string;
}

export interface EvaluationConfigurationSnapshot {
  snapshot_id?: string;
  schema_version?: string;
  platform_version: string;
  platform_commit?: string | null;
  references: EvaluationVersionReference[];
  environment: EvaluationSnapshotValue[];
}

export interface EvaluationEvaluatorDescriptor {
  evaluator_id: string;
  kind: string;
  version: string;
  deterministic: boolean;
  model_config_id: string | null;
  provider_id: string | null;
  configuration_ref: string | null;
}

export interface EvaluationAssertionResult {
  assertion_id: string;
  passed: boolean;
  message: string;
  expected: JsonValue;
  actual: JsonValue;
}

export interface EvaluationMetricResult {
  metric_name: string;
  value: number;
  passed: boolean | null;
  threshold: number | null;
  operator: string | null;
  unit: string | null;
}

export interface CanonicalEvaluationResult {
  id: string;
  type: "evaluation-result";
  result_id: string;
  evaluation_run_id: string;
  case_id: string;
  case_version: string;
  evaluator: EvaluationEvaluatorDescriptor;
  outcome: string;
  deterministic_pass: boolean | null;
  score: number | null;
  assertions: EvaluationAssertionResult[];
  metrics: EvaluationMetricResult[];
  case_tags: string[];
  task_id: string | null;
  run_id: string | null;
  artifact_refs: string[];
  telemetry_refs: string[];
  attempt_id: string | null;
  repetition_index: number;
  seed: number | null;
  error_category: string | null;
  error_message: string | null;
  created_at: string;
}

export interface EvaluationComparisonFinding {
  kind: string;
  rule_id: string;
  case_id: string;
  message: string;
  baseline_result_id: string;
  current_result_id: string;
}

export interface CanonicalEvaluationComparison {
  id: string;
  type: "evaluation-comparison";
  baseline_run_id: string;
  current_run_id: string;
  policy_id: string;
  policy_version: string;
  findings: EvaluationComparisonFinding[];
  regression_count: number;
  improvement_count: number;
}

export interface CanonicalEvaluationRun {
  id: string;
  type: "evaluation-run";
  run_id: string;
  suite_id: string;
  suite_version: string;
  status: string;
  baseline_run_id: string | null;
  repetitions: number;
  seed: number | null;
  started_at: string;
  completed_at: string | null;
  snapshot: EvaluationConfigurationSnapshot;
  results?: CanonicalEvaluationResult[];
  comparison?: CanonicalEvaluationComparison | null;
}

export interface RunEvaluationInput {
  snapshot: {
    platform_version: string;
    platform_commit?: string | null;
    references?: EvaluationVersionReference[];
    environment?: EvaluationSnapshotValue[];
  };
  repetitions?: number;
  seed?: number | null;
  baseline_run_id?: string | null;
  regression_policy_ref?: string | null;
}

export interface EvaluationClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const SUITES = "evaluation-suites";
const RUNS = "evaluation-runs";

export class EvaluationClient {
  readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: EvaluationClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "").replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.collections = new ControlPlaneCollectionClient(options);
  }

  listSuites(query: ListQuery = {}): Promise<Page<CanonicalEvaluationSuite>> {
    return this.collections.list<CanonicalEvaluationSuite>(SUITES, query);
  }

  getSuite(suiteRef: string): Promise<CanonicalEvaluationSuite> {
    return this.collections.get<CanonicalEvaluationSuite>(SUITES, suiteRef);
  }

  listRuns(query: ListQuery = {}): Promise<Page<CanonicalEvaluationRun>> {
    return this.collections.list<CanonicalEvaluationRun>(RUNS, query);
  }

  getRun(runId: string): Promise<CanonicalEvaluationRun> {
    return this.collections.get<CanonicalEvaluationRun>(RUNS, runId);
  }

  runSuite(
    suiteRef: string,
    input: RunEvaluationInput,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalEvaluationRun> {
    if (!input.snapshot.platform_version.trim()) {
      throw new Error("evaluation snapshot platform_version is required");
    }
    if (input.repetitions !== undefined && (!Number.isInteger(input.repetitions) || input.repetitions <= 0)) {
      throw new Error("evaluation repetitions must be a positive integer");
    }

    const baselineRunId = optionalNonBlank(input.baseline_run_id);
    const regressionPolicyRef = optionalNonBlank(input.regression_policy_ref);
    if ((baselineRunId === null) !== (regressionPolicyRef === null)) {
      throw new Error(
        "evaluation baseline_run_id and regression_policy_ref must both be set or both be omitted",
      );
    }
    const repetitions = input.repetitions ?? 1;
    if (baselineRunId !== null && repetitions !== 1) {
      throw new Error("evaluation baseline comparison requires repetitions=1");
    }

    return this.command<CanonicalEvaluationRun>(
      "evaluation.run",
      suiteRef,
      {
        snapshot: evaluationSnapshotPayload(input),
        ...(input.repetitions === undefined ? {} : { repetitions: input.repetitions }),
        ...(input.seed === undefined || input.seed === null ? {} : { seed: input.seed }),
        ...(baselineRunId === null ? {} : { baseline_run_id: baselineRunId }),
        ...(regressionPolicyRef === null ? {} : { regression_policy_ref: regressionPolicyRef }),
      },
      idempotencyKey,
    );
  }

  compareRuns(
    currentRunId: string,
    baselineRunId: string,
    regressionPolicyRef: string,
    idempotencyKey: string = crypto.randomUUID(),
  ): Promise<CanonicalEvaluationComparison> {
    if (!baselineRunId.trim()) throw new Error("baseline evaluation run id is required");
    if (!regressionPolicyRef.trim()) throw new Error("regression policy ref is required");
    return this.command<CanonicalEvaluationComparison>(
      "evaluation.compare",
      currentRunId,
      {
        baseline_run_id: baselineRunId,
        regression_policy_ref: regressionPolicyRef,
      },
      idempotencyKey,
    );
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: Record<string, JsonValue>,
    idempotencyKey: string,
  ): Promise<T> {
    if (!resourceRef.trim()) throw new Error("evaluation resource reference is required");
    if (!idempotencyKey.trim()) throw new Error("evaluation idempotency key is required");
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
    const body: unknown = text ? safeJson(text) : null;
    if (!response.ok) {
      throw new ControlPlaneError(response.status, normalizeError(response, body));
    }
    return body as T;
  }
}

function evaluationSnapshotPayload(input: RunEvaluationInput): JsonValue {
  const references: JsonValue[] = (input.snapshot.references ?? []).map((reference) => ({
    kind: reference.kind,
    ref_id: reference.ref_id,
    version: reference.version,
    revision: reference.revision ?? null,
  }));
  const environment: JsonValue[] = (input.snapshot.environment ?? []).map((item) => ({
    key: item.key,
    value: item.value,
  }));
  return {
    platform_version: input.snapshot.platform_version,
    platform_commit: input.snapshot.platform_commit ?? null,
    references,
    environment,
  };
}

function optionalNonBlank(value: string | null | undefined): string | null {
  if (value === undefined || value === null) return null;
  return value.trim() ? value : null;
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
