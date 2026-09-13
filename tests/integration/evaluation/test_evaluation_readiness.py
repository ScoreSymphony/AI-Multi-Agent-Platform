from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRun,
    EvaluationRunner,
    EvaluationRunStatus,
    EvaluationSuite,
    InMemoryEvalManifestRepository,
    InMemoryEvaluationRepository,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    SqliteEvaluationRepository,
)


class _StaticExecutor:
    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        return EvaluationObservation(data={"result": {"status": "ok"}})


class _FailingManifestRepository(InMemoryEvalManifestRepository):
    def save_manifest(self, manifest) -> None:
        del manifest
        raise RuntimeError("fault between manifest construction and run persistence")


class _FailingComparisonRepository(InMemoryEvaluationRepository):
    def save_comparison(
        self,
        comparison,
        *,
        candidate_reference_kinds: frozenset[str] = frozenset(),
        performance_sensitive: bool = False,
    ) -> None:
        del comparison, candidate_reference_kinds, performance_sensitive
        raise RuntimeError("fault while persisting comparison")


def test_manifest_persistence_failure_does_not_leave_run_without_manifest() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        runner = EvaluationRunner(
            repository=repository,
            manifest_repository=_FailingManifestRepository(),
            executor=_StaticExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )

        with pytest.raises(RuntimeError, match="manifest construction"):
            await runner.run_suite(suite=_suite(), snapshot=_snapshot())

        assert repository.list_runs(limit=None) == ()

    asyncio.run(scenario())


def test_comparison_persistence_fault_never_leaves_completed_learning_run() -> None:
    async def scenario() -> None:
        repository = _FailingComparisonRepository()
        manifests = InMemoryEvalManifestRepository()
        evaluator = DeterministicAssertionEvaluator()
        runner = EvaluationRunner(
            repository=repository,
            manifest_repository=manifests,
            executor=_StaticExecutor(),
            evaluators=(evaluator,),
        )
        baseline = await runner.run_suite(suite=_suite(), snapshot=_snapshot())
        policy = RegressionPolicy(
            policy_id="policy.readiness",
            version="1",
            rules=(
                RegressionRule(
                    rule_id="pass-to-fail",
                    kind=RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
                ),
            ),
        )

        with pytest.raises(RuntimeError, match="persisting comparison"):
            await runner.run_suite(
                suite=_suite(),
                snapshot=_snapshot(),
                baseline_run_id=baseline.run.run_id,
                regression_policy=policy,
            )

        current = next(
            run for run in repository.list_runs(limit=None) if run.run_id != baseline.run.run_id
        )
        assert current.status is EvaluationRunStatus.FAILED
        assert current.completed_at is not None

    asyncio.run(scenario())


def test_restart_reconciles_persisted_running_evaluation_as_failed(tmp_path) -> None:
    database_path = tmp_path / "evaluation.sqlite3"
    run = EvaluationRun(
        suite_id="suite.readiness",
        suite_version="1",
        snapshot=_snapshot(),
        status=EvaluationRunStatus.RUNNING,
    )
    SqliteEvaluationRepository(database_path).save_run(run)

    restarted = SqliteEvaluationRepository(database_path)
    reconciled = restarted.reconcile_interrupted_runs()

    assert reconciled == (run.run_id,)
    stored = restarted.get_run(run.run_id)
    assert stored is not None
    assert stored.status is EvaluationRunStatus.FAILED
    assert stored.completed_at is not None
    assert stored.completed_at >= run.started_at
    assert restarted.reconcile_interrupted_runs() == ()


def test_completed_run_is_not_reconciled_on_restart(tmp_path) -> None:
    database_path = tmp_path / "evaluation-completed.sqlite3"
    completed_at = datetime.now(UTC)
    run = EvaluationRun(
        suite_id="suite.readiness",
        suite_version="1",
        snapshot=_snapshot(),
        status=EvaluationRunStatus.COMPLETED,
        completed_at=completed_at,
    )
    repository = SqliteEvaluationRepository(database_path)
    repository.save_run(run)

    assert repository.reconcile_interrupted_runs() == ()
    assert repository.get_run(run.run_id) == run


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="suite.readiness",
        name="Evaluation readiness",
        version="1",
        cases=(
            EvaluationCase(
                case_id="case.readiness",
                name="Readiness case",
                version="1",
                input_template={},
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


def _snapshot() -> ConfigurationSnapshot:
    return ConfigurationSnapshot(platform_version="test", platform_commit="readiness")
