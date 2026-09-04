from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from ai_multi_agent_platform.evaluation import (
    AggregationMethod,
    AggregationPolicy,
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRunner,
    EvaluationSuite,
    EvaluatorDescriptor,
    EvaluatorKind,
    InMemoryEvaluationRepository,
    MetricResult,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    ResultAggregator,
    SqliteEvaluationRepository,
    load_aggregation_policy,
)
from ai_multi_agent_platform.evaluation.service import (
    EvaluationService,
    aggregation_policy_ref,
    evaluation_suite_ref,
    regression_policy_ref,
)


class RepetitionStatusExecutor:
    def __init__(self, statuses: tuple[str, ...]) -> None:
        self.statuses = statuses

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case
        assert execution_context.attempt_id == attempt.attempt_id
        return EvaluationObservation(
            data={"status": self.statuses[attempt.repetition_index]},
            metrics={"latency_ms": float(10 + 10 * attempt.repetition_index)},
            task_id=f"task-{attempt.repetition_index}",
            run_id=f"run-{attempt.repetition_index}",
            artifact_refs=(f"artifact-{attempt.repetition_index}",),
            telemetry_refs=(f"trace-{attempt.repetition_index}",),
        )


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="suite.stochastic",
        name="Stochastic suite",
        version="1",
        cases=(
            EvaluationCase(
                case_id="case.stochastic",
                name="Stochastic status",
                version="1",
                assertions=(
                    DeterministicAssertion(
                        assertion_id="status-ok",
                        path="status",
                        operator=ComparisonOperator.EQ,
                        expected="ok",
                    ),
                ),
                tags=("critical",),
            ),
        ),
    )


def _snapshot(commit: str) -> ConfigurationSnapshot:
    return ConfigurationSnapshot(platform_version="0.1.0", platform_commit=commit)


def _aggregation_policy(*, minimum_pass_rate: float = 1.0) -> AggregationPolicy:
    return AggregationPolicy(
        policy_id="aggregation.stochastic",
        version="1",
        score_method=AggregationMethod.MEAN,
        metric_method=AggregationMethod.MEDIAN,
        minimum_pass_rate=minimum_pass_rate,
        fail_on_error=True,
        require_equal_sample_count=True,
    )


def _regression_policy() -> RegressionPolicy:
    return RegressionPolicy(
        policy_id="policy.stochastic",
        version="1",
        rules=(
            RegressionRule(
                rule_id="pass-to-fail",
                kind=RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
            ),
            RegressionRule(
                rule_id="critical-failure",
                kind=RegressionRuleKind.TAGGED_CASE_FAILURE,
                tag="critical",
            ),
        ),
    )


def _raw_result(
    *,
    run_id: str,
    index: int,
    outcome: EvaluationOutcome,
    score: float,
    metric: float,
) -> EvaluationResult:
    descriptor = EvaluatorDescriptor(
        evaluator_id="test.stochastic",
        kind=EvaluatorKind.METRIC,
        version="1",
        deterministic=True,
    )
    return EvaluationResult(
        evaluation_run_id=run_id,
        case_id="case.aggregate",
        case_version="1",
        evaluator=descriptor,
        outcome=outcome,
        deterministic_pass=outcome is EvaluationOutcome.PASSED,
        score=score,
        metrics=(
            MetricResult(
                metric_name="latency_ms",
                value=metric,
                threshold=25.0,
                operator=ComparisonOperator.LTE,
                unit="ms",
            ),
        ),
        case_tags=("critical",),
        task_id=f"task-{index}",
        run_id=f"run-{index}",
        artifact_refs=(f"artifact-{index}",),
        telemetry_refs=(f"trace-{index}",),
        repetition_index=index,
        seed=100 + index,
        result_id=f"result-{run_id}-{index}",
    )


def test_result_aggregator_preserves_samples_and_uses_explicit_methods() -> None:
    policy = _aggregation_policy(minimum_pass_rate=0.5)
    results = (
        _raw_result(
            run_id="evaluation_run_aggregate",
            index=0,
            outcome=EvaluationOutcome.PASSED,
            score=1.0,
            metric=10.0,
        ),
        _raw_result(
            run_id="evaluation_run_aggregate",
            index=1,
            outcome=EvaluationOutcome.FAILED,
            score=0.0,
            metric=30.0,
        ),
    )

    aggregate = ResultAggregator().aggregate(
        results=results,
        policy=policy,
        expected_repetitions=2,
    )[0]
    repeated = ResultAggregator().aggregate(
        results=results,
        policy=policy,
        expected_repetitions=2,
    )[0]

    assert aggregate.result_id == repeated.result_id
    assert aggregate.outcome is EvaluationOutcome.PASSED
    assert aggregate.deterministic_pass is True
    assert aggregate.score == 0.5
    assert aggregate.pass_rate == 0.5
    assert aggregate.sample_count == 2
    assert aggregate.passed_count == 1
    assert aggregate.failed_count == 1
    assert aggregate.error_count == 0
    assert [(sample.repetition_index, sample.seed) for sample in aggregate.samples] == [
        (0, 100),
        (1, 101),
    ]
    assert aggregate.metrics[0].value == 20.0
    assert aggregate.metrics[0].passed is True
    assert aggregate.task_ids == ("task-0", "task-1")
    assert aggregate.run_ids == ("run-0", "run-1")
    assert aggregate.artifact_refs == ("artifact-0", "artifact-1")
    assert aggregate.telemetry_refs == ("trace-0", "trace-1")


def test_aggregation_policy_loader_is_strict(tmp_path: Path) -> None:
    valid = tmp_path / "aggregation.json"
    valid.write_text(
        """{
          "policy_id": "aggregation.test",
          "version": "2",
          "score_method": "mean",
          "metric_method": "median",
          "minimum_pass_rate": 0.75,
          "fail_on_error": true,
          "require_equal_sample_count": false
        }""",
        encoding="utf-8",
    )
    policy = load_aggregation_policy(valid)
    assert policy.policy_id == "aggregation.test"
    assert policy.version == "2"
    assert policy.metric_method is AggregationMethod.MEDIAN
    assert policy.minimum_pass_rate == 0.75
    assert policy.require_equal_sample_count is False

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"policy_id":"a","policy_id":"b","version":"1","score_method":"mean",'
        '"metric_method":"mean","minimum_pass_rate":1,"fail_on_error":true,'
        '"require_equal_sample_count":true}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON object key: policy_id"):
        load_aggregation_policy(duplicate)

    unknown = tmp_path / "unknown.json"
    unknown.write_text(
        valid.read_text(encoding="utf-8")[:-2] + ',"private":true}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown fields: private"):
        load_aggregation_policy(unknown)


def test_runner_compares_repeated_runs_through_versioned_aggregates() -> None:
    async def scenario() -> None:
        memory = InMemoryEvaluationRepository()
        suite = _suite()
        aggregation = _aggregation_policy()
        regression = _regression_policy()

        baseline = await EvaluationRunner(
            repository=memory,
            executor=RepetitionStatusExecutor(("ok", "ok", "ok")),
            evaluators=(DeterministicAssertionEvaluator(),),
        ).run_suite(
            suite=suite,
            snapshot=_snapshot("baseline"),
            repetitions=3,
            seed=50,
            aggregation_policy=aggregation,
        )
        assert baseline.aggregates[0].outcome is EvaluationOutcome.PASSED
        assert baseline.aggregates[0].sample_count == 3

        current = await EvaluationRunner(
            repository=memory,
            executor=RepetitionStatusExecutor(("ok", "bad", "ok")),
            evaluators=(DeterministicAssertionEvaluator(),),
        ).run_suite(
            suite=suite,
            snapshot=_snapshot("current"),
            repetitions=3,
            seed=80,
            baseline_run_id=baseline.run.run_id,
            regression_policy=regression,
            aggregation_policy=aggregation,
        )

        assert current.aggregates[0].outcome is EvaluationOutcome.FAILED
        assert current.aggregates[0].pass_rate == pytest.approx(2 / 3)
        assert current.comparison is not None
        assert {finding.rule_id for finding in current.comparison.regressions} == {
            "pass-to-fail",
            "critical-failure",
        }
        aggregation_refs = tuple(
            ref for ref in current.run.snapshot.references if ref.kind == "aggregation_policy"
        )
        assert len(aggregation_refs) == 1
        assert aggregation_refs[0].ref_id == aggregation.policy_id
        assert aggregation_refs[0].version == aggregation.version
        assert (
            memory.list_aggregates(
                current.run.run_id,
                aggregation_policy_id=aggregation.policy_id,
                aggregation_policy_version=aggregation.version,
            )
            == current.aggregates
        )

    asyncio.run(scenario())


def test_runner_rejects_mismatched_sample_counts_when_policy_requires_equality() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        suite = _suite()
        aggregation = _aggregation_policy()
        baseline = await EvaluationRunner(
            repository=repository,
            executor=RepetitionStatusExecutor(("ok", "ok")),
            evaluators=(DeterministicAssertionEvaluator(),),
        ).run_suite(
            suite=suite,
            snapshot=_snapshot("baseline"),
            repetitions=2,
            aggregation_policy=aggregation,
        )

        with pytest.raises(ValueError, match="same repetition count"):
            await EvaluationRunner(
                repository=repository,
                executor=RepetitionStatusExecutor(("ok", "ok", "ok")),
                evaluators=(DeterministicAssertionEvaluator(),),
            ).run_suite(
                suite=suite,
                snapshot=_snapshot("current"),
                repetitions=3,
                baseline_run_id=baseline.run.run_id,
                regression_policy=_regression_policy(),
                aggregation_policy=aggregation,
            )

    asyncio.run(scenario())


def test_service_can_compare_existing_repeated_runs_post_hoc() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        suite = _suite()
        aggregation = _aggregation_policy()
        regression = _regression_policy()
        baseline_runner = EvaluationRunner(
            repository=repository,
            executor=RepetitionStatusExecutor(("ok", "ok")),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        baseline = await baseline_runner.run_suite(
            suite=suite,
            snapshot=_snapshot("baseline"),
            repetitions=2,
        )
        current_runner = EvaluationRunner(
            repository=repository,
            executor=RepetitionStatusExecutor(("bad", "bad")),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        current = await current_runner.run_suite(
            suite=suite,
            snapshot=_snapshot("current"),
            repetitions=2,
        )
        service = EvaluationService(
            repository=repository,
            runner=current_runner,
            suites=(suite,),
            policies=(regression,),
            aggregation_policies=(aggregation,),
        )

        comparison = service.compare_runs(
            current_run_id=current.run.run_id,
            baseline_run_id=baseline.run.run_id,
            regression_policy_ref_value=regression_policy_ref(regression),
            aggregation_policy_ref_value=aggregation_policy_ref(aggregation),
        )

        assert len(comparison.regressions) == 2
        assert len(service.get_run_detail(baseline.run.run_id).aggregates) == 1
        assert len(service.get_run_detail(current.run.run_id).aggregates) == 1
        assert evaluation_suite_ref(suite) == "suite.stochastic@1"

    asyncio.run(scenario())


def test_sqlite_aggregate_survives_restart_and_v1_schema_migrates(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE evaluation_storage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO evaluation_storage_meta(key, value) VALUES ('schema_version', '1')"
        )

    repository = SqliteEvaluationRepository(path)
    suite = _suite()
    run = EvaluationRunner(
        repository=repository,
        executor=RepetitionStatusExecutor(("ok", "ok")),
        evaluators=(DeterministicAssertionEvaluator(),),
    )
    summary = asyncio.run(
        run.run_suite(
            suite=suite,
            snapshot=_snapshot("sqlite"),
            repetitions=2,
            seed=7,
            aggregation_policy=_aggregation_policy(),
        )
    )
    assert len(summary.aggregates) == 1

    reopened = SqliteEvaluationRepository(path)
    assert reopened.list_aggregates(summary.run.run_id) == summary.aggregates
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT value FROM evaluation_storage_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row == ("2",)
