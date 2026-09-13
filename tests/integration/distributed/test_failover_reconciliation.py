from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
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
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.high_availability import (
    AvailabilityMode,
    ControlPlaneFailoverService,
    ControlPlaneRole,
    DistributedRuntimeFailoverReconciler,
    FencingToken,
    InMemoryCoordinationProvider,
    NotLeaderError,
    ReconciliationResult,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend

NOW = datetime(2026, 9, 6, 2, 0, tzinfo=UTC)
LEASE_TTL = timedelta(seconds=30)
RESERVATION_TTL = timedelta(seconds=5)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


@dataclass
class PromotionScopeReconciler:
    service: ControlPlaneFailoverService | None = None

    async def reconcile(
        self,
        *,
        token: FencingToken,
        previous_epoch: int,
        reason: str,
    ) -> ReconciliationResult:
        del previous_epoch, reason
        assert self.service is not None
        assert self.service.role is ControlPlaneRole.PROMOTING
        with pytest.raises(NotLeaderError):
            await self.service.require_authority()
        grant = await self.service.require_reconciliation_authority()
        assert grant.fencing_token == token
        return ReconciliationResult(recovered_items=1)


def _node_and_worker() -> tuple[NodeRecord, WorkerRecord]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-89-worker-node",
        resources=ResourceSnapshot(
            cpu_cores_total=4,
            cpu_cores_available=4,
            ram_total_bytes=8_000,
            ram_available_bytes=8_000,
            storage_total_bytes=20_000,
            storage_available_bytes=20_000,
        ),
        supported_runtimes=("python",),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
        concurrency_limit=2,
    )
    return node, worker


def _job(worker_id: str) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(correlation_id="corr:issue-89-reconciliation"),
        ),
        requirements=JobRequirements(
            preferred_worker_ids=(worker_id,),
            cpu_cores_min=1,
            ram_min_bytes=1_000,
        ),
    )


def test_promotion_exposes_only_narrow_reconciliation_authority() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider(clock=MutableClock())
        reconciler = PromotionScopeReconciler()
        service = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=LEASE_TTL,
            reconciler=reconciler,
        )
        reconciler.service = service

        assert await service.start(reason="promotion-scope") is True
        assert service.role is ControlPlaneRole.ACTIVE
        status = await service.status()
        assert status.last_reconciliation == ReconciliationResult(recovered_items=1)

    asyncio.run(scenario())


def test_distributed_promotion_reconciler_expires_stale_reservations() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        registry = DistributedRegistry(
            heartbeat_timeout=timedelta(minutes=1),
            reservation_ttl=RESERVATION_TTL,
        )
        node, worker = _node_and_worker()
        registry.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
        reservation = registry.reserve(
            worker_job_id=new_id("worker_job"),
            worker_id=worker.worker_id,
            requirements=JobRequirements(cpu_cores_min=1, ram_min_bytes=1_000),
            now=NOW,
        )
        assert registry.active_reservations() == (reservation,)

        clock.advance(RESERVATION_TTL + timedelta(milliseconds=1))
        coordinator = InMemoryCoordinationProvider(clock=clock)
        runtime = DistributedRuntime(registry)
        reconciler = DistributedRuntimeFailoverReconciler(
            runtime,
            coordinator,
            clock=clock,
        )
        service = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=LEASE_TTL,
            reconciler=reconciler,
        )

        assert await service.start(reason="expire-stale-reservation") is True
        assert registry.active_reservations() == ()
        status = await service.status()
        assert status.last_reconciliation is not None
        assert status.last_reconciliation.rejected_stale_items == 1
        assert "expired_reservations=1" in status.last_reconciliation.details

    asyncio.run(scenario())


def test_restart_promotion_reconciles_running_work_and_preserves_worker_identity(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        state_path = tmp_path / "distributed-state.json"
        store = JsonDistributedStateStore(state_path)
        registry = DistributedRegistry(
            heartbeat_timeout=timedelta(minutes=1),
            reservation_ttl=RESERVATION_TTL,
        )
        node, worker = _node_and_worker()
        runtime = DistributedRuntime(registry, state_store=store)
        runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
        lifecycle = FakeLifecycleBackend()
        runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
        job = _job(worker.worker_id)

        accepted = await runtime.dispatch(job, now=NOW)
        assert accepted.state is DispatchState.DISPATCHED
        assert len(lifecycle.start_calls) == 1

        observed = await runtime.reconcile(now=NOW + timedelta(seconds=1))
        running = next(
            record for record in observed if record.job.worker_job_id == job.worker_job_id
        )
        assert running.state is DispatchState.RUNNING
        active_reservations = registry.active_reservations()
        assert len(active_reservations) == 1

        clock = MutableClock(active_reservations[0].expires_at + timedelta(milliseconds=1))
        restored_registry = DistributedRegistry(
            heartbeat_timeout=timedelta(minutes=1),
            reservation_ttl=RESERVATION_TTL,
        )
        restored_runtime = DistributedRuntime(restored_registry, state_store=store)
        assert restored_registry.get_node(node.node_id).status is NodeStatus.OFFLINE
        assert restored_registry.get_worker(worker.worker_id).status is WorkerStatus.OFFLINE
        restored_record = restored_runtime.get_record(job.worker_job_id)
        assert restored_record.job.execution.run_id == job.execution.run_id
        assert restored_record.state is DispatchState.RUNNING

        coordinator = InMemoryCoordinationProvider(clock=clock)
        reconciler = DistributedRuntimeFailoverReconciler(
            restored_runtime,
            coordinator,
            clock=clock,
        )
        service = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=LEASE_TTL,
            reconciler=reconciler,
        )

        assert await service.start(reason="control-plane-restart") is True
        reconciled = restored_runtime.get_record(job.worker_job_id)
        assert reconciled.state is DispatchState.LOST
        assert reconciled.job.worker_job_id == job.worker_job_id
        assert reconciled.job.execution.run_id == job.execution.run_id
        assert restored_registry.active_reservations() == ()
        assert len(lifecycle.start_calls) == 1

        reregistered = restored_runtime.register(
            RegistrationRequest(node=node, workers=(worker,)),
            now=clock.value,
        )
        assert reregistered.node_id == node.node_id
        assert restored_registry.get_worker(worker.worker_id).worker_id == worker.worker_id
        assert restored_registry.get_worker(worker.worker_id).status is WorkerStatus.HEALTHY
        assert restored_registry.active_reservations() == ()
        assert restored_runtime.get_record(job.worker_job_id).state is DispatchState.LOST

        status = await service.status()
        assert status.last_reconciliation is not None
        assert status.last_reconciliation.recovered_items == 1
        assert status.last_reconciliation.rejected_stale_items == 2
        assert "lost_ownership=1" in status.last_reconciliation.details
        assert "expired_reservations=1" in status.last_reconciliation.details

    asyncio.run(scenario())
