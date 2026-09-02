from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionStatus,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import (
    InMemoryKernelRepository,
    PlatformKernel,
    RecoveryDisposition,
    SqliteKernelRepository,
)
from ai_multi_agent_platform.testing import FakeEventProvider, FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend


class CrashAfterAccept(FakeLifecycleBackend):
    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        handle = await super().start(request)
        if len(self.start_calls) == 1:
            raise ContractError(ErrorCode.UNAVAILABLE, "crash after accept", retryable=True)
        return handle


class CrashBeforeAccept(FakeLifecycleBackend):
    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        if not self.crashed:
            self.crashed = True
            raise ContractError(ErrorCode.UNAVAILABLE, "crash before accept", retryable=True)
        return await super().start(request)


def kernel(
    lifecycle: FakeLifecycleBackend | None = None,
    repository: InMemoryKernelRepository | SqliteKernelRepository | None = None,
    sink: FakeEventProvider | None = None,
) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle or FakeLifecycleBackend(),
        repository=repository,
        event_sink=sink,
    )


def ready(k: PlatformKernel, key: str = "create") -> str:
    task = asyncio.run(
        k.create_task(
            idempotency_key=key,
            title="Kernel task",
            objective="Exercise canonical lifecycle",
            owner_type="user",
            owner_id="test-owner",
        )
    )
    asyncio.run(k.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id))
    return task.task_id


def started(k: PlatformKernel, lifecycle: FakeLifecycleBackend) -> tuple[str, str]:
    task_id = ready(k)
    run = asyncio.run(k.start_task(idempotency_key="start", task_id=task_id))
    assert run.status is RunStatus.RUNNING
    assert len(lifecycle.start_calls) == 1
    return task_id, run.run_id


def test_happy_path_persists_events_and_canonical_result_references() -> None:
    lifecycle, sink = FakeLifecycleBackend(), FakeEventProvider()
    k = kernel(lifecycle, sink=sink)
    task_id, run_id = started(k, lifecycle)
    lifecycle.complete(run_id, status=ExecutionStatus.SUCCEEDED, output={"answer": 42})
    run = asyncio.run(k.refresh_run(idempotency_key="refresh", task_id=task_id, run_id=run_id))
    artifact_id, result_id = new_id("artifact"), new_id("result")
    asyncio.run(
        k.attach_artifact(
            idempotency_key="artifact",
            task_id=task_id,
            run_id=run_id,
            artifact_id=artifact_id,
        )
    )
    task = asyncio.run(
        k.attach_result(
            idempotency_key="result",
            task_id=task_id,
            run_id=run_id,
            result_id=result_id,
        )
    )
    history = asyncio.run(k.history(task_id))
    assert run.status is RunStatus.SUCCEEDED and run.output == {"answer": 42}
    assert task.status is TaskStatus.SUCCEEDED
    assert artifact_id in task.artifact_ids and result_id in task.result_ids
    assert tuple(sink.publish_calls) == history


@pytest.mark.parametrize(
    ("backend_status", "run_status"),
    [
        (ExecutionStatus.FAILED, RunStatus.FAILED),
        (ExecutionStatus.TIMED_OUT, RunStatus.TIMED_OUT),
    ],
)
def test_failure_and_timeout(backend_status: ExecutionStatus, run_status: RunStatus) -> None:
    lifecycle = FakeLifecycleBackend()
    k = kernel(lifecycle)
    task_id, run_id = started(k, lifecycle)
    lifecycle.complete(run_id, status=backend_status)
    run = asyncio.run(
        k.refresh_run(
            idempotency_key=f"refresh:{backend_status.value}",
            task_id=task_id,
            run_id=run_id,
        )
    )
    assert run.status is run_status
    assert asyncio.run(k.get_task(task_id)).status is TaskStatus.FAILED


def test_cancellation_before_dispatch_and_while_running_are_idempotent() -> None:
    lifecycle = FakeLifecycleBackend()
    queued_kernel = kernel(lifecycle)
    queued_task = ready(queued_kernel)
    queued = asyncio.run(queued_kernel.create_run(idempotency_key="run", task_id=queued_task))
    cancelled = asyncio.run(
        queued_kernel.cancel_run(
            idempotency_key="cancel",
            task_id=queued_task,
            run_id=queued.run_id,
        )
    )
    assert cancelled.status is RunStatus.CANCELLED
    assert lifecycle.start_calls == [] and lifecycle.cancel_calls == []

    lifecycle2 = FakeLifecycleBackend()
    running_kernel = kernel(lifecycle2)
    task_id, run_id = started(running_kernel, lifecycle2)
    for key in ("cancel", "cancel", "cancel-again"):
        run = asyncio.run(
            running_kernel.cancel_run(
                idempotency_key=key,
                task_id=task_id,
                run_id=run_id,
            )
        )
    assert run.status is RunStatus.CANCELLED
    assert len(lifecycle2.cancel_calls) == 1


def test_duplicate_create_start_and_terminal_callback_do_not_duplicate_history() -> None:
    lifecycle = FakeLifecycleBackend()
    k = kernel(lifecycle)
    first = asyncio.run(
        k.create_task(
            idempotency_key="create",
            title="A",
            objective="B",
            owner_type="user",
            owner_id="owner",
        )
    )
    second = asyncio.run(
        k.create_task(
            idempotency_key="create",
            title="ignored",
            objective="ignored",
            owner_type="user",
            owner_id="owner",
        )
    )
    assert first.task_id == second.task_id
    asyncio.run(k.ready_task(idempotency_key="ready", task_id=first.task_id))
    run1 = asyncio.run(k.start_task(idempotency_key="start", task_id=first.task_id))
    run2 = asyncio.run(k.start_task(idempotency_key="start", task_id=first.task_id))
    assert run1.run_id == run2.run_id and len(lifecycle.start_calls) == 1
    for key in ("callback-1", "callback-1", "callback-2"):
        asyncio.run(
            k.record_run_outcome(
                idempotency_key=key,
                task_id=first.task_id,
                run_id=run1.run_id,
                status=RunStatus.SUCCEEDED,
            )
        )
    types = [event.event_type for event in asyncio.run(k.history(first.task_id))]
    assert types.count("task.created") == 1
    assert types.count("run.created") == 1
    assert types.count("run.succeeded") == 1


def test_invalid_transition_conflicting_terminal_and_key_reuse_are_rejected() -> None:
    lifecycle = FakeLifecycleBackend()
    k = kernel(lifecycle)
    task_id = ready(k)
    with pytest.raises(ContractError):
        asyncio.run(k.ready_task(idempotency_key="again", task_id=task_id))
    with pytest.raises(ContractError):
        asyncio.run(
            k.update_task(
                idempotency_key="create:ready",
                task_id=task_id,
                title="conflict",
            )
        )
    run = asyncio.run(k.start_task(idempotency_key="start", task_id=task_id))
    asyncio.run(
        k.record_run_outcome(
            idempotency_key="ok",
            task_id=task_id,
            run_id=run.run_id,
            status=RunStatus.SUCCEEDED,
        )
    )
    with pytest.raises(ContractError):
        asyncio.run(
            k.record_run_outcome(
                idempotency_key="bad",
                task_id=task_id,
                run_id=run.run_id,
                status=RunStatus.FAILED,
            )
        )


def test_retry_waiting_metadata_and_step_run_use_canonical_models() -> None:
    lifecycle = FakeLifecycleBackend()
    k = kernel(lifecycle)
    task_id, run_id = started(k, lifecycle)
    waiting = asyncio.run(
        k.wait_task(
            idempotency_key="wait",
            task_id=task_id,
            reason="approval",
            blocked=True,
        )
    )
    assert waiting.status is TaskStatus.WAITING and waiting.blocked
    asyncio.run(k.resume_task(idempotency_key="resume", task_id=task_id))
    asyncio.run(
        k.record_run_outcome(
            idempotency_key="fail",
            task_id=task_id,
            run_id=run_id,
            status=RunStatus.FAILED,
        )
    )
    retry = asyncio.run(k.retry_task(idempotency_key="retry", task_id=task_id))
    assert retry.attempt == 2 and retry.run_id != run_id
    updated = asyncio.run(
        k.update_task(
            idempotency_key="metadata",
            task_id=task_id,
            metadata={"priority": "high"},
        )
    )
    assert dict(updated.task.metadata) == {"priority": "high"}

    other = kernel()
    other_task = ready(other, "other")
    step_id = new_id("step")
    step_run = asyncio.run(
        other.create_run(
            idempotency_key="step-run",
            task_id=other_task,
            subject_type="step",
            subject_id=step_id,
        )
    )
    assert step_run.run.subject_type == "step" and step_run.run.subject_id == step_id


def test_restart_reconciles_post_accept_crash_without_duplicate_dispatch(tmp_path: Path) -> None:
    lifecycle = CrashAfterAccept()
    db = tmp_path / "kernel.sqlite3"
    first = kernel(lifecycle, SqliteKernelRepository(db))
    task_id = ready(first)
    run = asyncio.run(first.create_run(idempotency_key="run", task_id=task_id))
    with pytest.raises(ContractError):
        asyncio.run(
            first.start_run(
                idempotency_key="start",
                task_id=task_id,
                run_id=run.run_id,
            )
        )
    second = kernel(lifecycle, SqliteKernelRepository(db))
    report = asyncio.run(second.recover_task(task_id))
    assert report.entries[0].disposition is RecoveryDisposition.RECONCILED
    assert asyncio.run(second.get_run(task_id, run.run_id)).status is RunStatus.RUNNING
    assert len(lifecycle.start_calls) == 1


def test_recovery_distinguishes_queued_pre_accept_and_orphaned_running(tmp_path: Path) -> None:
    queued_lifecycle = FakeLifecycleBackend()
    db = tmp_path / "queued.sqlite3"
    first = kernel(queued_lifecycle, SqliteKernelRepository(db))
    task_id = ready(first)
    asyncio.run(first.create_run(idempotency_key="run", task_id=task_id))
    queued_report = asyncio.run(
        kernel(queued_lifecycle, SqliteKernelRepository(db)).recover_task(task_id)
    )
    assert queued_report.entries[0].disposition is RecoveryDisposition.QUEUED_PENDING

    pre = CrashBeforeAccept()
    pre_kernel = kernel(pre)
    pre_task = ready(pre_kernel)
    pre_run = asyncio.run(pre_kernel.create_run(idempotency_key="run", task_id=pre_task))
    with pytest.raises(ContractError):
        asyncio.run(
            pre_kernel.start_run(
                idempotency_key="start",
                task_id=pre_task,
                run_id=pre_run.run_id,
            )
        )
    report = asyncio.run(pre_kernel.recover_task(pre_task))
    recovered = asyncio.run(pre_kernel.get_run(pre_task, pre_run.run_id))
    assert report.entries[0].disposition is RecoveryDisposition.REDISPATCHED
    assert recovered.dispatch_attempts == 2

    orphan_lifecycle = FakeLifecycleBackend()
    orphan_kernel = kernel(orphan_lifecycle)
    orphan_task, orphan_run = started(orphan_kernel, orphan_lifecycle)
    orphan_lifecycle._runs.pop(orphan_run)
    orphan_lifecycle._handles.pop(orphan_run)
    orphan_report = asyncio.run(orphan_kernel.recover_task(orphan_task))
    orphan = asyncio.run(orphan_kernel.get_run(orphan_task, orphan_run))
    assert (
        orphan_report.entries[0].disposition is RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
    )
    assert orphan.recovery_required and len(orphan_lifecycle.start_calls) == 1


def test_event_metadata_ordering_replay_and_optimistic_revision(tmp_path: Path) -> None:
    lifecycle = FakeLifecycleBackend()
    db = tmp_path / "replay.sqlite3"
    repository = SqliteKernelRepository(db)
    k = kernel(lifecycle, repository)
    task_id, run_id = started(k, lifecycle)
    metadata = (AdapterMetadata(namespace="fake.executor", values={"delivery": "1"}),)
    asyncio.run(
        k.record_run_outcome(
            idempotency_key="done",
            task_id=task_id,
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            adapter_metadata=metadata,
        )
    )
    before_task = asyncio.run(k.get_task(task_id))
    before_run = asyncio.run(k.get_run(task_id, run_id))
    history = asyncio.run(k.history(task_id))
    for revision, event in enumerate(history, 1):
        assert event.context.correlation_id == task_id and event.context.causation_id
        assert event.payload["actor_ref"] and event.payload["source"]
        assert event.payload["canonical_payload_version"] == "1.0"
        assert event.payload["stream_revision"] == revision
    succeeded = next(event for event in history if event.event_type == "run.succeeded")
    assert succeeded.adapter_metadata == metadata
    restarted = kernel(lifecycle, SqliteKernelRepository(db))
    assert asyncio.run(restarted.get_task(task_id)) == before_task
    assert asyncio.run(restarted.get_run(task_id, run_id)) == before_run

    memory = InMemoryKernelRepository()
    small = kernel(repository=memory)
    small_task = ready(small)
    first_event = asyncio.run(small.history(small_task))[0]
    with pytest.raises(ContractError):
        asyncio.run(
            memory.commit(
                stream_id=small_task,
                expected_revision=0,
                events=(first_event,),
            )
        )
