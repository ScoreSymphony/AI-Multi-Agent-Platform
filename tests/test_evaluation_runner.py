from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionRequest, ExecutionStatus
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationRun,
    EvaluationRunner,
    EvaluationRunStatus,
    EvaluationSuite,
    InMemoryEvaluationRepository,
    KernelEvaluationCaseExecutor,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class RecordingExecutor:
    def __init__(self, events: list[tuple[str, str, int]]) -> None:
        self.events = events

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> EvaluationObservation:
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
    ) -> EvaluationObservation:
        del case, attempt
        raise RuntimeError("configured execution failure")


class RecordingIsolation:
    def __init__(self, events: list[tuple[str, str, int]]) -> None:
        self.events = events

    async def reset_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        self.events.append(("reset", case.case_id, attempt.repetition_index))

    async def setup_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        self.events.append(("setup", case.case_id, attempt.repetition_index))

    async def teardown_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        self.events.append(("teardown", case.case_id, attempt.repetition_index))


class AutoCompletingLifecycle(FakeLifecycleBackend):
    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        handle = await super().start(request)
        self.complete(
            request.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"answer": "reference-ok"},
        )
        return handle


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
        baseline = EvaluationRun(
            suite_id="suite.runner",
            suite_version="1",
            snapshot=_snapshot(),
            status=EvaluationRunStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )
        repository.save_run(baseline)
        baseline_result = DeterministicAssertionEvaluator().evaluate(
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
        runner = EvaluationRunner(
            repository=repository,
            executor=RecordingExecutor([]),
            evaluators=(DeterministicAssertionEvaluator(),),
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


def test_kernel_reference_executor_uses_real_task_run_and_event_path() -> None:
    async def scenario() -> None:
        event_repository = InMemoryKernelRepository()
        lifecycle = AutoCompletingLifecycle()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=event_repository,
        )
        executor = KernelEvaluationCaseExecutor(
            kernel=kernel,
            owner_type="service",
            owner_id="evaluation-tests",
            poll_interval_seconds=0.001,
        )
        case = EvaluationCase(
            case_id="case.kernel",
            name="Kernel case",
            version="1",
            input_template={"objective": "Exercise the real canonical Task/Run path"},
        )
        attempt = EvaluationAttempt(
            evaluation_run_id="evaluation_run_reference",
            case_id=case.case_id,
            case_version=case.version,
            repetition_index=0,
            seed=7,
        )

        observation = await executor.execute_case(case=case, attempt=attempt)

        assert observation.task_id is not None
        assert observation.task_id.startswith("task_")
        assert observation.run_id is not None
        assert observation.run_id.startswith("run_")
        run_data = observation.data["run"]
        assert isinstance(run_data, dict)
        assert run_data["status"] == "succeeded"
        assert run_data["output"] == {"answer": "reference-ok"}
        assert "task.created" in observation.event_types
        assert "run.created" in observation.event_types
        assert "run.succeeded" in observation.event_types
        assert observation.metrics["dispatch_attempts"] == 1.0
        assert lifecycle.start_calls[0].run_id == observation.run_id

    asyncio.run(scenario())
