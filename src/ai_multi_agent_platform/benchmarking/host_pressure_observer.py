"""Read-only real-host pressure observation benchmark for issue #440."""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

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
    InMemoryPressureSnapshotProvider,
    PressureAdmissionPolicy,
    PressureSnapshotProvider,
    PressureState,
)
from ai_multi_agent_platform.distributed.registry import DistributedRegistry
from ai_multi_agent_platform.distributed.scheduler import DeterministicScheduler
from ai_multi_agent_platform.domain import new_id

from .single_node import _environment_metadata

HOST_PRESSURE_OBSERVER_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class HostPressureObserverSpec:
    """Bounded read-only observation window for a real or fixture pressure provider."""

    benchmark_id: str
    benchmark_version: str
    duration_seconds: float
    sample_interval_seconds: float
    max_samples: int = 3601
    safety_max_duration_seconds: float = 3600.0
    workload_class: str = "heavy"
    include_provider_metadata: bool = True
    expected_invariants: tuple[str, ...] = (
        "observer-does-not-generate-host-pressure",
        "observer-does-not-mutate-host-configuration",
        "scheduler-evaluation-creates-no-reservation",
        "pressure-samples-retain-portable-state-and-signals",
        "admission-decisions-use-existing-pressure-policy",
    )
    captured_metrics: tuple[str, ...] = (
        "pressure-state-sample-counts",
        "portable-pressure-signal-state-counts",
        "admission-action-and-reason-counts",
        "scheduler-accept-reject-counts",
        "pressure-state-transitions",
        "observed-pressure-to-healthy-recovery-latency",
        "provider-specific-linux-evidence-when-enabled",
    )

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.benchmark_version.strip():
            raise ValueError("benchmark_id and benchmark_version must not be empty")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be positive")
        if self.safety_max_duration_seconds <= 0:
            raise ValueError("safety_max_duration_seconds must be positive")
        if self.duration_seconds > self.safety_max_duration_seconds:
            raise ValueError("duration_seconds exceeds configured safety limit")
        if self.max_samples < 1:
            raise ValueError("max_samples must be positive")
        if not self.workload_class.strip():
            raise ValueError("workload_class must not be blank")
        upper_bound = math.ceil(self.duration_seconds / self.sample_interval_seconds) + 1
        if upper_bound > self.max_samples:
            raise ValueError("observation window exceeds configured max_samples")


@dataclass(frozen=True, slots=True)
class HostPressureObservedSignal:
    kind: str
    state: str
    value: float | None
    unit: str | None


@dataclass(frozen=True, slots=True)
class HostPressureProviderEvidence:
    namespace: str
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class HostPressureObservation:
    sample_index: int
    offset_seconds: float
    observed_at: str | None
    pressure_state: str
    signals: tuple[HostPressureObservedSignal, ...]
    admission_action: str
    admission_reason_codes: tuple[str, ...]
    scheduler_accepted: bool
    provider_metadata: tuple[HostPressureProviderEvidence, ...]


@dataclass(frozen=True, slots=True)
class HostPressureTransition:
    offset_seconds: float
    from_state: str
    to_state: str


@dataclass(frozen=True, slots=True)
class HostPressureObserverSummary:
    sample_count: int
    state_counts: Mapping[str, int]
    admission_action_counts: Mapping[str, int]
    admission_reason_counts: Mapping[str, int]
    scheduler_acceptance_counts: Mapping[str, int]
    signal_state_counts: Mapping[str, int]
    observed_pressure: bool
    recovered_after_pressure: bool | None
    recovery_latency_seconds: float | None


@dataclass(frozen=True, slots=True)
class HostPressureObserverSafety:
    read_only_observer: bool
    host_mutation_attempted: bool
    destructive_load_generated: bool
    duration_seconds: float
    safety_max_duration_seconds: float
    sample_interval_seconds: float
    max_samples: int


@dataclass(frozen=True, slots=True)
class HostPressureObserverCorrectness:
    sample_count: int
    missing_decisions: int
    active_reservations_after: int
    monotonic_sample_offsets: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class HostPressureObserverReport:
    schema_version: str
    benchmark: HostPressureObserverSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    observations: tuple[HostPressureObservation, ...]
    transitions: tuple[HostPressureTransition, ...]
    summary: HostPressureObserverSummary
    safety: HostPressureObserverSafety
    correctness: HostPressureObserverCorrectness
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_compatible(asdict(self)))


class HostPressureObserverHarness:
    """Sample pressure evidence and admission decisions without creating pressure."""

    def __init__(
        self,
        pressure_provider: PressureSnapshotProvider,
        *,
        platform_commit: str = "unknown",
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.pressure_provider = pressure_provider
        self.platform_commit = platform_commit
        self.clock = clock or (lambda: datetime.now(UTC))
        self.monotonic = monotonic
        self.sleeper = sleeper

    def run(self, spec: HostPressureObserverSpec) -> HostPressureObserverReport:
        started = self.clock()
        if started.tzinfo is None or started.utcoffset() is None:
            raise ValueError("observer clock must return timezone-aware datetimes")
        registry, node, worker, job, frozen_provider, scheduler = _fixture(spec.workload_class)
        started_mono = self.monotonic()
        deadline = started_mono + spec.duration_seconds
        next_sample_at = started_mono

        observations: list[HostPressureObservation] = []
        transitions: list[HostPressureTransition] = []
        errors: list[str] = []
        state_counts: Counter[str] = Counter()
        action_counts: Counter[str] = Counter()
        reason_counts: Counter[str] = Counter()
        acceptance_counts: Counter[str] = Counter()
        signal_state_counts: Counter[str] = Counter()
        missing_decisions = 0
        previous_state: str | None = None
        first_pressure_offset: float | None = None
        recovery_latency: float | None = None

        while len(observations) < spec.max_samples:
            now_mono = self.monotonic()
            if observations and now_mono > deadline:
                break
            offset = max(0.0, now_mono - started_mono)
            snapshot = self.pressure_provider.snapshot_for_node(node.node_id)
            if snapshot is None:
                frozen_provider.remove(node.node_id)
                pressure_state = PressureState.UNKNOWN.value
                observed_at = None
                signals: tuple[HostPressureObservedSignal, ...] = ()
                provider_metadata: tuple[HostPressureProviderEvidence, ...] = ()
            else:
                frozen_provider.put(node.node_id, snapshot)
                pressure_state = snapshot.state.value
                observed_at = snapshot.observed_at.isoformat()
                signals = tuple(
                    HostPressureObservedSignal(
                        kind=signal.kind.value,
                        state=signal.state.value,
                        value=signal.value,
                        unit=signal.unit,
                    )
                    for signal in snapshot.signals
                )
                provider_metadata = (
                    tuple(
                        HostPressureProviderEvidence(
                            namespace=metadata.namespace,
                            values=dict(metadata.values),
                        )
                        for metadata in snapshot.provider_metadata
                    )
                    if spec.include_provider_metadata
                    else ()
                )

            decision = scheduler.pressure_admission(job, worker.worker_id, now=self.clock())
            evaluation = scheduler.evaluate_worker(job, worker.worker_id, now=self.clock())
            if decision is None:
                missing_decisions += 1
                errors.append(f"sample {len(observations)}: pressure policy returned no decision")
                admission_action = "missing"
                admission_reason_codes: tuple[str, ...] = ()
            else:
                admission_action = decision.action.value
                admission_reason_codes = tuple(reason.code.value for reason in decision.reasons)
                action_counts[admission_action] += 1
                reason_counts.update(admission_reason_codes)

            state_counts[pressure_state] += 1
            acceptance_counts["accepted" if evaluation.accepted else "rejected"] += 1
            for signal in signals:
                signal_state_counts[f"{signal.kind}:{signal.state}"] += 1

            if previous_state is not None and previous_state != pressure_state:
                transitions.append(
                    HostPressureTransition(
                        offset_seconds=round(offset, 6),
                        from_state=previous_state,
                        to_state=pressure_state,
                    )
                )
            previous_state = pressure_state

            if pressure_state in {PressureState.ELEVATED.value, PressureState.CRITICAL.value}:
                if first_pressure_offset is None:
                    first_pressure_offset = offset
            elif (
                pressure_state == PressureState.HEALTHY.value
                and first_pressure_offset is not None
                and recovery_latency is None
            ):
                recovery_latency = max(0.0, offset - first_pressure_offset)

            observations.append(
                HostPressureObservation(
                    sample_index=len(observations),
                    offset_seconds=round(offset, 6),
                    observed_at=observed_at,
                    pressure_state=pressure_state,
                    signals=signals,
                    admission_action=admission_action,
                    admission_reason_codes=admission_reason_codes,
                    scheduler_accepted=evaluation.accepted,
                    provider_metadata=provider_metadata,
                )
            )

            current_mono = self.monotonic()
            if current_mono >= deadline:
                break
            next_sample_at = min(deadline, next_sample_at + spec.sample_interval_seconds)
            while next_sample_at < current_mono and next_sample_at < deadline:
                next_sample_at = min(deadline, next_sample_at + spec.sample_interval_seconds)
            sleep_seconds = max(0.0, next_sample_at - current_mono)
            if sleep_seconds > 0:
                self.sleeper(sleep_seconds)

        offsets = [sample.offset_seconds for sample in observations]
        monotonic_offsets = all(
            left <= right for left, right in zip(offsets, offsets[1:], strict=False)
        )
        active_reservations_after = len(registry.active_reservations())
        if active_reservations_after:
            errors.append("observer unexpectedly created scheduler reservations")
        if not observations:
            errors.append("observer produced no pressure samples")

        observed_pressure = first_pressure_offset is not None
        recovered = None if not observed_pressure else recovery_latency is not None
        summary = HostPressureObserverSummary(
            sample_count=len(observations),
            state_counts=dict(state_counts),
            admission_action_counts=dict(action_counts),
            admission_reason_counts=dict(reason_counts),
            scheduler_acceptance_counts=dict(acceptance_counts),
            signal_state_counts=dict(signal_state_counts),
            observed_pressure=observed_pressure,
            recovered_after_pressure=recovered,
            recovery_latency_seconds=(
                None if recovery_latency is None else round(recovery_latency, 6)
            ),
        )
        safety = HostPressureObserverSafety(
            read_only_observer=True,
            host_mutation_attempted=False,
            destructive_load_generated=False,
            duration_seconds=spec.duration_seconds,
            safety_max_duration_seconds=spec.safety_max_duration_seconds,
            sample_interval_seconds=spec.sample_interval_seconds,
            max_samples=spec.max_samples,
        )
        correctness = HostPressureObserverCorrectness(
            sample_count=len(observations),
            missing_decisions=missing_decisions,
            active_reservations_after=active_reservations_after,
            monotonic_sample_offsets=monotonic_offsets,
            passed=(
                bool(observations)
                and missing_decisions == 0
                and active_reservations_after == 0
                and monotonic_offsets
                and not errors
            ),
        )
        return HostPressureObserverReport(
            schema_version=HOST_PRESSURE_OBSERVER_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self.platform_commit,
            started_at=started.isoformat(),
            duration_seconds=round(max(0.0, self.monotonic() - started_mono), 6),
            environment=_environment_metadata(),
            observations=tuple(observations),
            transitions=tuple(transitions),
            summary=summary,
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
        display_name="host-pressure-observer-node",
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
    frozen_provider = InMemoryPressureSnapshotProvider()
    scheduler = DeterministicScheduler(
        registry,
        pressure_provider=frozen_provider,
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
    return registry, node, worker, job, frozen_provider, scheduler


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


__all__ = [
    "HOST_PRESSURE_OBSERVER_SCHEMA_VERSION",
    "HostPressureObservation",
    "HostPressureObservedSignal",
    "HostPressureObserverCorrectness",
    "HostPressureObserverHarness",
    "HostPressureObserverReport",
    "HostPressureObserverSafety",
    "HostPressureObserverSpec",
    "HostPressureObserverSummary",
    "HostPressureProviderEvidence",
    "HostPressureTransition",
]
