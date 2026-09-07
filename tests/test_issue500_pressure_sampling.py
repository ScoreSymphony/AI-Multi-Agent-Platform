from __future__ import annotations

from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import (
    DeterministicScheduler,
    DistributedRegistry,
    HostPressureSnapshot,
    JobRequirements,
    NodeRecord,
    PressureAdmissionPolicy,
    PressureState,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import new_id

NOW = datetime(2026, 9, 7, 2, 30, tzinfo=UTC)


class CountingPressureProvider:
    def __init__(self, snapshot: HostPressureSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot:
        self.calls.append(node_id)
        return self.snapshot


def test_scheduler_samples_pressure_once_per_node_per_evaluation() -> None:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="shared-pressure-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
    )
    workers = (
        WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id),
        WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id),
    )
    registry = DistributedRegistry()
    registry.register(RegistrationRequest(node=node, workers=workers), now=NOW)
    snapshot = HostPressureSnapshot(state=PressureState.HEALTHY, observed_at=NOW)
    provider = CountingPressureProvider(snapshot)
    scheduler = DeterministicScheduler(
        registry,
        pressure_provider=provider,
        pressure_policy=PressureAdmissionPolicy(),
    )
    task_id = new_id("task")
    job = WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id=task_id),
        ),
        requirements=JobRequirements(),
    )

    first = scheduler.evaluate(job, now=NOW)

    assert len(first.evaluations) == 2
    assert all(item.accepted for item in first.evaluations)
    assert provider.calls == [node.node_id]

    scheduler.evaluate(job, now=NOW)

    assert provider.calls == [node.node_id, node.node_id]
