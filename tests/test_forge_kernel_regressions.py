from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.adapters.forge import (
    ForgeClientRequest,
    ForgeClientResult,
    ForgeExecutionStatus,
    ForgeExecutor,
    ForgeHealth,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend
from ai_multi_agent_platform.kernel import (
    PlatformKernel,
    RecoveryDisposition,
    SqliteKernelRepository,
)
from ai_multi_agent_platform.testing import FakeOrchestrator


class RecordingForgeClient:
    def __init__(self) -> None:
        self.requests: list[ForgeClientRequest] = []
        self.cancelled: list[str] = []

    async def execute(self, request: ForgeClientRequest) -> ForgeClientResult:
        self.requests.append(request)
        return ForgeClientResult(
            status=ForgeExecutionStatus.SUCCEEDED,
            execution_id=f"forge-job-{len(self.requests)}",
            result_code=0,
            output={"backend": "forge"},
            stdout="forge-ok",
            metadata={"route": "primary"},
        )

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> ForgeHealth:
        return ForgeHealth(healthy=True, capabilities=("echo",))


def forge_lifecycle(
    client: RecordingForgeClient,
    workspace_root: Path,
    workspace: str,
) -> ExecutorLifecycleBackend:
    return ExecutorLifecycleBackend(
        ForgeExecutor(client, workspace_root, capabilities=("echo",)),
        workspace=workspace,
        action="echo",
    )


def test_forge_external_identity_survives_canonical_sqlite_replay(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        workspace_root = tmp_path / "workspaces"
        (workspace_root / task_id).mkdir(parents=True)
        db = tmp_path / "kernel.sqlite3"
        client = RecordingForgeClient()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=forge_lifecycle(client, workspace_root, task_id),
            repository=SqliteKernelRepository(db),
        )

        created = await kernel.create_task(
            idempotency_key="forge:create",
            task_id=task_id,
            title="Forge-backed task",
            objective="Preserve canonical ownership",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(idempotency_key="forge:ready", task_id=task_id)
        run = await kernel.start_task(idempotency_key="forge:start", task_id=task_id)
        run = await kernel.refresh_run(
            idempotency_key="forge:refresh",
            task_id=task_id,
            run_id=run.run_id,
        )
        assert run.status is RunStatus.SUCCEEDED
        assert len(client.requests) == 1

        history = await kernel.history(task_id)
        running = next(event for event in history if event.event_type == "run.running")
        succeeded = next(event for event in history if event.event_type == "run.succeeded")
        for event in (running, succeeded):
            metadata = event.payload["adapter_metadata"]
            assert isinstance(metadata, dict)
            forge = metadata["forge"]
            assert isinstance(forge, dict)
            assert forge["execution_id"] == "forge-job-1"
            assert forge["route"] == "primary"
        assert running.payload["backend_ref"] == f"forge:{run.run_id}"

        restarted = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=forge_lifecycle(client, workspace_root, task_id),
            repository=SqliteKernelRepository(db),
        )
        replayed_task = await restarted.get_task(task_id)
        replayed_run = await restarted.get_run(task_id, run.run_id)
        replayed_history = await restarted.history(task_id)
        duplicate_create = await restarted.create_task(
            idempotency_key="forge:create",
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
        assert len(client.requests) == 1

    asyncio.run(scenario())


def test_step_run_keeps_task_and_step_identity_distinct_at_forge_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        workspace_root = tmp_path / "workspaces"
        (workspace_root / task_id).mkdir(parents=True)
        client = RecordingForgeClient()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=forge_lifecycle(client, workspace_root, task_id),
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

        assert len(client.requests) == 1
        request = client.requests[0]
        assert request.task_id == task_id
        assert request.run_id == run.run_id
        assert request.step_id == step_id
        assert request.correlation_id == task_id

    asyncio.run(scenario())


def test_restart_marks_missing_forge_job_for_reconciliation_without_redispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        workspace_root = tmp_path / "workspaces"
        (workspace_root / task_id).mkdir(parents=True)
        db = tmp_path / "recovery.sqlite3"
        client = RecordingForgeClient()
        first = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=forge_lifecycle(client, workspace_root, task_id),
            repository=SqliteKernelRepository(db),
        )
        await first.create_task(
            idempotency_key="recovery:create",
            task_id=task_id,
            title="Recovery task",
            objective="Do not redispatch canonical running work",
            owner_type="user",
            owner_id="tester",
        )
        await first.ready_task(idempotency_key="recovery:ready", task_id=task_id)
        run = await first.start_task(idempotency_key="recovery:start", task_id=task_id)
        assert run.status is RunStatus.RUNNING
        assert len(client.requests) == 1

        restarted = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=forge_lifecycle(client, workspace_root, task_id),
            repository=SqliteKernelRepository(db),
        )
        report = await restarted.recover_task(task_id)
        recovered = await restarted.get_run(task_id, run.run_id)
        history = await restarted.history(task_id)

        assert report.entries[0].disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
        assert recovered.status is RunStatus.RUNNING
        assert recovered.recovery_required is True
        assert len(client.requests) == 1
        assert any(event.event_type == "run.recovery_required" for event in history)

    asyncio.run(scenario())
