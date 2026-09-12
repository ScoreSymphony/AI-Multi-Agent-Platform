from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionRequest, ExecutionStatus
from ai_multi_agent_platform.evaluation import (
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    KernelEvaluationCaseExecutor,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class AutoCompletingLifecycle(FakeLifecycleBackend):
    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        handle = await super().start(request)
        self.complete(
            request.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"answer": "reference-ok"},
        )
        return handle


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

        observation = await executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )

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
