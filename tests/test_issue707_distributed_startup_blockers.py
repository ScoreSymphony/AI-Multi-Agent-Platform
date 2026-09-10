from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ErrorCode, ExecutionRequest, OperationContext
from ai_multi_agent_platform.deployment.startup_recovery import reconcile_single_node_startup
from ai_multi_agent_platform.distributed import DispatchRecord, DispatchState, WorkerJobRequest
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.kernel import (
    InMemoryKernelRepository,
    PlatformKernel,
    RecoveryDisposition,
)
from ai_multi_agent_platform.testing import FakeFailure, FakeLifecycleBackend, FakeOrchestrator


class _DistributedRuntime:
    def __init__(self, record: DispatchRecord) -> None:
        self.record = record
        self.calls = 0

    async def reconcile(self) -> tuple[object, ...]:
        self.calls += 1
        return (self.record,)


class _ForbiddenCoordinator:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile_all(self) -> tuple[object, ...]:
        self.calls += 1
        raise AssertionError("coordinator must not run while distributed ownership is uncertain")


def _lost_record(task_id: str, run_id: str) -> DispatchRecord:
    request = ExecutionRequest(
        run_id=run_id,
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(correlation_id=task_id),
    )
    return DispatchRecord(
        job=WorkerJobRequest(execution=request),
        worker_id=new_id("worker"),
        reservation_id=new_id("reservation"),
        state=DispatchState.LOST,
    )


async def _ready_task(kernel: PlatformKernel, key: str) -> str:
    task = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title="Distributed startup blocker",
        objective="Preserve uncertain distributed ownership across restart",
        owner_type="service",
        owner_id="issue707-test",
    )
    await kernel.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id)
    return task.task_id


def test_lost_running_dispatch_is_written_as_resolvable_startup_blocker(tmp_path: Path) -> None:
    async def scenario() -> None:
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        task_id = await _ready_task(kernel, "running-lost")
        run = await kernel.start_task(idempotency_key="running-lost:start", task_id=task_id)
        assert run.status is RunStatus.RUNNING

        runtime = _DistributedRuntime(_lost_record(task_id, run.run_id))
        coordinator = _ForbiddenCoordinator()
        recovery = await reconcile_single_node_startup(
            data_dir=tmp_path / "running-lost",
            kernel=kernel,
            coordinator=coordinator,
            distributed_runtime=runtime,
        )

        assert runtime.calls == 1
        assert coordinator.calls == 0
        assert recovery.ready_for_service is False
        assert recovery.unresolved_run_ids == (run.run_id,)
        assert recovery.report_path.is_file()
        assert recovery.reports[0].task_id == task_id
        entry = recovery.reports[0].entries[0]
        assert entry.run_id == run.run_id
        assert entry.before is RunStatus.RUNNING
        assert entry.after is RunStatus.RUNNING
        assert entry.disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED

    asyncio.run(scenario())


def test_lost_starting_dispatch_blocks_without_redispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        lifecycle = FakeLifecycleBackend(
            start_failure=FakeFailure(
                ErrorCode.UNAVAILABLE,
                "worker accepted work but acknowledgement is uncertain",
                retryable=True,
            )
        )
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        task_id = await _ready_task(kernel, "starting-lost")
        with pytest.raises(Exception, match="acknowledgement is uncertain"):
            await kernel.start_task(idempotency_key="starting-lost:start", task_id=task_id)
        task = await kernel.get_task(task_id)
        assert len(task.run_ids) == 1
        run_id = task.run_ids[0]
        run = await kernel.get_run(task_id, run_id)
        assert run.status is RunStatus.STARTING
        assert len(lifecycle.start_calls) == 1

        runtime = _DistributedRuntime(_lost_record(task_id, run_id))
        coordinator = _ForbiddenCoordinator()
        recovery = await reconcile_single_node_startup(
            data_dir=tmp_path / "starting-lost",
            kernel=kernel,
            coordinator=coordinator,
            distributed_runtime=runtime,
        )

        assert coordinator.calls == 0
        assert len(lifecycle.start_calls) == 1
        assert recovery.ready_for_service is False
        assert recovery.unresolved_run_ids == (run_id,)
        entry = recovery.reports[0].entries[0]
        assert entry.before is RunStatus.STARTING
        assert entry.after is RunStatus.STARTING
        assert entry.disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED

    asyncio.run(scenario())
