from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts.types import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    DistributedTelemetry,
    Heartbeat,
    JobRequirements,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend

BASE = datetime(2026, 9, 4, 0, 0, tzinfo=UTC)


def _stack() -> tuple[
    DistributedRuntime,
    InMemoryExporter,
    NodeRecord,
    WorkerRecord,
    WorkerRecord,
    FakeLifecycleBackend,
]:
    exporter = InMemoryExporter()
    telemetry = DistributedTelemetry(Telemetry(exporter))
    registry = DistributedRegistry(heartbeat_timeout=timedelta(seconds=10))
    runtime = DistributedRuntime(registry, telemetry=telemetry)
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="telemetry-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=32_000,
            ram_available_bytes=32_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )
    selected = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        concurrency_limit=2,
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )
    rejected = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        concurrency_limit=2,
        draining=True,
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )
    runtime.register(RegistrationRequest(node=node, workers=(selected, rejected)), now=BASE)
    lifecycle = FakeLifecycleBackend()
    runtime.attach_worker(LocalWorker(selected.worker_id, lifecycle))
    return runtime, exporter, node, selected, rejected, lifecycle


def _job() -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id=task_id,
                causation_id="cause-14-telemetry",
            ),
            input={"private_marker": "MUST_NOT_APPEAR_IN_TELEMETRY"},
        ),
        requirements=JobRequirements(cpu_cores_min=1, ram_min_bytes=1024),
        secret_refs=("secret:must-not-appear",),
    )


def test_scheduler_reservation_and_dispatch_emit_correlated_safe_telemetry() -> None:
    runtime, exporter, node, selected, rejected, lifecycle = _stack()
    job = _job()

    async def scenario() -> None:
        record = await runtime.dispatch(job, now=BASE + timedelta(seconds=1))
        assert record.worker_id == selected.worker_id
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())

    metric_names = {metric.name for metric in exporter.metrics}
    assert {
        "platform.scheduler.candidates",
        "platform.scheduler.rejections",
        "platform.scheduler.reservations",
        "platform.scheduler.reserved_cpu_cores",
        "platform.scheduler.reserved_ram_bytes",
        "platform.worker.dispatch.duration_seconds",
    } <= metric_names

    candidates = [metric for metric in exporter.metrics if metric.name == "platform.scheduler.candidates"]
    assert {metric.context.worker_id for metric in candidates} == {
        selected.worker_id,
        rejected.worker_id,
    }
    assert all(metric.context.node_id == node.node_id for metric in candidates)
    assert all(metric.context.run_id == job.execution.run_id for metric in candidates)
    assert all(metric.context.worker_job_id == job.worker_job_id for metric in candidates)

    rejections = [metric for metric in exporter.metrics if metric.name == "platform.scheduler.rejections"]
    assert any(metric.attributes["reason_code"] == "worker_draining" for metric in rejections)

    timeline_names = {entry.event_name for entry in exporter.timeline}
    assert "scheduler.decision" in timeline_names
    serialized = repr((exporter.metrics, exporter.logs, exporter.timeline, exporter.spans))
    assert "MUST_NOT_APPEAR_IN_TELEMETRY" not in serialized
    assert "secret:must-not-appear" not in serialized


def test_heartbeat_and_reconciliation_emit_liveness_and_loss_evidence() -> None:
    runtime, exporter, node, selected, rejected, _ = _stack()
    job = _job()

    async def scenario() -> None:
        runtime.heartbeat(
            Heartbeat(
                node_id=node.node_id,
                sequence=1,
                observed_at=BASE + timedelta(seconds=2),
                workers=(selected, rejected),
            )
        )
        await runtime.dispatch(job, now=BASE + timedelta(seconds=3))
        runtime.detach_worker(selected.worker_id)
        records = await runtime.reconcile(now=BASE + timedelta(seconds=15))
        assert records[0].state.value == "lost"
        assert records[0].last_error == "worker_unreachable"

    asyncio.run(scenario())

    metric_names = {metric.name for metric in exporter.metrics}
    assert {
        "platform.node.heartbeats",
        "platform.worker.heartbeats",
        "platform.node.heartbeat_age_seconds",
        "platform.worker.heartbeat_age_seconds",
        "platform.node.cpu_cores_available",
        "platform.node.ram_available_bytes",
        "platform.worker.active_jobs",
        "platform.worker.reconciliations",
    } <= metric_names

    node_age = [
        metric
        for metric in exporter.metrics
        if metric.name == "platform.node.heartbeat_age_seconds"
        and metric.context.node_id == node.node_id
    ]
    assert node_age
    assert node_age[-1].value == 13.0

    reconciled = [entry for entry in exporter.timeline if entry.event_name == "worker.reconciled"]
    assert reconciled
    assert reconciled[-1].context.worker_job_id == job.worker_job_id
    assert reconciled[-1].attributes["current_state"] == "lost"
    assert reconciled[-1].attributes["error_code"] == "worker_unreachable"
