# Issue #19 completion audit — evaluation and regression framework

This document records the final acceptance audit for issue #19 against merged `main` at
`40b8fd482437818bd3edf02580e6ce8e057fa48d` (PR #297 merged). It is evidence for
issue completion, not a new source of runtime authority.

The audit covers every acceptance criterion, every required test, the declared deliverables,
and the Definition of Done. The implementation remains provider-neutral, orchestrator-neutral,
executor-neutral and independent of optional distribution/import/export mechanisms.

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Canonical evaluation case/suite/run/result models exist. | PASS | `evaluation/models.py` defines `EvaluationCase`, `EvaluationSuite`, `EvaluationRun`, `EvaluationResult`, `EvaluationAttempt`, `ConfigurationSnapshot`, regression/comparison models and version references. |
| 2 | Runs record relevant platform, agent, model, tool, orchestrator and executor versions/configuration. | PASS | Every `EvaluationRun` owns an immutable `ConfigurationSnapshot`. `VersionReference` is intentionally extensible by component kind and supports agent/team, model/provider, capability/tool, orchestrator, executor, prompt/config and policy identities; platform version/commit and environment metadata are first-class snapshot fields. Reference CI tests verify persisted orchestrator/executor/evaluator/policy/suite identity, while foundation tests verify model/provider/capability/prompt version differences. |
| 3 | Deterministic evaluations work without an LLM evaluator. | PASS | `DeterministicAssertionEvaluator` and `MetricThresholdEvaluator` are local deterministic evaluators. `tests/test_evaluation_foundation.py::test_deterministic_assertions_pass_and_fail_without_model_evaluator` proves pass/fail operation without a model evaluator. |
| 4 | Reference scenarios can be repeated and compared to a baseline. | PASS | `EvaluationRunner` supports repetitions/seeds and baseline comparison. SQLite history tests prove baseline comparison after repository restart; stochastic aggregation covers repeated samples under explicit versioned aggregation policy. |
| 5 | Regression rules/thresholds are versioned. | PASS | `RegressionPolicy(policy_id, version, rules)` and versioned `MetricRule`/rubric thresholds keep policy outside evaluator code. Foundation tests verify versioned score-drop and metric threshold behavior. |
| 6 | CI has a deterministic no-paid-service evaluation subset. | PASS | Checked-in suite/policy/baseline assets plus `scripts/ci/issue19_evaluation_gate.py` run through the canonical reference Task/Run path. `.github/workflows/ci.yml` runs `Deterministic evaluation gate` after Pytest and before package build. No paid model/evaluation service is required. |
| 7 | Results link to underlying tasks/runs/artifacts/telemetry. | PASS | `EvaluationObservation` and `EvaluationResult` contain canonical Task/Run IDs plus Artifact and telemetry references. Kernel/reference execution projects Task/Run/Artifact/Result/Event/Workspace evidence. PR #297 adds source-owned Accounting usage and Observability trace/span/correlation/log references; `test_enriched_evidence_flows_into_result_and_survives_sqlite_restart` proves durable propagation. |
| 8 | Evaluators are replaceable. | PASS | `Evaluator` and `AsyncEvaluator` are Protocol boundaries combined as `EvaluatorLike`; deterministic, metric, rubric and optional model judge implementations share one canonical `EvaluationResult` contract. `evaluate_safely(...)` contains failures across sync/async evaluators. |
| 9 | Optional model judging records evaluator identity/configuration explicitly. | PASS | `EvaluatorDescriptor` requires model/provider metadata for `MODEL_JUDGE`; `ModelJudgeEvaluator` records evaluator version, model config, provider and configuration reference and marks itself non-deterministic. Local fake-provider tests exercise the real canonical `ModelRuntime` path without a paid endpoint. |
| 10 | Distribution/import/export is optional and separate from evaluation execution. | PASS | Evaluation execution, persistence, CI, API and CLI have no dependency on portability/registry distribution. Reusable templates/import-export/registry remain separate follow-up domains (#78/#79/#81); evaluation assets execute directly from checked-in/local canonical configuration. |

## Required tests

| Required test | Result | Concrete coverage |
|---|---|---|
| deterministic pass/fail | PASS | `tests/test_evaluation_foundation.py::test_deterministic_assertions_pass_and_fail_without_model_evaluator` |
| baseline comparison | PASS | `test_regression_engine_detects_pass_to_fail_and_recovery`; `tests/test_evaluation_sqlite_history.py::test_runner_can_compare_against_baseline_after_repository_restart` |
| threshold regression | PASS | `test_score_drop_threshold_is_versioned_policy_data`; `test_metric_regression_rule_detects_absolute_threshold_violation`; checked-in CI metric rule |
| critical-case regression | PASS | `test_critical_and_security_cases_can_be_policy_gated_by_tag`; `test_reference_ci_gate_reports_checked_in_baseline_regression` |
| configuration snapshot integrity | PASS | `test_configuration_snapshot_rejects_duplicate_component_identity`; durable snapshot/trend round-trip in SQLite history tests |
| isolated workspace repetition | PASS | `tests/test_evaluation_workspace_isolation.py::test_workspace_isolation_rehydrates_fixture_without_cross_attempt_contamination` |
| model/provider version difference | PASS | `tests/test_evaluation_foundation.py::test_configuration_snapshot_exposes_model_and_provider_version_difference` |
| evaluator failure handling | PASS | `test_safe_evaluator_turns_evaluator_exception_into_canonical_error_result`; rubric/model-judge error containment tests |
| optional model-evaluator metadata | PASS | `test_model_evaluator_descriptor_requires_explicit_model_and_provider_metadata`; `test_model_judge_uses_canonical_model_runtime_and_records_identity` |
| local/reference CI suite | PASS | `tests/test_evaluation_ci_gate.py::test_reference_ci_gate_runs_real_kernel_reference_path` plus deliberate-regression coverage |

## Deliverables audit

All declared deliverables are present:

- canonical EvaluationCase/Suite/Run/Result models;
- replaceable evaluator interface, including sync and async implementations;
- deterministic assertion evaluator;
- metric/threshold evaluator;
- rubric scorer and optional model-evaluator boundary;
- immutable/versioned configuration snapshot model;
- in-memory and restart-safe SQLite result/history persistence;
- baseline comparison and versioned regression engine;
- explicit stochastic aggregation for repeated samples;
- checked-in deterministic no-paid PR evaluation suite, policy and baseline;
- Control Plane API resources/commands;
- API-first `platform eval` CLI surface;
- documentation and checked-in example/reference configuration;
- Task/Run/Artifact/Workspace/telemetry/accounting evidence provenance.

## Definition of Done

PASS.

Significant platform/model/orchestration/tool/execution changes can be measured against repeatable,
versioned scenarios through `EvaluationRunner` and the Control Plane/CLI surfaces. The deterministic
PR profile runs the real canonical reference Task/Run path, compares against an accepted versioned
baseline and classifies concrete regressions before downstream acceptance checks. Repeated stochastic
samples remain individually durable and require an explicit aggregation policy before baseline
comparison. Optional model judging and optional distribution/import/export are not required for the
mandatory CI path.

## Final verification evidence

The final implementation slice before this audit was PR #297 at head
`bbf44c6316e5d8a8505a32d7a806ff9b618d9314`, synchronized to then-current
`main@fdee379d6a31a3121e707f66b0f2e6b37c54f3a6` with `behind_by=0`.
CI run #2408 / workflow run `33909310162` completed successfully with:

- Ruff format and lint;
- strict Mypy;
- full Pytest suite;
- deterministic Evaluation regression gate;
- package build;
- single-node install smoke;
- frontend typecheck/tests/build;
- pinned LiteLLM compatibility;
- real pinned Hermes compatibility;
- real Forge sidecar integration.

PR #297 then merged as `40b8fd482437818bd3edf02580e6ce8e057fa48d`, which is the `main` revision audited here.

## Documentation status note

The pre-#297 `docs/EVALUATION.md` status section still lists richer telemetry/accounting/log references
and this final audit as remaining work. PR #297 completed the first item, and this document completes
the second. That historical status block must therefore be treated as superseded by this completion
matrix until the central overview is refreshed.
