import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

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

export interface EvaluationManifestDifferenceProjection {
  path: string;
  baseline: JsonValue;
  candidate: JsonValue;
  blocking: boolean;
  intentional_candidate_dimension: boolean;
}

export interface EvaluationManifestComparisonProjection {
  status: string;
  differences: EvaluationManifestDifferenceProjection[];
}

export interface EvaluationRepeatPolicyProjection extends Record<string, JsonValue> {
  strategy: string;
  repeat_count: number;
  min_repeats: number;
  stability_window: number | null;
  variance_threshold: number | null;
  version: string;
}

export interface EvaluationSeedPolicyProjection extends Record<string, JsonValue> {
  mode: string;
  ordered_seeds: number[];
  provider_seed_control: boolean | null;
  limitations: string[];
  version: string;
}

export interface EvaluationRepeatOutcomeProjection {
  repetition_index: number;
  seed: number | null;
  result_ids: string[];
  outcomes: string[];
  scores: Array<number | null>;
}

export interface EvaluationRepeatStatisticProjection {
  case_id: string;
  case_version: string;
  evaluator_id: string;
  sample_count: number;
  pass_rate: number;
  score_mean: number | null;
  score_variance: number | null;
}

export interface EvaluationManifestProjection {
  manifest_id: string;
  manifest_digest: string;
  evaluation_run_id: string;
  schema_version: string;
  repeat_policy: EvaluationRepeatPolicyProjection;
  actual_repeat_count: number;
  repeat_completion: string;
  repeat_statistics: EvaluationRepeatStatisticProjection[];
  seed_policy: EvaluationSeedPolicyProjection;
  environment: {
    digest: string;
    comparability: string | null;
  };
  per_repeat_outcomes: EvaluationRepeatOutcomeProjection[];
  reproducibility_limitations: string[];
  manifest_diff: EvaluationManifestDifferenceProjection[];
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
  manifest_comparison?: EvaluationManifestComparisonProjection;
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
  manifest?: EvaluationManifestProjection | null;
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
  aggregation_policy_ref?: string | null;
  repeat_policy?: EvaluationRepeatPolicyProjection;
  seed_policy?: EvaluationSeedPolicyProjection;
  candidate_reference_kinds?: string[];
  performance_sensitive?: boolean;
}

export interface CompareEvaluationOptions {
  aggregation_policy_ref?: string | null;
  candidate_reference_kinds?: string[];
  performance_sensitive?: boolean;
}

export interface EvaluationClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

const SUITES = "evaluation-suites";
const RUNS = "evaluation-runs";

export class EvaluationClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: EvaluationClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport: this.transport });
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
    if (
      input.repetitions !== undefined
      && (!Number.isInteger(input.repetitions) || input.repetitions <= 0)
    ) {
      throw new Error("evaluation repetitions must be a positive integer");
    }
    if (input.seed !== undefined && input.seed !== null && input.seed_policy !== undefined) {
      throw new Error("evaluation seed and seed_policy are alternative inputs");
    }

    const baselineRunId = optionalNonBlank(input.baseline_run_id);
    const regressionPolicyRef = optionalNonBlank(input.regression_policy_ref);
    const aggregationPolicyRef = optionalNonBlank(input.aggregation_policy_ref);
    if ((baselineRunId === null) !== (regressionPolicyRef === null)) {
      throw new Error(
        "evaluation baseline_run_id and regression_policy_ref must both be set or both be omitted",
      );
    }
    const repetitions = input.repetitions ?? 1;
    if (baselineRunId !== null && repetitions !== 1 && aggregationPolicyRef === null) {
      throw new Error("repeated baseline comparison requires an aggregation policy");
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
        ...(aggregationPolicyRef === null ? {} : { aggregation_policy_ref: aggregationPolicyRef }),
        ...(input.repeat_policy === undefined ? {} : { repeat_policy: input.repeat_policy }),
        ...(input.seed_policy === undefined ? {} : { seed_policy: input.seed_policy }),
        ...(input.candidate_reference_kinds === undefined
          ? {}
          : { candidate_reference_kinds: input.candidate_reference_kinds }),
        ...(input.performance_sensitive === undefined
          ? {}
          : { performance_sensitive: input.performance_sensitive }),
      },
      idempotencyKey,
    );
  }

  compareRuns(
    currentRunId: string,
    baselineRunId: string,
    regressionPolicyRef: string,
    idempotencyKey: string = crypto.randomUUID(),
    options: CompareEvaluationOptions = {},
  ): Promise<CanonicalEvaluationComparison> {
    if (!baselineRunId.trim()) throw new Error("baseline evaluation run id is required");
    if (!regressionPolicyRef.trim()) throw new Error("regression policy ref is required");
    const aggregationPolicyRef = optionalNonBlank(options.aggregation_policy_ref);
    return this.command<CanonicalEvaluationComparison>(
      "evaluation.compare",
      currentRunId,
      {
        baseline_run_id: baselineRunId,
        regression_policy_ref: regressionPolicyRef,
        ...(aggregationPolicyRef === null ? {} : { aggregation_policy_ref: aggregationPolicyRef }),
        ...(options.candidate_reference_kinds === undefined
          ? {}
          : { candidate_reference_kinds: options.candidate_reference_kinds }),
        ...(options.performance_sensitive === undefined
          ? {}
          : { performance_sensitive: options.performance_sensitive }),
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
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: resourceRef, ...payload },
      idempotencyKey,
    });
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
