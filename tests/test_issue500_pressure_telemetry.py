from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ExecutionRequest,
    OperationContext,
)
from ai_multi_agent_platform.distributed import (
    DeterministicScheduler,
    DistributedRegistry,
    DistributedTelemetry,
    HostPressureSnapshot,
    InMemoryPressureSnapshotProvider,
    JobRequirements,
    NodeRecord,
    PressureAdmissionPolicy,
    PressureKind,
    PressureSignal,
    PressureState,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry

NOW = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)


def _stack() -> tuple[
    DeterministicScheduler,
    InMemoryPressureSnapshotProvider,
    InMemoryExporter,
    NodeRecord,
    WorkerRecord,
    WorkerJobRequest,
]:
    registry = DistributedRegistry()
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="pressure-telemetry-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
    )
    worker = WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id)
    registry.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
    exporter = InMemoryExporter()
    provider = InMemoryPressureSnapshotProvider()
    scheduler = DeterministicScheduler(
        registry,
        telemetry=DistributedTelemetry(Telemetry(exporter)),
        pressure_provider=provider,
        pressure_policy=PressureAdmissionPolicy(),
        workload_class_resolver=lambda _job: "heavy",
    )
    task_id = new_id("task")
    job = WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id=task_id,
                causation_id="cause-pressure-telemetry",
            ),
        ),
        requirements=JobRequirements(),
    )
    return scheduler, provider, exporter, node, worker, job


def _snapshot(state: PressureState, *, observed_at: datetime) -> HostPressureSnapshot:
    return HostPressureSnapshot(
        state=state,
        observed_at=observed_at,
        source_ref="linux:/private/host/path-that-must-not-be-exported",
        signals=(
            PressureSignal(
                PressureKind.MEMORY,
                state,
                25.0 if state is not PressureState.HEALTHY else 1.0,
                "percent_stall_avg10",
            ),
        ),
        provider_metadata=(
            AdapterMetadata(
                "linux.host_pressure",
                {"private_path": "/sys/fs/cgroup/private-service"},
            ),
        ),
    )


def test_scheduler_emits_portable_pressure_metrics_and_structured_admission_reasons() -> None:
    scheduler, provider, exporter, node, worker, job = _stack()
    provider.put(node.node_id, _snapshot(PressureState.CRITICAL, observed_at=NOW))

    decision = scheduler.evaluate(job, now=NOW)

    assert not decision.evaluations[0].accepted
    metric_names = {metric.name for metric in exporter.metrics}
    assert {
        "platform.node.pressure.observations",
        "platform.node.pressure.signals",
        "platform.node.pressure.signal_value",
        "platform.scheduler.pressure_admissions",
        "platform.scheduler.pressure_snapshot_age_seconds",
        "platform.scheduler.pressure_admission_reasons",
    } <= metric_names
    admissions = [
        metric
        for metric in exporter.metrics
        if metric.name == "platform.scheduler.pressure_admissions"
    ]
    assert admissions[-1].context.node_id == node.node_id
    assert admissions[-1].context.worker_id == worker.worker_id
    assert admissions[-1].context.run_id == job.execution.run_id
    assert admissions[-1].attributes["action"] == "deny_temporarily"
    assert admissions[-1].attributes["pressure_state"] == "critical"
    assert admissions[-1].attributes["workload_class"] == "heavy"
    reasons = [
        metric
        for metric in exporter.metrics
        if metric.name == "platform.scheduler.pressure_admission_reasons"
    ]
    assert reasons[-1].attributes["reason_code"] == "pressure_critical"
    assert any(
        entry.event_name == "scheduler.pressure_admission" for entry in exporter.timeline
    )

    serialized = repr((exporter.metrics, exporter.logs, exporter.timeline, exporter.spans))
    assert "/private/host/path-that-must-not-be-exported" not in serialized
    assert "/sys/fs/cgroup/private-service" not in serialized
    assert "linux.host_pressure" not in serialized


def test_pressure_recovery_uses_existing_timeline_and_deduplicates_shared_snapshot() -> None:
    scheduler, provider, exporter, node, _worker, job = _stack()
    elevated = _snapshot(PressureState.ELEVATED, observed_at=NOW)
    provider.put(node.node_id, elevated)

    scheduler.evaluate(job, now=NOW)
    scheduler.evaluate(job, now=NOW)

    observations = [
        metric
        for metric in exporter.metrics
        if metric.name == "platform.node.pressure.observations"
    ]
    assert len(observations) == 1

    recovered_at = NOW + timedelta(seconds=5)
    provider.put(node.node_id, _snapshot(PressureState.HEALTHY, observed_at=recovered_at))
    scheduler.evaluate(job, now=recovered_at)

    recovered = [
        entry for entry in exporter.timeline if entry.event_name == "node.pressure.recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0].context.node_id == node.node_id
    assert recovered[0].attributes["previous_state"] == "elevated"
    assert recovered[0].attributes["current_state"] == "healthy"
    assert recovered[0].timestamp == recovered_at


def test_diagnostic_pressure_admission_does_not_count_as_scheduler_admission() -> None:
    scheduler, provider, exporter, node, worker, job = _stack()
    provider.put(node.node_id, _snapshot(PressureState.HEALTHY, observed_at=NOW))

    diagnostic = scheduler.pressure_admission(job, worker.worker_id, now=NOW)

    assert diagnostic is not None
    assert diagnostic.admits
    assert not [
        metric
        for metric in exporter.metrics
        if metric.name == "platform.scheduler.pressure_admissions"
    ]
