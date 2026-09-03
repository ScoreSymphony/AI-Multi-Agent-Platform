from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import (
    AcceleratorResource,
    DeterministicScheduler,
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    JobRequirements,
    LocalWorker,
    NoEligibleWorkerError,
    NodeRecord,
    RegistrationRequest,
    ReservationStatus,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.testing import FakeLifecycleBackend

BASE_TIME = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)


def _resources(
    *,
    cpu: float = 8.0,
    ram: int = 16_000,
    storage: int = 100_000,
    vram: int = 0,
) -> ResourceSnapshot:
    accelerators = ()
    if vram:
        accelerators = (
            AcceleratorResource(
                accelerator_id="gpu-0",
                memory_total_bytes=vram,
                memory_available_bytes=vram,
            ),
        )
    return ResourceSnapshot(
        cpu_cores_total=cpu,
        cpu_cores_available=cpu,
        ram_total_bytes=ram,
        ram_available_bytes=ram,
        storage_total_bytes=storage,
        storage_available_bytes=storage,
        accelerators=accelerators,
    )


def _node(
    *,
    name: str,
    resources: ResourceSnapshot | None = None,
    labels: tuple[str, ...] = (),
    models: tuple[str, ...] = (),
    locality: tuple[str, ...] = (),
) -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name=name,
        resources=resources or _resources(),
        labels=labels,
        model_refs=models,
        locality_refs=locality,
        supported_runtimes=("python",),
    )


def _worker(
    node: NodeRecord,
    *,
    executors: tuple[str, ...] = ("reference",),
    capabilities: tuple[str, ...] = (),
    models: tuple[str, ...] = (),
    locality: tuple[str, ...] = (),
    concurrency: int = 1,
) -> WorkerRecord:
    return WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=executors,
        capability_refs=capabilities,
        supported_runtimes=("python",),
        model_refs=models,
        locality_refs=locality,
        concurrency_limit=concurrency,
    )


def _job(*, requirements: JobRequirements | None = None) -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id=f"corr:{task_id}",
                project_id=None,
            ),
        ),
        requirements=requirements or JobRequirements(),
        workspace_ref="workspace:test",
        snapshot_ref="snapshot:test",
        artifact_refs=("artifact:input",),
        secret_refs=("secret:model",),
    )


def _register(
    registry: DistributedRegistry,
    node: NodeRecord,
    *workers: WorkerRecord,
    now: datetime = BASE_TIME,
) -> None:
    registry.register(RegistrationRequest(node=node, workers=workers), now=now)


def test_single_node_uses_same_scheduler_path_as_multi_node() -> None:
    registry = DistributedRegistry()
    node = _node(name="local")
    worker = _worker(node)
    _register(registry, node, worker)

    placement = DeterministicScheduler(registry).schedule(
        _job(requirements=JobRequirements(executor_type="reference", cpu_cores_min=1.0)),
        now=BASE_TIME,
    )

    assert placement.decision.selected_worker_id == worker.worker_id
    assert placement.reservation.worker_id == worker.worker_id
    assert placement.reservation.status is ReservationStatus.ACTIVE


def test_two_node_selection_filters_resources_capabilities_and_model() -> None:
    registry = DistributedRegistry()
    cpu_node = _node(name="cpu", resources=_resources(cpu=4.0, ram=8_000))
    gpu_node = _node(
        name="gpu",
        resources=_resources(cpu=16.0, ram=64_000, vram=24_000),
        models=("model:large",),
    )
    cpu_worker = _worker(cpu_node, capabilities=("tool:read",))
    gpu_worker = _worker(
        gpu_node,
        capabilities=("tool:read", "tool:gpu"),
        models=("model:large",),
        concurrency=2,
    )
    _register(registry, cpu_node, cpu_worker)
    _register(registry, gpu_node, gpu_worker)

    decision = DeterministicScheduler(registry).evaluate(
        _job(
            requirements=JobRequirements(
                executor_type="reference",
                capability_refs=("tool:gpu",),
                cpu_cores_min=8.0,
                ram_min_bytes=32_000,
                gpu="required",
                vram_min_bytes=12_000,
                model_ref="model:large",
                runtime="python",
            )
        )
    )

    assert decision.selected_worker_id == gpu_worker.worker_id
    cpu_evaluation = next(item for item in decision.evaluations if item.worker_id == cpu_worker.worker_id)
    assert not cpu_evaluation.accepted
    assert {reason.code.value for reason in cpu_evaluation.reasons} >= {
        "capability_unsupported",
        "cpu_insufficient",
        "ram_insufficient",
        "gpu_required",
        "vram_insufficient",
        "model_unavailable",
    }


def test_deterministic_tie_break_uses_canonical_worker_id() -> None:
    registry = DistributedRegistry()
    node_a = _node(name="a")
    node_b = _node(name="b")
    worker_a = _worker(node_a)
    worker_b = _worker(node_b)
    _register(registry, node_a, worker_a)
    _register(registry, node_b, worker_b)

    decision = DeterministicScheduler(registry).evaluate(_job())

    assert decision.selected_worker_id == min(worker_a.worker_id, worker_b.worker_id)


def test_locality_is_preference_not_canonical_identity() -> None:
    registry = DistributedRegistry()
    remote = _node(name="remote")
    local = _node(name="local", locality=("workspace:alpha",))
    remote_worker = _worker(remote)
    local_worker = _worker(local, locality=("snapshot:alpha",))
    _register(registry, remote, remote_worker)
    _register(registry, local, local_worker)

    decision = DeterministicScheduler(registry).evaluate(
        _job(requirements=JobRequirements(locality_refs=("workspace:alpha", "snapshot:alpha")))
    )

    assert decision.selected_worker_id == local_worker.worker_id


def test_draining_and_unhealthy_workers_are_rejected() -> None:
    registry = DistributedRegistry()
    node = _node(name="node")
    draining = _worker(node)
    unhealthy = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        status=WorkerStatus.UNHEALTHY,
    )
    _register(registry, node, draining, unhealthy)
    registry.set_worker_draining(draining.worker_id, draining=True)

    with pytest.raises(NoEligibleWorkerError):
        DeterministicScheduler(registry).schedule(_job(), now=BASE_TIME)


def test_reservation_prevents_overcommit_and_duplicate_claims_are_idempotent() -> None:
    registry = DistributedRegistry()
    node = _node(name="small", resources=_resources(cpu=2.0, ram=2_000))
    worker = _worker(node, concurrency=1)
    _register(registry, node, worker)
    scheduler = DeterministicScheduler(registry)
    first_job = _job(requirements=JobRequirements(cpu_cores_min=2.0, ram_min_bytes=2_000))

    first = scheduler.schedule(first_job, now=BASE_TIME)
    duplicate = scheduler.schedule(first_job, now=BASE_TIME)

    assert duplicate.reservation.reservation_id == first.reservation.reservation_id
    with pytest.raises(NoEligibleWorkerError):
        scheduler.schedule(
            _job(requirements=JobRequirements(cpu_cores_min=1.0, ram_min_bytes=1_000)),
            now=BASE_TIME,
        )


def test_heartbeat_timeout_marks_offline_and_reregistration_rejoins() -> None:
    registry = DistributedRegistry(heartbeat_timeout=timedelta(seconds=10))
    node = _node(name="rejoin")
    worker = _worker(node)
    _register(registry, node, worker)

    assert registry.expire_heartbeats(now=BASE_TIME + timedelta(seconds=11)) == (node.node_id,)
    assert registry.get_worker(worker.worker_id).status is WorkerStatus.OFFLINE

    _register(registry, node, worker, now=BASE_TIME + timedelta(seconds=12))

    assert registry.get_worker(worker.worker_id).status is WorkerStatus.HEALTHY
    assert registry.get_node(node.node_id).status.value == "online"


def test_heartbeat_sequence_is_monotonic_and_duplicate_is_idempotent() -> None:
    registry = DistributedRegistry()
    node = _node(name="heartbeat")
    worker = _worker(node)
    _register(registry, node, worker)
    heartbeat = Heartbeat(
        node_id=node.node_id,
        sequence=1,
        observed_at=BASE_TIME + timedelta(seconds=1),
        workers=(worker,),
    )

    first = registry.heartbeat(heartbeat)
    duplicate = registry.heartbeat(heartbeat)

    assert duplicate == first
    with pytest.raises(Exception, match="stale heartbeat sequence"):
        registry.heartbeat(
            Heartbeat(
                node_id=node.node_id,
                sequence=0,
                observed_at=BASE_TIME,
            )
        )


def test_local_worker_dispatch_is_idempotent_and_preserves_execution_identity() -> None:
    lifecycle = FakeLifecycleBackend()
    node = _node(name="local")
    worker_record = _worker(node)
    registry = DistributedRegistry()
    _register(registry, node, worker_record)
    local_worker = LocalWorker(worker_record.worker_id, lifecycle)
    runtime = DistributedRuntime(registry)
    runtime.attach_worker(local_worker)
    job = _job()

    async def scenario() -> None:
        first = await runtime.dispatch(job, now=BASE_TIME)
        duplicate = await runtime.dispatch(job, now=BASE_TIME)
        assert duplicate == first
        assert first.handle is not None
        assert first.handle.run_id == job.execution.run_id
        assert len(lifecycle.start_calls) == 1
        assert lifecycle.start_calls[0].context.correlation_id == job.execution.context.correlation_id

    asyncio.run(scenario())


def test_unreachable_cancellation_is_reconciled_after_worker_returns() -> None:
    lifecycle = FakeLifecycleBackend()
    node = _node(name="local")
    worker_record = _worker(node)
    registry = DistributedRegistry()
    _register(registry, node, worker_record)
    local_worker = LocalWorker(worker_record.worker_id, lifecycle)
    runtime = DistributedRuntime(registry)
    runtime.attach_worker(local_worker)
    job = _job()

    async def scenario() -> None:
        await runtime.dispatch(job, now=BASE_TIME)
        runtime.detach_worker(worker_record.worker_id)
        pending = await runtime.cancel(job.worker_job_id)
        assert pending.state is DispatchState.CANCEL_PENDING

        runtime.attach_worker(local_worker)
        reconciled = await runtime.reconcile(now=BASE_TIME + timedelta(seconds=1))
        assert reconciled[0].state is DispatchState.TERMINAL
        assert reconciled[0].snapshot is not None
        assert reconciled[0].snapshot.status.value == "cancelled"
        assert not registry.active_reservations()

    asyncio.run(scenario())


def test_worker_loss_becomes_lost_and_rejoin_reconciles_running_state() -> None:
    lifecycle = FakeLifecycleBackend()
    registry = DistributedRegistry(heartbeat_timeout=timedelta(seconds=5))
    node = _node(name="remote")
    worker_record = _worker(node)
    _register(registry, node, worker_record)
    local_transport_fixture = LocalWorker(worker_record.worker_id, lifecycle)
    runtime = DistributedRuntime(registry)
    runtime.attach_worker(local_transport_fixture)
    job = _job()

    async def scenario() -> None:
        await runtime.dispatch(job, now=BASE_TIME)
        lost = await runtime.reconcile(now=BASE_TIME + timedelta(seconds=6))
        assert lost[0].state is DispatchState.LOST

        _register(registry, node, worker_record, now=BASE_TIME + timedelta(seconds=7))
        recovered = await runtime.reconcile(now=BASE_TIME + timedelta(seconds=7))
        assert recovered[0].state is DispatchState.RUNNING
        assert recovered[0].snapshot is not None
        assert recovered[0].snapshot.run_id == job.execution.run_id

    asyncio.run(scenario())
