from __future__ import annotations

import asyncio

from ai_multi_agent_platform.application_distribution.distributed_execution import (
    APPLICATION_BUILD_WORKER_INPUT_KEY,
    APPLICATION_BUILD_WORKER_SCHEMA,
    ApplicationBuildWorkerLifecycleBackend,
)
from ai_multi_agent_platform.application_distribution.execution import APPLICATION_BUILD_ACTION
from ai_multi_agent_platform.contracts import ExecutionRequest as KernelExecutionRequest
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.execution import (
    ExecutionRequest,
    ExecutionResult,
    Executor,
    ExecutorDescriptor,
)


class _BlockingBuildExecutor(Executor):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.calls = 0

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id="issue749-blocking-build",
            capabilities=(APPLICATION_BUILD_ACTION,),
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        self.started.set()
        assert request.cancellation is not None
        await request.cancellation.wait()
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            step_id=request.step_id,
            correlation_id=request.correlation_id,
            status=RunStatus.CANCELLED,
            result_code=130,
            stderr="cancelled",
        )


def _worker_request() -> KernelExecutionRequest:
    task_id = new_id("task")
    run_id = new_id("run")
    return KernelExecutionRequest(
        run_id=run_id,
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(correlation_id="issue-749-worker-cancel"),
        input={
            APPLICATION_BUILD_WORKER_INPUT_KEY: {
                "schema": APPLICATION_BUILD_WORKER_SCHEMA,
                "release_id": "release-test",
                "target_id": "linux-x64",
                "build_specification_id": "build-spec-test",
                "build_specification_revision": 1,
                "source_revision": "source-test",
                "workspace_id": "workspace-test",
                "workspace_snapshot_id": "snapshot-test",
                "workspace_content_checksum": "checksum-test",
                "command": ["python", "build.py"],
                "source_path": None,
                "output_path": "dist/app.bin",
                "environment": {},
            }
        },
    )


def test_remote_build_worker_can_cancel_running_execution_idempotently() -> None:
    async def scenario() -> None:
        executor = _BlockingBuildExecutor()
        backend = ApplicationBuildWorkerLifecycleBackend(
            executor,
            workspace="snapshot-test",
            workspace_id="workspace-test",
            snapshot_id="snapshot-test",
        )
        request = _worker_request()

        first = await backend.start(request)
        second = await backend.start(request)
        await executor.started.wait()

        assert first.run_id == request.run_id
        assert second.run_id == request.run_id
        assert executor.calls == 1

        running = await backend.get(request.run_id, request.context)
        assert running.status is RunStatus.RUNNING

        cancelled = await backend.cancel(request.run_id, request.context)
        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.output["result_code"] == 130

        repeated = await backend.get(request.run_id, request.context)
        assert repeated.status is RunStatus.CANCELLED
        assert executor.calls == 1

    asyncio.run(scenario())
