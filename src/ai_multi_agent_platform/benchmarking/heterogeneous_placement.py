"""Heterogeneous capability/resource placement benchmark evidence for issue #440."""

from __future__ import annotations

import time
import tracemalloc
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import (
    AcceleratorResource,
    DeterministicScheduler,
    DistributedRegistry,
    JobRequirements,
    NoEligibleWorkerError,
    NodeRecord,
    RegistrationRequest,
    RejectionCode,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.models import SchedulingDecision
from ai_multi_agent_platform.domain import new_id

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

HETEROGENEOUS_PLACEMENT_REPORT_SCHEMA_VERSION = "1.0"
HETEROGENEOUS_WORKLOAD_PROFILES = (
    "cpu-only",
    "gpu-inference",
    "browser-network",
    "unschedulable-vram",
)


@dataclass(frozen=True, slots=True)
class HeterogeneousPlacementSpec:
    """One bounded sweep across representative heterogeneous placement constraints."""

    iterations_per_profile: int = 100
    safety_max_operations: int = 10_000
    benchmark_id: str = "distributed.heterogeneous-placement"
    benchmark_version: str = "1.0"
    deployment_profile: str = "distributed-reference-scheduler"

    def __post_init__(self) -> None:
        if self.iterations_per_profile < 1:
            raise ValueError("iterations_per_profile must be at least 1")
        if self.safety_max_operations < 1:
            raise ValueError("safety_max_operations must be at least 1")
        if self.operation_count > self.safety_max_operations:
            raise ValueError("heterogeneous placement workload exceeds operation safety bound")

    @property
    def operation_count(self) -> int:
        return self.iterations_per_profile * len(HETEROGENEOUS_WORKLOAD_PROFILES)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "operation_count": self.operation_count,
            "workload_profiles": list(HETEROGENEOUS_WORKLOAD_PROFILES),
            "expected_invariants": [
                "CPU-only work never lands on accelerator-equipped Nodes",
                "GPU/model work lands only on the accelerator/model-capable Worker",
                "browser/network work lands only on the browser-capable Worker",
                "an impossible VRAM request is rejected without creating a reservation",
                "overlapping logical Worker capabilities make resource/label/network filters decisive",
                "expected decoy rejection reasons are observed for every profile iteration",
                "every successful scheduling decision releases its reservation before the next sample",
            ],
            "captured_metrics": [
                "scheduler placement latency p50/p95/p99 by workload profile",
                "placement counts by heterogeneous Worker role",
                "canonical scheduler rejection-code counts across all rejected candidates",
                "profile/role/constraint rejection evidence counts",
                "process CPU, memory, descriptor and storage evidence",
            ],
        }


@dataclass(frozen=True, slots=True)
class HeterogeneousPlacementCorrectnessSummary:
    expected_operations: int
    attempted_operations: int
    successful_placements: int
    expected_successful_placements: int
    rejected_operations: int
    expected_rejected_operations: int
    misplaced_operations: int
    constraint_evidence_failures: int
    reservation_leaks: int
    passed: bool


@dataclass(frozen=True, slots=True)
class HeterogeneousPlacementReport:
    schema_version: str
    benchmark: HeterogeneousPlacementSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_operations_per_second: float
    placement_latency: LatencyDistribution
    profile_latency: dict[str, LatencyDistribution]
    role_placement_counts: dict[str, int]
    rejection_code_counts: dict[str, int]
    constraint_evidence_counts: dict[str, int]
    worker_roles: dict[str, str]
    resources: ResourceMetrics
    correctness: HeterogeneousPlacementCorrectnessSummary
    node_ids: tuple[str, ...]
    worker_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["benchmark"] = self.benchmark.to_dict()
        return cast(dict[str, Any], _json_compatible(document))


@dataclass(frozen=True, slots=True)
class _WorkerFixture:
    role: str
    node: NodeRecord
    worker: WorkerRecord


@dataclass(frozen=True, slots=True)
class _PlacementWorkload:
    name: str
    requirements: JobRequirements
    expected_role: str | None
    expected_rejections: tuple[tuple[str, tuple[RejectionCode, ...]], ...] = ()


class HeterogeneousPlacementBenchmarkHarness:
    """Measure canonical scheduler behavior across deliberately different Node constraints."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    def run(self, spec: HeterogeneousPlacementSpec) -> HeterogeneousPlacementReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        registry = DistributedRegistry()
        scheduler = DeterministicScheduler(registry)
        fixtures = _build_workers()
        workloads = _workloads()
        for fixture in fixtures:
            registry.register(RegistrationRequest(node=fixture.node, workers=(fixture.worker,)))

        role_by_worker = {fixture.worker.worker_id: fixture.role for fixture in fixtures}
        latency_samples: list[float] = []
        profile_samples: dict[str, list[float]] = defaultdict(list)
        role_placement_counts: Counter[str] = Counter()
        rejection_code_counts: Counter[str] = Counter()
        constraint_evidence_counts: Counter[str] = Counter()
        errors: list[str] = []
        attempted_operations = 0
        successful_placements = 0
        rejected_operations = 0
        misplaced_operations = 0
        constraint_evidence_failures = 0

        storage_before = _directory_size(self._data_dir)
        cpu_before = time.process_time()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        tracemalloc.start()
        try:
            for iteration in range(spec.iterations_per_profile):
                for workload in workloads:
                    attempted_operations += 1
                    job = _job(workload, iteration=iteration)
                    sample_started = time.perf_counter()
                    try:
                        placement = scheduler.schedule(job)
                    except NoEligibleWorkerError:
                        elapsed = time.perf_counter() - sample_started
                        latency_samples.append(elapsed)
                        profile_samples[workload.name].append(elapsed)
                        rejected_operations += 1
                        decision = scheduler.evaluate(job)
                        constraint_evidence_failures += _record_constraint_evidence(
                            decision=decision,
                            workload=workload,
                            role_by_worker=role_by_worker,
                            rejection_code_counts=rejection_code_counts,
                            constraint_evidence_counts=constraint_evidence_counts,
                            errors=errors,
                            iteration=iteration,
                        )
                        if workload.expected_role is not None:
                            errors.append(f"{workload.name} unexpectedly had no eligible Worker")
                        continue

                    elapsed = time.perf_counter() - sample_started
                    latency_samples.append(elapsed)
                    profile_samples[workload.name].append(elapsed)
                    constraint_evidence_failures += _record_constraint_evidence(
                        decision=placement.decision,
                        workload=workload,
                        role_by_worker=role_by_worker,
                        rejection_code_counts=rejection_code_counts,
                        constraint_evidence_counts=constraint_evidence_counts,
                        errors=errors,
                        iteration=iteration,
                    )
                    successful_placements += 1
                    selected_role = role_by_worker[placement.reservation.worker_id]
                    role_placement_counts[selected_role] += 1
                    if workload.expected_role is None:
                        misplaced_operations += 1
                        errors.append(
                            f"{workload.name} unexpectedly placed on role {selected_role}"
                        )
                    elif selected_role != workload.expected_role:
                        misplaced_operations += 1
                        errors.append(
                            f"{workload.name} placed on {selected_role}; "
                            f"expected {workload.expected_role}"
                        )
                    registry.release_reservation(placement.reservation.reservation_id)
        finally:
            duration = max(0.0, time.perf_counter() - started)
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        reservation_leaks = len(registry.active_reservations())
        expected_rejected = spec.iterations_per_profile
        expected_successful = spec.operation_count - expected_rejected
        correctness = HeterogeneousPlacementCorrectnessSummary(
            expected_operations=spec.operation_count,
            attempted_operations=attempted_operations,
            successful_placements=successful_placements,
            expected_successful_placements=expected_successful,
            rejected_operations=rejected_operations,
            expected_rejected_operations=expected_rejected,
            misplaced_operations=misplaced_operations,
            constraint_evidence_failures=constraint_evidence_failures,
            reservation_leaks=reservation_leaks,
            passed=(
                not errors
                and attempted_operations == spec.operation_count
                and successful_placements == expected_successful
                and rejected_operations == expected_rejected
                and misplaced_operations == 0
                and constraint_evidence_failures == 0
                and reservation_leaks == 0
            ),
        )
        if not correctness.passed and not errors:
            errors.append("heterogeneous placement correctness invariants failed")

        storage_after = _directory_size(self._data_dir)
        resources = ResourceMetrics(
            process_cpu_seconds=round(max(0.0, time.process_time() - cpu_before), 6),
            traced_memory_current_bytes=traced_current,
            traced_memory_peak_bytes=traced_peak,
            peak_rss_bytes=_peak_rss_bytes(),
            storage_bytes_before=storage_before,
            storage_bytes_after=storage_after,
            storage_growth_bytes=storage_after - storage_before,
            open_file_descriptors=_open_file_descriptor_count(),
        )
        throughput = spec.operation_count / duration if duration > 0 else 0.0

        return HeterogeneousPlacementReport(
            schema_version=HETEROGENEOUS_PLACEMENT_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at.isoformat(),
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_operations_per_second=round(throughput, 6),
            placement_latency=LatencyDistribution.from_seconds(latency_samples),
            profile_latency={
                name: LatencyDistribution.from_seconds(profile_samples[name])
                for name in HETEROGENEOUS_WORKLOAD_PROFILES
            },
            role_placement_counts=dict(sorted(role_placement_counts.items())),
            rejection_code_counts=dict(sorted(rejection_code_counts.items())),
            constraint_evidence_counts=dict(sorted(constraint_evidence_counts.items())),
            worker_roles=dict(sorted(role_by_worker.items())),
            resources=resources,
            correctness=correctness,
            node_ids=tuple(fixture.node.node_id for fixture in fixtures),
            worker_ids=tuple(fixture.worker.worker_id for fixture in fixtures),
            errors=tuple(errors),
        )


def _record_constraint_evidence(
    *,
    decision: SchedulingDecision,
    workload: _PlacementWorkload,
    role_by_worker: dict[str, str],
    rejection_code_counts: Counter[str],
    constraint_evidence_counts: Counter[str],
    errors: list[str],
    iteration: int,
) -> int:
    expected_by_role = dict(workload.expected_rejections)
    seen_roles: set[str] = set()
    failures = 0
    for evaluation in decision.evaluations:
        role = role_by_worker[evaluation.worker_id]
        seen_roles.add(role)
        observed_codes = {reason.code for reason in evaluation.reasons}
        for reason in evaluation.reasons:
            rejection_code_counts[reason.code.value] += 1
        for expected_code in expected_by_role.get(role, ()):
            evidence_key = f"{workload.name}:{role}:{expected_code.value}"
            if expected_code in observed_codes:
                constraint_evidence_counts[evidence_key] += 1
            else:
                failures += 1
                errors.append(
                    f"{workload.name} iteration {iteration} role {role} missing expected "
                    f"rejection {expected_code.value}"
                )
    for expected_role in expected_by_role:
        if expected_role not in seen_roles:
            failures += 1
            errors.append(
                f"{workload.name} iteration {iteration} missing candidate role {expected_role}"
            )
    return failures


def _build_workers() -> tuple[_WorkerFixture, ...]:
    gib = 1024**3
    shared_executors = ("shell", "model", "browser")
    shared_capabilities = (
        "general.execution",
        "cpu.only",
        "model.inference",
        "browser.automation",
    )
    shared_runtimes = ("python", "local-model", "browser-runtime")
    shared_models = ("benchmark-model",)

    cpu_node = NodeRecord(
        node_id=new_id("node"),
        display_name="Benchmark CPU Node",
        resources=ResourceSnapshot(
            cpu_cores_total=8.0,
            cpu_cores_available=8.0,
            ram_total_bytes=16 * gib,
            ram_available_bytes=16 * gib,
            storage_total_bytes=128 * gib,
            storage_available_bytes=128 * gib,
        ),
        labels=("benchmark", "cpu", "cpu-primary", "browser"),
        network_available=False,
        supported_runtimes=shared_runtimes,
        model_refs=shared_models,
    )
    cpu_worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=cpu_node.node_id,
        supported_executors=shared_executors,
        capability_refs=shared_capabilities,
        supported_runtimes=shared_runtimes,
        model_refs=shared_models,
        concurrency_limit=4,
    )

    gpu_node = NodeRecord(
        node_id=new_id("node"),
        display_name="Benchmark Accelerator Node",
        resources=ResourceSnapshot(
            cpu_cores_total=16.0,
            cpu_cores_available=16.0,
            ram_total_bytes=64 * gib,
            ram_available_bytes=64 * gib,
            storage_total_bytes=256 * gib,
            storage_available_bytes=256 * gib,
            accelerators=(
                AcceleratorResource(
                    accelerator_id="benchmark-accelerator-0",
                    memory_total_bytes=24 * gib,
                    memory_available_bytes=24 * gib,
                ),
            ),
        ),
        labels=("benchmark", "accelerator", "cpu-primary"),
        network_available=True,
        supported_runtimes=shared_runtimes,
        model_refs=shared_models,
    )
    gpu_worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=gpu_node.node_id,
        supported_executors=shared_executors,
        capability_refs=shared_capabilities + ("accelerator",),
        supported_runtimes=shared_runtimes,
        model_refs=shared_models,
        concurrency_limit=2,
    )

    browser_node = NodeRecord(
        node_id=new_id("node"),
        display_name="Benchmark Browser Node",
        resources=ResourceSnapshot(
            cpu_cores_total=4.0,
            cpu_cores_available=4.0,
            ram_total_bytes=8 * gib,
            ram_available_bytes=8 * gib,
            storage_total_bytes=64 * gib,
            storage_available_bytes=64 * gib,
        ),
        labels=("benchmark", "browser"),
        network_available=True,
        supported_runtimes=shared_runtimes,
        model_refs=shared_models,
    )
    browser_worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=browser_node.node_id,
        supported_executors=shared_executors,
        capability_refs=shared_capabilities,
        supported_runtimes=shared_runtimes,
        model_refs=shared_models,
        concurrency_limit=2,
    )

    return (
        _WorkerFixture(role="cpu", node=cpu_node, worker=cpu_worker),
        _WorkerFixture(role="gpu", node=gpu_node, worker=gpu_worker),
        _WorkerFixture(role="browser", node=browser_node, worker=browser_worker),
    )


def _workloads() -> tuple[_PlacementWorkload, ...]:
    gib = 1024**3
    return (
        _PlacementWorkload(
            name="cpu-only",
            requirements=JobRequirements(
                executor_type="shell",
                capability_refs=("general.execution", "cpu.only"),
                cpu_cores_min=1.0,
                ram_min_bytes=512 * 1024**2,
                gpu="forbidden",
                required_labels=("cpu-primary",),
                runtime="python",
            ),
            expected_role="cpu",
            expected_rejections=(
                ("gpu", (RejectionCode.GPU_REQUIRED,)),
                ("browser", (RejectionCode.LABEL_MISMATCH,)),
            ),
        ),
        _PlacementWorkload(
            name="gpu-inference",
            requirements=JobRequirements(
                executor_type="model",
                capability_refs=("model.inference",),
                cpu_cores_min=2.0,
                ram_min_bytes=2 * gib,
                gpu="required",
                vram_min_bytes=4 * gib,
                model_ref="benchmark-model",
                runtime="local-model",
            ),
            expected_role="gpu",
            expected_rejections=(
                ("cpu", (RejectionCode.GPU_REQUIRED,)),
                ("browser", (RejectionCode.GPU_REQUIRED,)),
            ),
        ),
        _PlacementWorkload(
            name="browser-network",
            requirements=JobRequirements(
                executor_type="browser",
                capability_refs=("browser.automation",),
                cpu_cores_min=1.0,
                ram_min_bytes=512 * 1024**2,
                network_required=True,
                required_labels=("browser",),
                runtime="browser-runtime",
            ),
            expected_role="browser",
            expected_rejections=(
                ("cpu", (RejectionCode.NETWORK_UNAVAILABLE,)),
                ("gpu", (RejectionCode.LABEL_MISMATCH,)),
            ),
        ),
        _PlacementWorkload(
            name="unschedulable-vram",
            requirements=JobRequirements(
                executor_type="model",
                capability_refs=("model.inference",),
                gpu="required",
                vram_min_bytes=64 * gib,
                model_ref="benchmark-model",
                runtime="local-model",
            ),
            expected_role=None,
            expected_rejections=(("gpu", (RejectionCode.VRAM_INSUFFICIENT,)),),
        ),
    )


def _job(workload: _PlacementWorkload, *, iteration: int) -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id=f"heterogeneous-placement:{workload.name}:{iteration}"
            ),
            input={"benchmark_profile": workload.name},
        ),
        requirements=workload.requirements,
        idempotency_key=f"heterogeneous-placement:{workload.name}:{iteration}",
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value
