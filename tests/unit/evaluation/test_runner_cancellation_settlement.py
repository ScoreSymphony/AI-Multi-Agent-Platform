from __future__ import annotations

import asyncio

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
    InMemoryEvaluationRepository,
)
from ai_multi_agent_platform.evaluation.async_persistence import (
    AsyncEvaluationRepositoryAdapter,
)


class _BlockingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled execution must not resume")


class _BlockingFailureSettlementRepository(AsyncEvaluationRepositoryAdapter):
    def __init__(self, repository: InMemoryEvaluationRepository) -> None:
        super().__init__(repository)
        self.failure_save_started = asyncio.Event()
        self.release_failure_save = asyncio.Event()

    async def save_run(self, run: EvaluationRun) -> None:
        if run.status is EvaluationRunStatus.FAILED:
            self.failure_save_started.set()
            await self.release_failure_save.wait()
        await super().save_run(run)


def _suite() -> EvaluationSuite:
    case = EvaluationCase(
        case_id="case.cancellation-settlement",
        name="Cancellation settlement",
        version="1",
        input_template={"objective": "block until cancelled"},
        assertions=(
            DeterministicAssertion(
                assertion_id="status-ok",
                path="result.status",
                operator=ComparisonOperator.EQ,
                expected="ok",
            ),
        ),
    )
    return EvaluationSuite(
        suite_id="suite.cancellation-settlement",
        name="Cancellation settlement suite",
        version="1",
        cases=(case,),
    )


def test_runner_settles_persisted_run_before_repeated_cancellation_propagates() -> None:
    async def scenario() -> None:
        repository = InMemoryEvaluationRepository()
        async_repository = _BlockingFailureSettlementRepository(repository)
        executor = _BlockingExecutor()
        runner = EvaluationRunner(
            repository=repository,
            async_repository=async_repository,
            executor=executor,
            evaluators=(DeterministicAssertionEvaluator(),),
        )

        task = asyncio.create_task(
            runner.run_suite(
                suite=_suite(),
                snapshot=ConfigurationSnapshot(
                    platform_version="0.0.1",
                    platform_commit="cancellation-settlement-test",
                ),
            )
        )
        await executor.started.wait()

        task.cancel()
        await async_repository.failure_save_started.wait()
        task.cancel()
        async_repository.release_failure_save.set()

        with pytest.raises(asyncio.CancelledError):
            await task

        runs = repository.list_runs(suite_id="suite.cancellation-settlement")
        assert len(runs) == 1
        assert runs[0].status is EvaluationRunStatus.FAILED
        assert runs[0].completed_at is not None

    asyncio.run(scenario())
