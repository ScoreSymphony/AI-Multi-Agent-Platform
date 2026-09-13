from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ExecutionRequest,
    OperationContext,
    OperationControl,
    RetryMode,
)
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    FailoverError,
    FailoverFenceReceipt,
    FailoverRejectionCode,
    JobRequirements,
    JsonDistributedStateStore,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.testing import FakeLifecycleBackend

BASE_TIME = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)


class _RecordingFencer:
    def __init__(
        self,
        *,
        receipt_worker_id: str | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.receipt_worker_id = receipt_worker_id
        self.failure = failure
        self.calls: list[tuple[str, WorkerJobRequest]] = []

    async def fence(
        self,
        *,
        worker_id: str,
        job: WorkerJobRequest,
    ) -> FailoverFenceReceipt:
        self.calls.append((worker_id, job))
        if self.failure is not None:
            raise self.failure
        return FailoverFenceReceipt(
            worker_job_id=job.worker_job_id,
            worker_id=self.receipt_worker_id or worker_id,
            fence_ref=f"supervisor:{worker_id}:{job.dispatch_attempt}",
            fenced_at=BASE_TIME + timedelta(seconds=2),
        )


def _node(name: str) -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name=name,
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        supported_runtimes=("python",),
    )


def _worker(node: NodeRecord) -> WorkerRecord:
    return WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
        concurrency_limit=2,
    )


def _job(
    preferred_worker_id: str,
    *,
    retry_mode: RetryMode = RetryMode.IDEMPOTENT,
) -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id="corr:issue-14-failover",
                causation_id="cause:issue-14-failover",
                owner_type="service",
                owner_id="service:distributed-runtime",
                control=OperationControl(
                    idempotency_key="run:issue-14-failover",
                    retry_mode=retry_mode,
                ),
            ),
            input={"portable": True},
        ),
        requirements=JobRequirements(
            executor_type="reference",
            runtime="python",
            cpu_cores_min=2,
            ram_min_bytes=2_000,
            preferred_worker_ids=(preferred_worker_id,),
            locality_refs=("workspace:portable",),
        ),
        workspace_ref="workspace:portable",
        snapshot_ref="snapshot:portable",
        artifact_refs=("artifact:input",),
        secret_refs=("secret:worker",),
        actor_ref="service:distributed-runtime",
        cancellation_ref="cancel:issue-14-failover",
        timeout_seconds=60,
        idempotency_key="worker-job:issue-14-failover",
        trace_parent="00-issue14-failover-trace",
    )


def _register_pair(
    runtime: DistributedRuntime,
    node_a: NodeRecord,
    worker_a: WorkerRecord,
    node_b: NodeRecord,
    worker_b: WorkerRecord,
    *,
    now: datetime = BASE_TIME,
) -> None:
    runtime.register(RegistrationRequest(node=node_a, workers=(worker_a,)), now=now)
    runtime.register(RegistrationRequest(node=node_b, workers=(worker_b,)), now=now)


async def _lose_owner(
    runtime: DistributedRuntime,
    worker_id: str,
    worker_job_id: str,
) -> None:
    runtime.detach_worker(worker_id)
    reconciled = await runtime.reconcile(now=BASE_TIME + timedelta(seconds=1))
    record = next(item for item in reconciled if item.job.worker_job_id == worker_job_id)
    assert record.state is DispatchState.LOST


def test_fenced_failover_moves_same_canonical_work_to_alternate_worker() -> None:
    async def scenario() -> None:
        node_a = _node("worker-a-node")
        node_b = _node("worker-b-node")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        fencer = _RecordingFencer()
        runtime = DistributedRuntime(DistributedRegistry(), ownership_fencer=fencer)
        _register_pair(runtime, node_a, worker_a, node_b, worker_b)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _job(worker_a.worker_id)

        original = await runtime.dispatch(job, now=BASE_TIME)
        assert original.worker_id == worker_a.worker_id
        assert original.job.dispatch_attempt == 1
        assert len(lifecycle_a.start_calls) == 1
        old_reservation = original.reservation_id

        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)
        failed_over = await runtime.failover(
            job.worker_job_id,
            now=BASE_TIME + timedelta(seconds=2),
        )

        assert failed_over.worker_id == worker_b.worker_id
        assert failed_over.worker_id != original.worker_id
        assert failed_over.job.worker_job_id == job.worker_job_id
        assert failed_over.job.execution == job.execution
        assert failed_over.job.requirements == job.requirements
        assert failed_over.job.workspace_ref == job.workspace_ref
        assert failed_over.job.snapshot_ref == job.snapshot_ref
        assert failed_over.job.artifact_refs == job.artifact_refs
        assert failed_over.job.secret_refs == job.secret_refs
        assert failed_over.job.actor_ref == job.actor_ref
        assert failed_over.job.cancellation_ref == job.cancellation_ref
        assert failed_over.job.idempotency_key == job.idempotency_key
        assert failed_over.job.trace_parent == job.trace_parent
        assert failed_over.job.dispatch_attempt == 2
        assert len(lifecycle_a.start_calls) == 1
        assert len(lifecycle_b.start_calls) == 1
        assert lifecycle_b.start_calls[0] == job.execution
        assert fencer.calls == [(worker_a.worker_id, job)]

        active = runtime.registry.active_reservations()
        assert len(active) == 1
        assert active[0].reservation_id != old_reservation
        assert active[0].worker_id == worker_b.worker_id

    asyncio.run(scenario())


def test_network_partition_without_fence_never_starts_parallel_worker() -> None:
    async def scenario() -> None:
        node_a = _node("partition-a")
        node_b = _node("partition-b")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        runtime = DistributedRuntime(DistributedRegistry())
        _register_pair(runtime, node_a, worker_a, node_b, worker_b)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _job(worker_a.worker_id)
        original = await runtime.dispatch(job, now=BASE_TIME)

        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)
        with pytest.raises(FailoverError) as rejected:
            await runtime.failover(job.worker_job_id)

        assert rejected.value.code is FailoverRejectionCode.FENCE_UNAVAILABLE
        assert runtime.get_record(job.worker_job_id).state is DispatchState.LOST
        assert len(lifecycle_a.start_calls) == 1
        assert lifecycle_b.start_calls == []
        active = runtime.registry.active_reservations()
        assert len(active) == 1
        assert active[0].reservation_id == original.reservation_id
        assert active[0].worker_id == worker_a.worker_id

    asyncio.run(scenario())


def test_retry_mode_never_rejects_failover_before_fencing_or_release() -> None:
    async def scenario() -> None:
        node_a = _node("never-a")
        node_b = _node("never-b")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        fencer = _RecordingFencer()
        runtime = DistributedRuntime(DistributedRegistry(), ownership_fencer=fencer)
        _register_pair(runtime, node_a, worker_a, node_b, worker_b)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _job(worker_a.worker_id, retry_mode=RetryMode.NEVER)
        original = await runtime.dispatch(job, now=BASE_TIME)

        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)
        with pytest.raises(FailoverError) as rejected:
            await runtime.failover(job.worker_job_id)

        assert rejected.value.code is FailoverRejectionCode.RETRY_FORBIDDEN
        assert fencer.calls == []
        assert lifecycle_b.start_calls == []
        active = runtime.registry.active_reservations()
        assert len(active) == 1
        assert active[0].reservation_id == original.reservation_id

    asyncio.run(scenario())


def test_mismatched_fence_receipt_does_not_release_old_ownership() -> None:
    async def scenario() -> None:
        node_a = _node("mismatch-a")
        node_b = _node("mismatch-b")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        fencer = _RecordingFencer(receipt_worker_id=worker_b.worker_id)
        runtime = DistributedRuntime(DistributedRegistry(), ownership_fencer=fencer)
        _register_pair(runtime, node_a, worker_a, node_b, worker_b)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _job(worker_a.worker_id)
        original = await runtime.dispatch(job, now=BASE_TIME)

        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)
        with pytest.raises(FailoverError) as rejected:
            await runtime.fence_for_failover(job.worker_job_id)

        assert rejected.value.code is FailoverRejectionCode.FENCE_IDENTITY_MISMATCH
        assert runtime.get_record(job.worker_job_id).state is DispatchState.LOST
        assert lifecycle_b.start_calls == []
        active = runtime.registry.active_reservations()
        assert len(active) == 1
        assert active[0].reservation_id == original.reservation_id

    asyncio.run(scenario())


def test_fenced_state_survives_control_plane_restart_before_redispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "distributed.json"
        node_a = _node("restart-a")
        node_b = _node("restart-b")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        fencer = _RecordingFencer()

        runtime = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
            ownership_fencer=fencer,
        )
        _register_pair(runtime, node_a, worker_a, node_b, worker_b)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _job(worker_a.worker_id)
        await runtime.dispatch(job, now=BASE_TIME)
        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)

        fenced = await runtime.fence_for_failover(
            job.worker_job_id,
            now=BASE_TIME + timedelta(seconds=2),
        )
        assert fenced.state is DispatchState.FENCED
        assert runtime.registry.active_reservations() == ()

        restored = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        restored_record = restored.get_record(job.worker_job_id)
        assert restored_record.state is DispatchState.FENCED
        assert restored_record.worker_id == worker_a.worker_id
        assert restored.registry.active_reservations() == ()

        restored.register(
            RegistrationRequest(node=node_b, workers=(worker_b,)),
            now=BASE_TIME + timedelta(seconds=3),
        )
        restored.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        replacement = await restored.redispatch_fenced(
            job.worker_job_id,
            now=BASE_TIME + timedelta(seconds=3),
        )

        assert replacement.worker_id == worker_b.worker_id
        assert replacement.job.dispatch_attempt == 2
        assert replacement.job.execution == job.execution
        assert len(lifecycle_a.start_calls) == 1
        assert len(lifecycle_b.start_calls) == 1
        active = restored.registry.active_reservations()
        assert len(active) == 1
        assert active[0].worker_id == worker_b.worker_id

    asyncio.run(scenario())


def test_cancel_after_fence_prevents_redispatch() -> None:
    async def scenario() -> None:
        node_a = _node("cancel-a")
        node_b = _node("cancel-b")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        runtime = DistributedRuntime(
            DistributedRegistry(),
            ownership_fencer=_RecordingFencer(),
        )
        _register_pair(runtime, node_a, worker_a, node_b, worker_b)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _job(worker_a.worker_id)
        await runtime.dispatch(job, now=BASE_TIME)
        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)
        await runtime.fence_for_failover(job.worker_job_id)

        cancelled = await runtime.cancel(job.worker_job_id)
        assert cancelled.state is DispatchState.TERMINAL
        assert cancelled.snapshot is not None
        assert cancelled.snapshot.status is RunStatus.CANCELLED

        with pytest.raises(FailoverError) as rejected:
            await runtime.redispatch_fenced(job.worker_job_id)
        assert rejected.value.code is FailoverRejectionCode.NOT_FENCED
        assert lifecycle_b.start_calls == []
        assert runtime.registry.active_reservations() == ()

    asyncio.run(scenario())


def test_late_old_worker_rejoin_cannot_reclaim_fenced_job() -> None:
    async def scenario() -> None:
        node_a = _node("rejoin-a")
        node_b = _node("rejoin-b")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        old_dispatcher = LocalWorker(worker_a.worker_id, lifecycle_a)
        runtime = DistributedRuntime(
            DistributedRegistry(),
            ownership_fencer=_RecordingFencer(),
        )
        _register_pair(runtime, node_a, worker_a, node_b, worker_b)
        runtime.attach_worker(old_dispatcher)
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        job = _job(worker_a.worker_id)
        await runtime.dispatch(job, now=BASE_TIME)
        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)
        replacement = await runtime.failover(
            job.worker_job_id,
            now=BASE_TIME + timedelta(seconds=2),
        )
        assert replacement.worker_id == worker_b.worker_id

        runtime.attach_worker(old_dispatcher)
        reconciled = await runtime.reconcile(now=BASE_TIME + timedelta(seconds=3))
        current = next(item for item in reconciled if item.job.worker_job_id == job.worker_job_id)

        assert current.worker_id == worker_b.worker_id
        assert current.job.dispatch_attempt == 2
        assert len(lifecycle_a.start_calls) == 1
        assert len(lifecycle_b.start_calls) == 1

    asyncio.run(scenario())


def test_fenced_redispatch_uses_deterministic_alternate_worker_selection() -> None:
    async def scenario() -> None:
        node_a = _node("det-a")
        node_b = _node("det-b")
        node_c = _node("det-c")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        worker_c = _worker(node_c)
        lifecycle_a = FakeLifecycleBackend()
        lifecycle_b = FakeLifecycleBackend()
        lifecycle_c = FakeLifecycleBackend()
        runtime = DistributedRuntime(
            DistributedRegistry(),
            ownership_fencer=_RecordingFencer(),
        )
        runtime.register(RegistrationRequest(node=node_a, workers=(worker_a,)), now=BASE_TIME)
        runtime.register(RegistrationRequest(node=node_b, workers=(worker_b,)), now=BASE_TIME)
        runtime.register(RegistrationRequest(node=node_c, workers=(worker_c,)), now=BASE_TIME)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, lifecycle_b))
        runtime.attach_worker(LocalWorker(worker_c.worker_id, lifecycle_c))
        job = _job(worker_a.worker_id)
        await runtime.dispatch(job, now=BASE_TIME)
        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)

        replacement = await runtime.failover(
            job.worker_job_id,
            now=BASE_TIME + timedelta(seconds=2),
        )
        expected = min(worker_b.worker_id, worker_c.worker_id)

        assert replacement.worker_id == expected
        assert replacement.worker_id != worker_a.worker_id
        assert len(lifecycle_a.start_calls) == 1
        assert len(lifecycle_b.start_calls) + len(lifecycle_c.start_calls) == 1

    asyncio.run(scenario())


def test_fenced_job_without_alternate_worker_remains_safely_fenced() -> None:
    async def scenario() -> None:
        node_a = _node("solo")
        worker_a = _worker(node_a)
        lifecycle_a = FakeLifecycleBackend()
        runtime = DistributedRuntime(
            DistributedRegistry(),
            ownership_fencer=_RecordingFencer(),
        )
        runtime.register(RegistrationRequest(node=node_a, workers=(worker_a,)), now=BASE_TIME)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_a))
        job = _job(worker_a.worker_id)
        await runtime.dispatch(job, now=BASE_TIME)
        await _lose_owner(runtime, worker_a.worker_id, job.worker_job_id)
        fenced = await runtime.fence_for_failover(job.worker_job_id)

        with pytest.raises(FailoverError) as rejected:
            await runtime.redispatch_fenced(job.worker_job_id)

        assert rejected.value.code is FailoverRejectionCode.NO_ALTERNATE_WORKER
        assert runtime.get_record(job.worker_job_id) == fenced
        assert runtime.registry.active_reservations() == ()
        assert len(lifecycle_a.start_calls) == 1

    asyncio.run(scenario())
