from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.execution import (
    ExecutionRequest,
    ExecutionResult,
    Executor,
    ExecutorDescriptor,
    ExecutorLifecycleBackend,
    ReferenceExecutor,
)
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository, TaskStatus
from ai_multi_agent_platform.testing import FakeOrchestrator


class RecordingExecutor(Executor):
    def __init__(self) -> None:
        self.requests: list[ExecutionRequest] = []

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id="recording",
            capabilities=("echo",),
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            step_id=request.step_id,
            correlation_id=request.correlation_id,
            status=RunStatus.SUCCEEDED,
            result_code=0,
            output={"backend": "recording"},
            adapter_metadata={
                "recording": {
                    "execution_id": f"recording-{request.run_id}",
                    "route": "generic",
                }
            },
        )


def test_kernel_executes_end_to_end_through_reference_executor(
    tmp_path: Path,
) -> None:
    task_id = new_id("task")
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / task_id
    workspace.mkdir(parents=True)
    lifecycle = ExecutorLifecycleBackend(
        ReferenceExecutor(workspace_root),
        workspace=task_id,
        action="write_artifact",
    )
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
    )

    async def scenario() -> None:
        await kernel.create_task(
            idempotency_key="cmd-create",
            task_id=task_id,
            title="Demo",
            objective="Prove platform-owned execution",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(
            idempotency_key="cmd-ready",
            task_id=task_id,
        )
        run = await kernel.start_task(
            idempotency_key="cmd-start",
            task_id=task_id,
        )
        run = await kernel.refresh_run(
            idempotency_key="cmd-refresh",
            task_id=task_id,
            run_id=run.run_id,
        )
        assert run.status.value == "succeeded"
        assert run.output["result_code"] == 0
        assert run.output["artifacts"] == ("artifact.txt",)
        assert (workspace / "artifact.txt").exists()

        artifact_id = new_id("artifact")
        await kernel.attach_artifact(
            idempotency_key="cmd-attach-artifact",
            task_id=task_id,
            run_id=run.run_id,
            artifact_id=artifact_id,
        )
        run = await kernel.get_run(task_id, run.run_id)
        task = await kernel.get_task(task_id)
        assert artifact_id in run.artifact_ids
        assert artifact_id in task.artifact_ids
        assert task.status is TaskStatus.SUCCEEDED

    asyncio.run(scenario())


def test_executor_lifecycle_keeps_task_and_step_identity_distinct(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        workspace_root = tmp_path / "workspaces"
        (workspace_root / task_id).mkdir(parents=True)
        executor = RecordingExecutor()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=ExecutorLifecycleBackend(executor, workspace=task_id),
        )
        await kernel.create_task(
            idempotency_key="step:create",
            task_id=task_id,
            title="Step task",
            objective="Keep canonical task identity",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(idempotency_key="step:ready", task_id=task_id)
        planned = await kernel.plan_task(idempotency_key="step:plan", task_id=task_id)
        step_id = planned.step_ids[0]
        run = await kernel.create_run(
            idempotency_key="step:run",
            task_id=task_id,
            subject_type="step",
            subject_id=step_id,
        )
        await kernel.start_run(
            idempotency_key="step:start",
            task_id=task_id,
            run_id=run.run_id,
        )

        assert len(executor.requests) == 1
        request = executor.requests[0]
        assert request.task_id == task_id
        assert request.run_id == run.run_id
        assert request.step_id == step_id
        assert request.correlation_id == task_id

    asyncio.run(scenario())


def test_executor_adapter_metadata_survives_canonical_sqlite_replay(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        workspace_root = tmp_path / "workspaces"
        (workspace_root / task_id).mkdir(parents=True)
        db = tmp_path / "kernel.sqlite3"
        executor = RecordingExecutor()
        lifecycle = ExecutorLifecycleBackend(executor, workspace=task_id)
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(db),
        )

        created = await kernel.create_task(
            idempotency_key="recording:create",
            task_id=task_id,
            title="Generic executor task",
            objective="Preserve adapter metadata without backend ownership",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(idempotency_key="recording:ready", task_id=task_id)
        run = await kernel.start_task(idempotency_key="recording:start", task_id=task_id)
        run = await kernel.refresh_run(
            idempotency_key="recording:refresh",
            task_id=task_id,
            run_id=run.run_id,
        )
        assert run.status is RunStatus.SUCCEEDED
        assert len(executor.requests) == 1

        history = await kernel.history(task_id)
        running = next(event for event in history if event.event_type == "run.running")
        succeeded = next(event for event in history if event.event_type == "run.succeeded")
        for event in (running, succeeded):
            metadata = event.payload["adapter_metadata"]
            assert isinstance(metadata, Mapping)
            recording = metadata["recording"]
            assert isinstance(recording, Mapping)
            assert recording["execution_id"] == f"recording-{run.run_id}"
            assert recording["route"] == "generic"
        assert running.payload["backend_ref"] == f"recording:{run.run_id}"

        restarted = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=ExecutorLifecycleBackend(RecordingExecutor(), workspace=task_id),
            repository=SqliteKernelRepository(db),
        )
        replayed_task = await restarted.get_task(task_id)
        replayed_run = await restarted.get_run(task_id, run.run_id)
        replayed_history = await restarted.history(task_id)
        duplicate_create = await restarted.create_task(
            idempotency_key="recording:create",
            task_id=new_id("task"),
            title="ignored",
            objective="ignored",
            owner_type="user",
            owner_id="tester",
        )

        assert replayed_task.status is TaskStatus.SUCCEEDED
        assert replayed_run == run
        assert replayed_history == history
        assert duplicate_create.task_id == created.task_id
        assert len(executor.requests) == 1

    asyncio.run(scenario())
