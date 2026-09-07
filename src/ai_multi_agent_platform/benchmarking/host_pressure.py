"""Deterministic host-pressure admission benchmark fixture for issues #500 and #440."""

from __future__ import annotations

import time
import tracemalloc
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from ai_multi_agent_platform import __version__
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
    HostPressureSnapshot,
    InMemoryPressureSnapshotProvider,
    PressureAdmissionPolicy,
    PressureKind,
    PressureSignal,
    PressureState,
)
from ai_multi_agent_platform.distributed.registry import DistributedRegistry
from ai_multi_agent_platform.distributed.scheduler import DeterministicScheduler
from ai_multi_agent_platform.domain import new_id

from .models import LatencyDistribution
from .single_node import _environment_metadata, _open_file_descriptor_count, _peak_rss_bytes

HOST_PRESSURE_REPORT_SCHEMA_VERSION = "1.0"

_PHASES = ("healthy", "elevated", "critical", "recovery")
_EXPECTED_ACTION = {
    "healthy": AdmissionAction.ADMIT,
    "elevated": AdmissionAction.QUEUE,
    "critical": AdmissionAction.DENY_TEMPORARILY,
    "recovery": AdmissionAction.ADMIT,
}


@dataclass(frozen=True, slots=True)
class HostPressureBenchmarkSpec:
    """One tiny synthetic pressure/admission run with hard harness bounds."""

    benchmark_id: str
    benchmark_version: str
    iterations_per_phase: int
    safety_max_iterations_per_phase: int = 1000
    workload_class: str = "heavy"
    expected_invariants: tuple[str, ...] = (
        "healthy-pressure-admits-heavy-work",
        "elevated-pressure-queues-heavy-work",
        "critical-pressure-temporarily-denies-heavy-work",
        "healthy-recovery-restores-admission",
        "pressure-evaluation-creates-no-reservation",
        "fixture-does-not-generate-host-resource-pressure",
    )
    captured_metrics: tuple[str, ...] = (
        "phase-admission-latency-p50-p95-p99",
        "phase-admission-action-counts",
        "phase-pressure-state-counts",
        "phase-structured-reason-counts",
        "phase-scheduler-accept-reject-counts",
        "process-cpu-memory-open-files",
        "recovery-and-reservation-correctness",
    )

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.benchmark_version.strip():
            raise ValueError("benchmark_id and benchmark_version must not be empty")
        if self.iterations_per_phase < 1:
            raise ValueError("iterations_per_phase must be positive")
        if self.safety_max_iterations_per_phase < 1:
            raise ValueError("safety_max_iterations_per_phase must be positive")
        if self.iterations_per_phase > self.safety_max_iterations_per_phase:
            raise ValueError("iterations_per_phase exceeds configured safety limit")
        if self.workload_class != "heavy":
            raise ValueError("semantic host-pressure fixture requires workload_class='heavy'")


@dataclass(frozen=True, slots=True)
class HostPressureBenchmarkResources:
    process_cpu_seconds: float
    traced_memory_current_bytes: int
    traced_memory_peak_bytes: int
    peak_rss_bytes: int | None
    open_file_descriptors: int | None


@dataclass(frozen=True, slots=True)
class HostPressureBenchmarkSafety:
    synthetic_fixture: bool
    host_mutation_attempted: bool
    destructive_load_generated: bool
    iterations_per_phase: int
    safety_max_iterations_per_phase: int


@dataclass(frozen=True, slots=True)
class HostPressureBenchmarkCorrectness:
    attempted_decisions: int
    matching_decisions: int
    unexpected_decisions: int
    scheduler_acceptance_mismatches: int
    active_reservations_after: int
    recovered: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class HostPressureBenchmarkReport:
    schema_version: str
    benchmark: HostPressureBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    phase_latency: Mapping[str, LatencyDistribution]
    phase_action_counts: Mapping[str, Mapping[str, int]]
    phase_pressure_state_counts: Mapping[str, Mapping[str, int]]
    phase_reason_counts: Mapping[str, Mapping[str, int]]
    phase_scheduler_acceptance_counts: Mapping[str, Mapping[str, int]]
    resources: HostPressureBenchmarkResources
    safety: HostPressureBenchmarkSafety
    correctness: HostPressureBenchmarkCorrectness
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        benchmark = payload["benchmark"]
        if isinstance(benchmark, dict):
            benchmark["expected_invariants"] = list(benchmark["expected_invariants"])
            benchmark["captured_metrics"] = list(benchmark["captured_metrics"])
        payload["errors"] = list(self.errors)
        return payload


class HostPressureSemanticBenchmarkHarness:
    """Exercise the canonical scheduler pressure hook without generating host pressure."""

    def __init__(self, *, platform_commit: str = "unknown") -> None:
        self.platform_commit = platform_commit

    def run(self, spec: HostPressureBenchmarkSpec) -> HostPressureBenchmarkReport:
        started = datetime.now(UTC)
        wall_started = time.perf_counter()
        cpu_started = time.process_time()
        registry, node, worker, job, provider, scheduler = _fixture(spec.workload_class)

        phase_latency: dict[str, LatencyDistribution] = {}
        phase_action_counts: dict[str, dict[str, int]] = {}
        phase_pressure_state_counts: dict[str, dict[str, int]] = {}
        phase_reason_counts: dict[str, dict[str, int]] = {}
        phase_scheduler_acceptance_counts: dict[str, dict[str, int]] = {}
        matching_decisions = 0
        scheduler_acceptance_mismatches = 0
        errors: list[str] = []

        tracemalloc.start()
        try:
            for phase in _PHASES:
                observed_at = datetime.now(UTC)
                provider.put(node.node_id, _phase_snapshot(phase, observed_at))
                action_counts: Counter[str] = Counter()
                state_counts: Counter[str] = Counter()
                reason_counts: Counter[str] = Counter()
                acceptance_counts: Counter[str] = Counter()
                latencies: list[float] = []

                for _ in range(spec.iterations_per_phase):
                    operation_started = time.perf_counter()
                    decision = scheduler.pressure_admission(
                        job,
                        worker.worker_id,
                        now=observed_at,
                    )
                    evaluation = scheduler.evaluate_worker(
                        job,
                        worker.worker_id,
                        now=observed_at,
                    )
                    latencies.append(time.perf_counter() - operation_started)

                    if decision is None:
                        errors.append(f"{phase}: pressure policy returned no decision")
                        continue
                    action_counts[decision.action.value] += 1
                    state_counts[decision.pressure_state.value] += 1
                    for reason in decision.reasons:
                        reason_counts[reason.code.value] += 1
                    acceptance_counts["accepted" if evaluation.accepted else "rejected"] += 1

                expected_action = _EXPECTED_ACTION[phase]
                matching = action_counts[expected_action.value]
                matching_decisions += matching
                expected_acceptance = (
                    acceptance_counts["accepted"]
                    if expected_action is AdmissionAction.ADMIT
                    else acceptance_counts["rejected"]
                )
                scheduler_acceptance_mismatches += spec.iterations_per_phase - expected_acceptance
                if matching != spec.iterations_per_phase:
                    errors.append(
                        f"{phase}: expected {spec.iterations_per_phase} "
                        f"{expected_action.value} decisions, got {matching}"
                    )
                if expected_acceptance != spec.iterations_per_phase:
                    expected_label = (
                        "accepted" if expected_action is AdmissionAction.ADMIT else "rejected"
                    )
                    errors.append(
                        f"{phase}: expected {spec.iterations_per_phase} scheduler "
                        f"{expected_label} evaluations, got {expected_acceptance}"
                    )

                phase_latency[phase] = LatencyDistribution.from_seconds(latencies)
                phase_action_counts[phase] = dict(action_counts)
                phase_pressure_state_counts[phase] = dict(state_counts)
                phase_reason_counts[phase] = dict(reason_counts)
                phase_scheduler_acceptance_counts[phase] = dict(acceptance_counts)

            traced_current, traced_peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        attempted = spec.iterations_per_phase * len(_PHASES)
        unexpected = attempted - matching_decisions
        active_reservations_after = len(registry.active_reservations())
        recovered = (
            phase_action_counts["recovery"].get(AdmissionAction.ADMIT.value, 0)
            == spec.iterations_per_phase
            and phase_scheduler_acceptance_counts["recovery"].get("accepted", 0)
            == spec.iterations_per_phase
        )
        if active_reservations_after != 0:
            errors.append("pressure benchmark unexpectedly created scheduler reservations")

        correctness = HostPressureBenchmarkCorrectness(
            attempted_decisions=attempted,
            matching_decisions=matching_decisions,
            unexpected_decisions=unexpected,
            scheduler_acceptance_mismatches=scheduler_acceptance_mismatches,
            active_reservations_after=active_reservations_after,
            recovered=recovered,
            passed=(
                unexpected == 0
                and scheduler_acceptance_mismatches == 0
                and active_reservations_after == 0
                and recovered
                and not errors
            ),
        )
        resources = HostPressureBenchmarkResources(
            process_cpu_seconds=round(max(0.0, time.process_time() - cpu_started), 6),
            traced_memory_current_bytes=traced_current,
            traced_memory_peak_bytes=traced_peak,
            peak_rss_bytes=_peak_rss_bytes(),
            open_file_descriptors=_open_file_descriptor_count(),
        )
        safety = HostPressureBenchmarkSafety(
            synthetic_fixture=True,
            host_mutation_attempted=False,
            destructive_load_generated=False,
            iterations_per_phase=spec.iterations_per_phase,
            safety_max_iterations_per_phase=spec.safety_max_iterations_per_phase,
        )
        return HostPressureBenchmarkReport(
            schema_version=HOST_PRESSURE_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self.platform_commit,
            started_at=started.isoformat(),
            duration_seconds=round(max(0.0, time.perf_counter() - wall_started), 6),
            environment=_environment_metadata(),
            phase_latency=phase_latency,
            phase_action_counts=phase_action_counts,
            phase_pressure_state_counts=phase_pressure_state_counts,
            phase_reason_counts=phase_reason_counts,
            phase_scheduler_acceptance_counts=phase_scheduler_acceptance_counts,
            resources=resources,
            safety=safety,
            correctness=correctness,
            errors=tuple(errors),
        )


def _fixture(
    workload_class: str,
) -> tuple[
    DistributedRegistry,
    NodeRecord,
    WorkerRecord,
    WorkerJobRequest,
    InMemoryPressureSnapshotProvider,
    DeterministicScheduler,
]:
    registry = DistributedRegistry()
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="host-pressure-benchmark-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8.0,
            cpu_cores_available=8.0,
            ram_total_bytes=16_000_000_000,
            ram_available_bytes=16_000_000_000,
            storage_total_bytes=100_000_000_000,
            storage_available_bytes=100_000_000_000,
        ),
        supported_runtimes=("python",),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
    )
    registry.register(RegistrationRequest(node=node, workers=(worker,)))
    provider = InMemoryPressureSnapshotProvider()
    scheduler = DeterministicScheduler(
        registry,
        pressure_provider=provider,
        pressure_policy=PressureAdmissionPolicy(),
        workload_class_resolver=lambda _job: workload_class,
    )
    task_id = new_id("task")
    job = WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id=f"corr:{task_id}", project_id=None),
        ),
        requirements=JobRequirements(executor_type="reference"),
    )
    return registry, node, worker, job, provider, scheduler


def _phase_snapshot(phase: str, observed_at: datetime) -> HostPressureSnapshot:
    if phase == "elevated":
        state = PressureState.ELEVATED
        signals = (PressureSignal(PressureKind.MEMORY, PressureState.ELEVATED, 0.75, "ratio"),)
    elif phase == "critical":
        state = PressureState.CRITICAL
        signals = (
            PressureSignal(
                PressureKind.PAGING,
                PressureState.CRITICAL,
                200.0,
                "events_per_second",
            ),
        )
    else:
        state = PressureState.HEALTHY
        signals = (PressureSignal(PressureKind.MEMORY, PressureState.HEALTHY, 0.25, "ratio"),)
    return HostPressureSnapshot(
        state=state,
        observed_at=observed_at,
        signals=signals,
        source_ref="benchmark:synthetic-host-pressure",
        trusted=True,
    )


__all__ = [
    "HOST_PRESSURE_REPORT_SCHEMA_VERSION",
    "HostPressureBenchmarkCorrectness",
    "HostPressureBenchmarkReport",
    "HostPressureBenchmarkResources",
    "HostPressureBenchmarkSafety",
    "HostPressureBenchmarkSpec",
    "HostPressureSemanticBenchmarkHarness",
]
