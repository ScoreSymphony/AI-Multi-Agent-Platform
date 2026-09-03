from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionRequest, ExecutionSnapshot
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
    JsonDistributedStateStore,
    LocalWorker,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    ReservationStatus,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.testing import FakeLifecycleBackend

BASE_TIME = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)


def _node() -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name="restart-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8.0,
            cpu_cores_available=8.0,
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


def _job() -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id=f"corr:{task_id}"),
            input={"request": "restart-safe"},
        ),
        requirements=JobRequirements(
            executor_type="reference",
            cpu_cores_min=2.0,
            ram_min_bytes=2_000,
        ),
        workspace_ref="workspace:portable",
        snapshot_ref="snapshot:portable",
        artifact_refs=("artifact:input",),
        secret_refs=("secret:scoped-reference",),
        dispatch_attempt=1,
        idempotency_key="restart-safe-job",
        trace_parent="trace-parent:test",
    )


class LostAcknowledgementWorker:
    """Fixture that accepts work locally and then loses only the dispatch acknowledgement."""

    def __init__(self, worker: LocalWorker) -> None:
        self._worker = worker

    @property
    def worker_id(self) -> str:
        return self._worker.worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        await self._worker.dispatch(job)
        raise RuntimeError("simulated acknowledgement loss")

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._worker.get(worker_job_id)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._worker.cancel(worker_job_id)


def test_scheduler_restart_restores_claim_and_dispatch_ownership(tmp_path: Path) -> None:
    state_path = tmp_path / "distributed-state.json"
    lifecycle = FakeLifecycleBackend()
    node = _node()
    worker_record = _worker(node)
    request = RegistrationRequest(node=node, workers=(worker_record,))
    job = _job()

    async def scenario() -> None:
        first_registry = DistributedRegistry(
            heartbeat_timeout=timedelta(seconds=10),
            reservation_ttl=timedelta(seconds=30),
        )
        first_runtime = DistributedRuntime(
            first_registry,
            state_store=JsonDistributedStateStore(state_path),
        )
        first_runtime.register(request, now=BASE_TIME)
        local_worker = LocalWorker(worker_record.worker_id, lifecycle)
        first_runtime.attach_worker(local_worker)
        dispatched = await first_runtime.dispatch(job, now=BASE_TIME)

        assert dispatched.state is DispatchState.DISPATCHED
        assert first_registry.active_reservations()[0].status is ReservationStatus.ACTIVE
        assert state_path.exists()

        restored_registry = DistributedRegistry(
            heartbeat_timeout=timedelta(seconds=10),
            reservation_ttl=timedelta(seconds=30),
        )
        restored_runtime = DistributedRuntime(
            restored_registry,
            state_store=JsonDistributedStateStore(state_path),
        )

        # Persisted health is deliberately not trusted as fresh liveness after restart.
        assert restored_registry.get_node(node.node_id).status is NodeStatus.OFFLINE
        assert restored_registry.get_worker(worker_record.worker_id).status is WorkerStatus.OFFLINE
        restored_claim = restored_registry.active_reservations()[0]
        assert restored_claim.reservation_id == dispatched.reservation_id
        assert restored_claim.status is ReservationStatus.ACTIVE
        restored_record = restored_runtime.get_record(job.worker_job_id)
        assert restored_record.job.workspace_ref == "workspace:portable"
        assert restored_record.job.snapshot_ref == "snapshot:portable"
        assert restored_record.job.artifact_refs == ("artifact:input",)
        assert restored_record.job.secret_refs == ("secret:scoped-reference",)
        assert (
            restored_record.job.execution.context.correlation_id
            == job.execution.context.correlation_id
        )

        restored_runtime.register(request, now=BASE_TIME + timedelta(seconds=1))
        restored_runtime.attach_worker(local_worker)
        reconciled = await restored_runtime.reconcile(now=BASE_TIME + timedelta(seconds=1))

        assert reconciled[0].state is DispatchState.RUNNING
        assert reconciled[0].snapshot is not None
        assert reconciled[0].snapshot.run_id == job.execution.run_id
        assert len(lifecycle.start_calls) == 1

        duplicate = await restored_runtime.dispatch(job, now=BASE_TIME + timedelta(seconds=2))
        assert duplicate == reconciled[0]
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())


def test_lost_dispatch_ack_is_reconciled_without_duplicate_execution(tmp_path: Path) -> None:
    state_path = tmp_path / "distributed-lost-ack.json"
    lifecycle = FakeLifecycleBackend()
    node = _node()
    worker_record = _worker(node)
    request = RegistrationRequest(node=node, workers=(worker_record,))
    job = _job()

    async def scenario() -> None:
        registry = DistributedRegistry(reservation_ttl=timedelta(seconds=30))
        runtime = DistributedRuntime(
            registry,
            state_store=JsonDistributedStateStore(state_path),
        )
        runtime.register(request, now=BASE_TIME)
        accepted_worker = LocalWorker(worker_record.worker_id, lifecycle)
        runtime.attach_worker(LostAcknowledgementWorker(accepted_worker))

        with pytest.raises(RuntimeError, match="acknowledgement loss"):
            await runtime.dispatch(job, now=BASE_TIME)

        uncertain = runtime.get_record(job.worker_job_id)
        assert uncertain.state is DispatchState.LOST
        assert uncertain.last_error == "dispatch_outcome_unknown"
        assert registry.active_reservations()[0].status is ReservationStatus.RESERVED
        assert len(lifecycle.start_calls) == 1

        restored_registry = DistributedRegistry(reservation_ttl=timedelta(seconds=30))
        restored_runtime = DistributedRuntime(
            restored_registry,
            state_store=JsonDistributedStateStore(state_path),
        )
        restored_runtime.register(request, now=BASE_TIME + timedelta(seconds=1))
        restored_runtime.attach_worker(accepted_worker)

        recovered = await restored_runtime.reconcile(now=BASE_TIME + timedelta(seconds=1))

        assert recovered[0].state is DispatchState.RUNNING
        assert restored_registry.active_reservations()[0].status is ReservationStatus.ACTIVE
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())
