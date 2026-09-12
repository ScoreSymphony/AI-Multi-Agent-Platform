from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvalManifestBuilder,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationRun,
    EvaluationRunner,
    EvaluationRunStatus,
    EvaluationSuite,
    InMemoryEvalManifestRepository,
    InMemoryEvaluationRepository,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    VersionReference,
)


class RecordingExecutor:
    def __init__(self, events: list[tuple[str, str, int]]) -> None:
        self.events = events

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        assert execution_context.attempt_id == attempt.attempt_id
        self.events.append(("execute", case.case_id, attempt.repetition_index))
        return EvaluationObservation(
            data={"result": {"status": "ok"}},
            metrics={"latency_ms": 25.0},
        )


class FailingExecutor:
    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        raise RuntimeError("configured execution failure")


class RecordingIsolation:
    def __init__(self, events: list[tuple[str, str, int]]) -> None:
        self.events = events

    async def reset_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        self.events.append(("reset", case.case_id, attempt.repetition_index))

    async def setup_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> EvaluationExecutionContext:
        self.events.append(("setup", case.case_id, attempt.repetition_index))
        return EvaluationExecutionContext(attempt_id=attempt.attempt_id)

    async def teardown_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
        succeeded: bool,
    ) -> None:
        assert execution_context.attempt_id == attempt.attempt_id
        assert succeeded is True
        self.events.append(("teardown", case.case_id, attempt.repetition_index))


def _case(case_id: str = "case.basic") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        name=f"Case {case_id}",
        version="1",
        input_template={"objective": "Run the canonical evaluation task"},
        assertions=(
            DeterministicAssertion(
                assertion_id="status-ok",
                path="result.status",
                operator=ComparisonOperator.EQ,
                expected="ok",
            ),
        ),
    )


def _suite(*cases: EvaluationCase) -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="suite.runner",
        name="Runner suite",
        version="1",
        cases=cases or (_case(),),
    )


def _snapshot() -> ConfigurationSnapshot:
    return ConfigurationSnapshot(platform_version="0.0.1", platform_commit="runner-test")


def test_runner_repeats_cases_with_seed_and_isolation_lifecycle() -> None:
    async def scenario() -> None:
        events: list[tuple[str, str, int]] = []
        repository = InMemoryEvaluationRepository()
        runner = EvaluationRunner(
            repository=repository,
            executor=RecordingExecutor(events),
            evaluators=(DeterministicAssertionEvaluator(),),
            isolation=RecordingIsolation(events),
        )

        summary = await runner.run_suite(
            suite=_suite(_case("case.a"), _case("case.b")),
            snapshot=_snapshot(),
            repetitions=2,
            seed=41,
        )

        assert summary.run.status is EvaluationRunStatus.COMPLETED
        assert summary.run.repetitions == 2
        assert summary.run.seed == 41
        assert len(summary.results) == 4
        assert {result.repetition_index for result in summary.results} == {0, 1}
        assert {result.seed for result in summary.results} == {41, 42}
        assert all(result.attempt_id is not None for result in summary.results)
        assert all(result.outcome is EvaluationOutcome.PASSED for result in summary.results)

        assert events == [
            ("reset", "case.a", 0),
            ("setup", "case.a", 0),
            ("execute", "case.a", 0),
            ("teardown", "case.a", 0),
            ("reset", "case.b", 0),
            ("setup", "case.b", 0),
            ("execute", "case.b", 0),
            ("teardown", "case.b", 0),
            ("reset", "case.a", 1),
            ("setup", "case.a", 1),
            ("execute", "case.a", 1),
            ("teardown", "case.a", 1),
            ("reset", "case.b", 1),
            ("setup", "case.b", 1),
            ("execute", "case.b", 1),
            ("teardown", "case.b", 1),
        ]

    asyncio.run(scenario())


def test_runner_contains_case_execution_failure_and_still_completes_suite() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        runner = EvaluationRunner(
            repository=repository,
            executor=FailingExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        )

        summary = await runner.run_suite(suite=_suite(), snapshot=_snapshot())

        assert summary.run.status is EvaluationRunStatus.COMPLETED
        assert len(summary.results) == 1
        result = summary.results[0]
        assert result.outcome is EvaluationOutcome.ERROR
        assert result.error_category == "case_execution_failure"
        assert result.error_message == "configured execution failure"
        assert result.attempt_id is not None

    asyncio.run(scenario())


def test_runner_persists_single_repetition_baseline_comparison() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        evaluator = DeterministicAssertionEvaluator()
        baseline_snapshot = ConfigurationSnapshot(
            platform_version=_snapshot().platform_version,
            platform_commit=_snapshot().platform_commit,
            references=(
                VersionReference("evaluation_suite", "suite.runner", "1"),
                VersionReference(
                    "evaluator",
                    evaluator.descriptor.evaluator_id,
                    evaluator.descriptor.version,
                ),
            ),
        )
        baseline = EvaluationRun(
            suite_id="suite.runner",
            suite_version="1",
            snapshot=baseline_snapshot,
            status=EvaluationRunStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )
        repository.save_run(baseline)
        baseline_result = evaluator.evaluate(
            evaluation_run_id=baseline.run_id,
            case=_case(),
            observation=EvaluationObservation(data={"result": {"status": "ok"}}),
        )
        repository.save_result(baseline_result)

        policy = RegressionPolicy(
            policy_id="policy.runner",
            version="1",
            rules=(
                RegressionRule(
                    rule_id="pass-to-fail",
                    kind=RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
                ),
            ),
        )
        manifests = InMemoryEvalManifestRepository()
        manifests.save_manifest(EvalManifestBuilder().build(run=baseline, suite=_suite()))
        runner = EvaluationRunner(
            repository=repository,
            manifest_repository=manifests,
            executor=RecordingExecutor([]),
            evaluators=(evaluator,),
        )
        summary = await runner.run_suite(
            suite=_suite(),
            snapshot=_snapshot(),
            baseline_run_id=baseline.run_id,
            regression_policy=policy,
        )

        assert summary.comparison is not None
        assert summary.comparison.regressions == ()
        assert repository.get_comparison(summary.run.run_id) == summary.comparison

    asyncio.run(scenario())


def test_runner_rejects_automatic_baseline_comparison_for_unaggregated_repetitions() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        baseline = EvaluationRun(
            suite_id="suite.runner",
            suite_version="1",
            snapshot=_snapshot(),
        )
        repository.save_run(baseline)
        runner = EvaluationRunner(
            repository=repository,
            executor=RecordingExecutor([]),
            evaluators=(DeterministicAssertionEvaluator(),),
        )
        with pytest.raises(ValueError, match="repetitions=1"):
            await runner.run_suite(
                suite=_suite(),
                snapshot=_snapshot(),
                repetitions=2,
                baseline_run_id=baseline.run_id,
                regression_policy=RegressionPolicy(
                    policy_id="policy.runner",
                    version="1",
                    rules=(),
                ),
            )

    asyncio.run(scenario())
