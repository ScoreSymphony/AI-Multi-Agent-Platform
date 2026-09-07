from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed.models import (
    JobRequirements,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.pressure import (
    AdmissionAction,
    AdmissionReasonCode,
    HostPressureSnapshot,
    InMemoryPressureSnapshotProvider,
    PressureAdmissionPolicy,
    PressureKind,
    PressureSignal,
    PressureState,
    ProtectedHeadroom,
)
from ai_multi_agent_platform.distributed.registry import DistributedRegistry
from ai_multi_agent_platform.distributed.scheduler import (
    DeterministicScheduler,
    NoEligibleWorkerError,
)
from ai_multi_agent_platform.domain import new_id

NOW = datetime(2026, 9, 7, 1, 30, tzinfo=UTC)


def _node(*, resources: ResourceSnapshot | None = None) -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name="pressure-node",
        resources=resources
        or ResourceSnapshot(
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
    )


def _job(*, requirements: JobRequirements | None = None) -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id=f"corr:{task_id}", project_id=None),
        ),
        requirements=requirements or JobRequirements(executor_type="reference"),
    )


def _registered() -> tuple[DistributedRegistry, NodeRecord, WorkerRecord]:
    registry = DistributedRegistry()
    node = _node()
    worker = _worker(node)
    registry.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
    return registry, node, worker


def _snapshot(
    state: PressureState, *, age_seconds: int = 0, trusted: bool = True
) -> HostPressureSnapshot:
    return HostPressureSnapshot(
        state=state,
        observed_at=NOW - timedelta(seconds=age_seconds),
        trusted=trusted,
        source_ref="test:pressure",
    )


def test_portable_snapshot_rejects_duplicate_pressure_dimensions() -> None:
    with pytest.raises(ValueError, match="duplicate signal kinds"):
        HostPressureSnapshot(
            state=PressureState.ELEVATED,
            observed_at=NOW,
            signals=(
                PressureSignal(PressureKind.MEMORY, PressureState.ELEVATED, 0.5, "ratio"),
                PressureSignal(PressureKind.MEMORY, PressureState.CRITICAL, 0.9, "ratio"),
            ),
        )


def test_healthy_elevated_critical_and_unknown_decisions_are_deterministic() -> None:
    _registry, node, worker = _registered()
    policy = PressureAdmissionPolicy()
    available = node.resources
    requirements = JobRequirements()

    healthy = policy.decide(
        node=node,
        worker=worker,
        requirements=requirements,
        available=available,
        snapshot=_snapshot(PressureState.HEALTHY),
        now=NOW,
    )
    elevated_light = policy.decide(
        node=node,
        worker=worker,
        requirements=requirements,
        available=available,
        snapshot=_snapshot(PressureState.ELEVATED),
        workload_class="light",
        now=NOW,
    )
    elevated_heavy = policy.decide(
        node=node,
        worker=worker,
        requirements=requirements,
        available=available,
        snapshot=_snapshot(PressureState.ELEVATED),
        workload_class="heavy",
        now=NOW,
    )
    critical = policy.decide(
        node=node,
        worker=worker,
        requirements=requirements,
        available=available,
        snapshot=_snapshot(PressureState.CRITICAL),
        now=NOW,
    )
    unknown = policy.decide(
        node=node,
        worker=worker,
        requirements=requirements,
        available=available,
        snapshot=_snapshot(PressureState.UNKNOWN),
        now=NOW,
    )

    assert healthy.action is AdmissionAction.ADMIT
    assert elevated_light.action is AdmissionAction.ADMIT
    assert elevated_heavy.action is AdmissionAction.QUEUE
    assert critical.action is AdmissionAction.DENY_TEMPORARILY
    assert unknown.action is AdmissionAction.ADMIT
    assert unknown.pressure_state is PressureState.UNKNOWN


def test_missing_stale_and_untrusted_reports_become_unknown_and_can_fail_closed() -> None:
    _registry, node, worker = _registered()
    policy = PressureAdmissionPolicy(
        max_snapshot_age=timedelta(seconds=10),
        require_pressure_report=True,
    )

    missing = policy.decide(
        node=node,
        worker=worker,
        requirements=JobRequirements(),
        available=node.resources,
        snapshot=None,
        now=NOW,
    )
    stale = policy.decide(
        node=node,
        worker=worker,
        requirements=JobRequirements(),
        available=node.resources,
        snapshot=_snapshot(PressureState.HEALTHY, age_seconds=11),
        now=NOW,
    )
    untrusted = policy.decide(
        node=node,
        worker=worker,
        requirements=JobRequirements(),
        available=node.resources,
        snapshot=_snapshot(PressureState.HEALTHY, trusted=False),
        now=NOW,
    )

    assert missing.action is AdmissionAction.DENY_TEMPORARILY
    assert missing.reasons[0].code is AdmissionReasonCode.REPORT_MISSING
    assert stale.action is AdmissionAction.DENY_TEMPORARILY
    assert stale.reasons[0].code is AdmissionReasonCode.REPORT_STALE
    assert stale.snapshot_age_seconds == 11.0
    assert untrusted.action is AdmissionAction.DENY_TEMPORARILY
    assert untrusted.reasons[0].code is AdmissionReasonCode.REPORT_UNTRUSTED


def test_protected_headroom_queues_before_capacity_is_reserved() -> None:
    _registry, node, worker = _registered()
    policy = PressureAdmissionPolicy(
        protected_headroom=ProtectedHeadroom(cpu_cores=2.0, ram_bytes=4_000, storage_bytes=10_000)
    )
    decision = policy.decide(
        node=node,
        worker=worker,
        requirements=JobRequirements(cpu_cores_min=7.0, ram_min_bytes=13_000),
        available=node.resources,
        snapshot=_snapshot(PressureState.HEALTHY),
        now=NOW,
    )

    assert decision.action is AdmissionAction.QUEUE
    assert decision.reasons[0].code is AdmissionReasonCode.PROTECTED_HEADROOM


def test_scheduler_consults_pressure_after_normal_eligibility_before_reservation() -> None:
    registry, node, worker = _registered()
    provider = InMemoryPressureSnapshotProvider()
    provider.put(node.node_id, _snapshot(PressureState.CRITICAL))
    scheduler = DeterministicScheduler(
        registry,
        pressure_provider=provider,
        pressure_policy=PressureAdmissionPolicy(),
    )
    job = _job(
        requirements=JobRequirements(
            executor_type="reference",
            cpu_cores_min=1.0,
            ram_min_bytes=1_000,
        )
    )

    admission = scheduler.pressure_admission(job, worker.worker_id, now=NOW)
    evaluation = scheduler.evaluate_worker(job, worker.worker_id, now=NOW)

    assert admission is not None
    assert admission.action is AdmissionAction.DENY_TEMPORARILY
    assert not evaluation.accepted
    assert evaluation.reasons[-1].message.startswith("pressure admission deny_temporarily")
    with pytest.raises(NoEligibleWorkerError):
        scheduler.schedule(job, now=NOW)
    assert registry.active_reservations() == ()


def test_scheduler_pressure_feature_is_optional_and_unknown_platforms_remain_usable() -> None:
    registry, _node_record, worker = _registered()
    job = _job()

    disabled = DeterministicScheduler(registry).schedule(job, now=NOW)

    assert disabled.reservation.worker_id == worker.worker_id


def test_scheduler_can_queue_configured_heavy_workload_without_second_scheduler() -> None:
    registry, node, worker = _registered()
    provider = InMemoryPressureSnapshotProvider()
    provider.put(node.node_id, _snapshot(PressureState.ELEVATED))
    scheduler = DeterministicScheduler(
        registry,
        pressure_provider=provider,
        pressure_policy=PressureAdmissionPolicy(),
        workload_class_resolver=lambda _job_request: "heavy",
    )
    job = _job()

    decision = scheduler.pressure_admission(job, worker.worker_id, now=NOW)

    assert decision is not None
    assert decision.action is AdmissionAction.QUEUE
    with pytest.raises(NoEligibleWorkerError):
        scheduler.schedule(job, now=NOW)
    assert registry.active_reservations() == ()
