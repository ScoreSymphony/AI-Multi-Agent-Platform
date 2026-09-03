from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.evaluation import (
    ComparisonKind,
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluatorDescriptor,
    EvaluatorKind,
    InMemoryEvaluationRepository,
    MetricResult,
    MetricRule,
    MetricThresholdEvaluator,
    RegressionEngine,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    SafeEvaluator,
    SnapshotValue,
    VersionReference,
)


def _snapshot(
    *,
    model_version: str = "qwen-v1",
    provider_version: str = "1.0",
) -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        platform_version="0.0.1",
        platform_commit="abc123",
        references=(
            VersionReference("model", "model.local.qwen", model_version, revision="7"),
            VersionReference("model-provider", "provider.local", provider_version),
            VersionReference("orchestrator", "reference", "1.0"),
            VersionReference("executor", "reference", "1.0"),
            VersionReference("capability", "capability.echo", "1.0"),
            VersionReference("prompt", "prompt.coding", "3"),
        ),
        environment=(
            SnapshotValue("node_id", "node_local"),
            SnapshotValue("python", "3.12"),
        ),
    )


def _result(
    *,
    evaluation_run_id: str,
    case_id: str = "case.lifecycle",
    passed: bool = True,
    score: float | None = None,
    tags: tuple[str, ...] = (),
    metrics: tuple[MetricResult, ...] = (),
) -> EvaluationResult:
    descriptor = EvaluatorDescriptor(
        evaluator_id="test.evaluator",
        kind=EvaluatorKind.DETERMINISTIC,
        version="1",
        deterministic=True,
    )
    return EvaluationResult(
        evaluation_run_id=evaluation_run_id,
        case_id=case_id,
        case_version="1",
        evaluator=descriptor,
        outcome=EvaluationOutcome.PASSED if passed else EvaluationOutcome.FAILED,
        deterministic_pass=passed,
        score=score,
        case_tags=tags,
        metrics=metrics,
    )


def test_deterministic_assertions_pass_and_fail_without_model_evaluator() -> None:
    case = EvaluationCase(
        case_id="case.lifecycle",
        name="canonical lifecycle",
        version="1",
        assertions=(
            DeterministicAssertion(
                "status",
                "result.status",
                ComparisonOperator.EQ,
                expected="succeeded",
            ),
            DeterministicAssertion(
                "artifact",
                "result.artifacts.0",
                ComparisonOperator.EXISTS,
            ),
        ),
    )
    evaluator = DeterministicAssertionEvaluator()

    passing = evaluator.evaluate(
        evaluation_run_id="evaluation_run_pass",
        case=case,
        observation=EvaluationObservation(
            data={"result": {"status": "succeeded", "artifacts": ["artifact_1"]}}
        ),
    )
    failing = evaluator.evaluate(
        evaluation_run_id="evaluation_run_fail",
        case=case,
        observation=EvaluationObservation(
            data={"result": {"status": "failed", "artifacts": []}}
        ),
    )

    assert passing.outcome is EvaluationOutcome.PASSED
    assert passing.deterministic_pass is True
    assert all(item.passed for item in passing.assertions)
    assert failing.outcome is EvaluationOutcome.FAILED
    assert failing.deterministic_pass is False
    assert {item.assertion_id for item in failing.assertions if not item.passed} == {
        "status",
        "artifact",
    }


def test_metric_threshold_evaluator_is_deterministic() -> None:
    case = EvaluationCase(
        case_id="case.latency",
        name="latency threshold",
        version="2",
        metric_rules=(
            MetricRule(
                "latency",
                "latency_ms",
                ComparisonOperator.LTE,
                500.0,
                unit="ms",
            ),
            MetricRule(
                "retry-count",
                "retry_count",
                ComparisonOperator.LTE,
                1.0,
            ),
        ),
    )
    evaluator = MetricThresholdEvaluator()
    result = evaluator.evaluate(
        evaluation_run_id="evaluation_run_metrics",
        case=case,
        observation=EvaluationObservation(
            metrics={"latency_ms": 450.0, "retry_count": 0.0}
        ),
    )

    assert result.outcome is EvaluationOutcome.PASSED
    assert result.deterministic_pass is True
    assert all(metric.passed is True for metric in result.metrics)


def test_regression_engine_detects_pass_to_fail_and_recovery() -> None:
    policy = RegressionPolicy(
        policy_id="policy.pr",
        version="1",
        rules=(
            RegressionRule(
                "deterministic-regression",
                RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
            ),
        ),
    )
    engine = RegressionEngine()

    regression = engine.compare(
        baseline_run_id="evaluation_run_baseline",
        current_run_id="evaluation_run_current",
        baseline_results=(_result(evaluation_run_id="evaluation_run_baseline", passed=True),),
        current_results=(_result(evaluation_run_id="evaluation_run_current", passed=False),),
        policy=policy,
    )
    improvement = engine.compare(
        baseline_run_id="evaluation_run_baseline",
        current_run_id="evaluation_run_current",
        baseline_results=(_result(evaluation_run_id="evaluation_run_baseline", passed=False),),
        current_results=(_result(evaluation_run_id="evaluation_run_current", passed=True),),
        policy=policy,
    )

    assert len(regression.regressions) == 1
    assert regression.regressions[0].kind is ComparisonKind.REGRESSION
    assert len(improvement.improvements) == 1
    assert improvement.improvements[0].kind is ComparisonKind.IMPROVEMENT


def test_score_drop_threshold_is_versioned_policy_data() -> None:
    policy = RegressionPolicy(
        policy_id="policy.release",
        version="2026-09-03",
        rules=(
            RegressionRule(
                "score-drop",
                RegressionRuleKind.SCORE_DROP,
                threshold=0.05,
            ),
        ),
    )
    report = RegressionEngine().compare(
        baseline_run_id="evaluation_run_baseline",
        current_run_id="evaluation_run_current",
        baseline_results=(
            _result(evaluation_run_id="evaluation_run_baseline", score=0.90),
        ),
        current_results=(
            _result(evaluation_run_id="evaluation_run_current", score=0.80),
        ),
        policy=policy,
    )

    assert report.policy_version == "2026-09-03"
    assert len(report.regressions) == 1
    assert report.regressions[0].rule_id == "score-drop"


def test_critical_and_security_cases_can_be_policy_gated_by_tag() -> None:
    policy = RegressionPolicy(
        policy_id="policy.critical",
        version="1",
        rules=(
            RegressionRule(
                "critical-failure",
                RegressionRuleKind.TAGGED_CASE_FAILURE,
                tag="critical",
            ),
            RegressionRule(
                "security-failure",
                RegressionRuleKind.TAGGED_CASE_FAILURE,
                tag="security",
            ),
        ),
    )
    report = RegressionEngine().compare(
        baseline_run_id="evaluation_run_baseline",
        current_run_id="evaluation_run_current",
        baseline_results=(
            _result(
                evaluation_run_id="evaluation_run_baseline",
                passed=True,
                tags=("critical", "security"),
            ),
        ),
        current_results=(
            _result(
                evaluation_run_id="evaluation_run_current",
                passed=False,
                tags=("critical", "security"),
            ),
        ),
        policy=policy,
    )

    assert {item.rule_id for item in report.regressions} == {
        "critical-failure",
        "security-failure",
    }


def test_metric_regression_rule_detects_absolute_threshold_violation() -> None:
    policy = RegressionPolicy(
        policy_id="policy.resources",
        version="1",
        rules=(
            RegressionRule(
                "latency-ceiling",
                RegressionRuleKind.METRIC_THRESHOLD,
                metric_name="latency_ms",
                metric_operator=ComparisonOperator.LTE,
                threshold=500.0,
            ),
        ),
    )
    baseline_metric = MetricResult("latency_ms", 400.0)
    current_metric = MetricResult("latency_ms", 700.0)
    report = RegressionEngine().compare(
        baseline_run_id="evaluation_run_baseline",
        current_run_id="evaluation_run_current",
        baseline_results=(
            _result(
                evaluation_run_id="evaluation_run_baseline",
                metrics=(baseline_metric,),
            ),
        ),
        current_results=(
            _result(
                evaluation_run_id="evaluation_run_current",
                metrics=(current_metric,),
            ),
        ),
        policy=policy,
    )

    assert len(report.regressions) == 1
    assert "latency_ms" in report.regressions[0].message


def test_configuration_snapshot_rejects_duplicate_component_identity() -> None:
    with pytest.raises(ValueError, match="unique by kind/ref_id"):
        ConfigurationSnapshot(
            platform_version="0.0.1",
            references=(
                VersionReference("model", "model.local", "1"),
                VersionReference("model", "model.local", "2"),
            ),
        )


def test_configuration_snapshot_exposes_model_and_provider_version_difference() -> None:
    baseline = _snapshot()
    model_changed = _snapshot(model_version="qwen-v2")
    provider_changed = _snapshot(provider_version="2.0")

    assert baseline.references != model_changed.references
    assert baseline.references != provider_changed.references
    assert baseline.platform_commit == model_changed.platform_commit


def test_model_evaluator_descriptor_requires_explicit_model_and_provider_metadata() -> None:
    with pytest.raises(ValueError, match="model_config_id"):
        EvaluatorDescriptor(
            evaluator_id="judge",
            kind=EvaluatorKind.MODEL_JUDGE,
            version="1",
            deterministic=False,
        )

    descriptor = EvaluatorDescriptor(
        evaluator_id="judge",
        kind=EvaluatorKind.MODEL_JUDGE,
        version="1",
        deterministic=False,
        model_config_id="model.judge",
        provider_id="provider.local",
        configuration_ref="config/evaluators/judge-v1",
    )
    assert descriptor.model_config_id == "model.judge"
    assert descriptor.provider_id == "provider.local"


def test_safe_evaluator_turns_evaluator_exception_into_canonical_error_result() -> None:
    class BrokenEvaluator:
        descriptor = EvaluatorDescriptor(
            evaluator_id="broken",
            kind=EvaluatorKind.RUBRIC,
            version="1",
            deterministic=False,
        )

        def evaluate(
            self,
            *,
            evaluation_run_id: str,
            case: EvaluationCase,
            observation: EvaluationObservation,
        ) -> EvaluationResult:
            del evaluation_run_id, case, observation
            raise RuntimeError("judge unavailable")

    result = SafeEvaluator(BrokenEvaluator()).evaluate(
        evaluation_run_id="evaluation_run_error",
        case=EvaluationCase("case.rubric", "rubric", "1"),
        observation=EvaluationObservation(),
    )

    assert result.outcome is EvaluationOutcome.ERROR
    assert result.error_category == "evaluator_failure"
    assert result.error_message == "judge unavailable"


def test_reference_repository_persists_runs_and_results() -> None:
    from ai_multi_agent_platform.evaluation import EvaluationRun

    repository = InMemoryEvaluationRepository()
    run = EvaluationRun(
        suite_id="suite.ci",
        suite_version="1",
        snapshot=_snapshot(),
    )
    repository.save_run(run)
    result = _result(evaluation_run_id=run.run_id)
    repository.save_result(result)

    assert repository.get_run(run.run_id) == run
    assert repository.list_results(run.run_id) == (result,)

    updated = replace(result, score=1.0)
    repository.save_result(updated)
    assert repository.list_results(run.run_id) == (updated,)
