from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationHistoryService,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunner,
    EvaluationRunStatus,
    EvaluationSuite,
    EvaluatorDescriptor,
    EvaluatorKind,
    MetricResult,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    SqliteEvaluationRepository,
)


class StatusExecutor:
    def __init__(self, status: str) -> None:
        self._status = status

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case
        assert execution_context.attempt_id == attempt.attempt_id
        return EvaluationObservation(data={"result": {"status": self._status}})


def _snapshot(version: str) -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        platform_version=version,
        platform_commit=f"commit-{version}",
    )


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="suite.sqlite-history",
        name="SQLite history",
        version="1",
        cases=(
            EvaluationCase(
                case_id="case.sqlite-history",
                name="SQLite history case",
                version="1",
                assertions=(
                    DeterministicAssertion(
                        assertion_id="status-ok",
                        path="result.status",
                        operator=ComparisonOperator.EQ,
                        expected="ok",
                    ),
                ),
            ),
        ),
    )


def test_sqlite_history_survives_restart_and_projects_case_trend(tmp_path: Path) -> None:
    path = tmp_path / "evaluation.sqlite3"
    repository = SqliteEvaluationRepository(path)
    base_time = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    descriptor = EvaluatorDescriptor(
        evaluator_id="test.sqlite",
        kind=EvaluatorKind.METRIC,
        version="1",
        deterministic=True,
    )
    baseline = EvaluationRun(
        suite_id="suite.history",
        suite_version="1",
        snapshot=_snapshot("0.0.1"),
        status=EvaluationRunStatus.COMPLETED,
        run_id="evaluation_run_history_baseline",
        started_at=base_time,
        completed_at=base_time + timedelta(seconds=2),
    )
    current = EvaluationRun(
        suite_id="suite.history",
        suite_version="1",
        snapshot=_snapshot("0.0.2"),
        status=EvaluationRunStatus.COMPLETED,
        baseline_run_id=baseline.run_id,
        run_id="evaluation_run_history_current",
        started_at=base_time + timedelta(minutes=1),
        completed_at=base_time + timedelta(minutes=1, seconds=2),
    )
    repository.save_run(baseline)
    repository.save_run(current)
    baseline_result = EvaluationResult(
        evaluation_run_id=baseline.run_id,
        case_id="case.history",
        case_version="1",
        evaluator=descriptor,
        outcome=EvaluationOutcome.PASSED,
        deterministic_pass=True,
        score=1.0,
        metrics=(MetricResult(metric_name="latency_ms", value=100.0, unit="ms"),),
        result_id="evaluation_result_history_baseline",
        created_at=base_time + timedelta(seconds=1),
    )
    current_result = EvaluationResult(
        evaluation_run_id=current.run_id,
        case_id="case.history",
        case_version="1",
        evaluator=descriptor,
        outcome=EvaluationOutcome.FAILED,
        deterministic_pass=False,
        score=0.0,
        metrics=(MetricResult(metric_name="latency_ms", value=180.0, unit="ms"),),
        result_id="evaluation_result_history_current",
        created_at=base_time + timedelta(minutes=1, seconds=1),
    )
    repository.save_result(baseline_result)
    repository.save_result(current_result)

    reopened = SqliteEvaluationRepository(path)
    assert reopened.get_run(baseline.run_id) == baseline
    assert reopened.get_run(current.run_id) == current
    assert reopened.list_runs(suite_id="suite.history") == (current, baseline)
    assert reopened.list_runs(suite_id="suite.history", limit=None) == (current, baseline)
    assert reopened.list_case_results(
        case_id="case.history",
        evaluator_id="test.sqlite",
    ) == (current_result, baseline_result)

    trend = EvaluationHistoryService(reopened).case_trend(
        case_id="case.history",
        evaluator_id="test.sqlite",
    )
    assert [point.platform_version for point in trend] == ["0.0.2", "0.0.1"]
    assert [point.snapshot_id for point in trend] == [
        current.snapshot.snapshot_id,
        baseline.snapshot.snapshot_id,
    ]
    assert trend[0].metrics[0].value == 180.0


def test_runner_can_compare_against_baseline_after_repository_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "runner-history.sqlite3"
        repository = SqliteEvaluationRepository(path)
        suite = _suite()
        baseline_summary = await EvaluationRunner(
            repository=repository,
            executor=StatusExecutor("ok"),
            evaluators=(DeterministicAssertionEvaluator(),),
        ).run_suite(suite=suite, snapshot=_snapshot("0.0.1"))

        reopened = SqliteEvaluationRepository(path)
        summary = await EvaluationRunner(
            repository=reopened,
            executor=StatusExecutor("regressed"),
            evaluators=(DeterministicAssertionEvaluator(),),
        ).run_suite(
            suite=suite,
            snapshot=_snapshot("0.0.2"),
            baseline_run_id=baseline_summary.run.run_id,
            regression_policy=RegressionPolicy(
                policy_id="policy.sqlite-history",
                version="1",
                rules=(
                    RegressionRule(
                        rule_id="pass-to-fail",
                        kind=RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
                    ),
                ),
            ),
        )

        assert summary.comparison is not None
        assert len(summary.comparison.regressions) == 1
        assert reopened.get_comparison(summary.run.run_id) == summary.comparison
        restarted_again = SqliteEvaluationRepository(path)
        assert restarted_again.get_comparison(summary.run.run_id) == summary.comparison

    asyncio.run(scenario())


def test_sqlite_repository_rejects_non_json_nan_and_orphan_results(tmp_path: Path) -> None:
    repository = SqliteEvaluationRepository(tmp_path / "strict.sqlite3")
    descriptor = EvaluatorDescriptor(
        evaluator_id="test.strict",
        kind=EvaluatorKind.METRIC,
        version="1",
        deterministic=True,
    )
    orphan = EvaluationResult(
        evaluation_run_id="evaluation_run_missing",
        case_id="case.strict",
        case_version="1",
        evaluator=descriptor,
        outcome=EvaluationOutcome.FAILED,
    )
    with pytest.raises(ContractError) as orphan_error:
        repository.save_result(orphan)
    assert orphan_error.value.code is ErrorCode.NOT_FOUND

    run = EvaluationRun(
        suite_id="suite.strict",
        suite_version="1",
        snapshot=_snapshot("0.0.1"),
    )
    repository.save_run(run)
    non_json = EvaluationResult(
        evaluation_run_id=run.run_id,
        case_id="case.strict",
        case_version="1",
        evaluator=descriptor,
        outcome=EvaluationOutcome.FAILED,
        metrics=(MetricResult(metric_name="invalid", value=float("nan")),),
    )
    with pytest.raises(ContractError) as nan_error:
        repository.save_result(non_json)
    assert nan_error.value.code is ErrorCode.CONTRACT_VIOLATION
