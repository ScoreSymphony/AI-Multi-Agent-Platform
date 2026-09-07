"""Deterministic Control Plane HA failover performance evidence for issue #440."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.high_availability import (
    AvailabilityMode,
    ControlPlaneFailoverService,
    CoordinationUnavailable,
    DistributedRuntimeFailoverReconciler,
    InMemoryCoordinationProvider,
    StaleFencingToken,
)
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

HA_FAILOVER_REPORT_SCHEMA_VERSION = "1.0"


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


@dataclass(frozen=True, slots=True)
class HAFailoverBenchmarkSpec:
    """One bounded active/passive failover workload over the completed #89 reference path."""

    active_task_count: int = 25
    repetitions: int = 3
    lease_ttl_seconds: float = 5.0
    timeout_seconds: float = 60.0
    safety_max_tasks: int = 1000
    benchmark_id: str = "ha.control-plane-failover"
    benchmark_version: str = "1.0"
    deployment_profile: str = "ha-active-passive-reference"
    coordination_profile: str = "in-memory-process-local"

    def __post_init__(self) -> None:
        if self.active_task_count < 1:
            raise ValueError("active_task_count must be at least 1")
        if self.repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        if self.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.safety_max_tasks < 1:
            raise ValueError("safety_max_tasks must be at least 1")
        if self.total_active_tasks > self.safety_max_tasks:
            raise ValueError("HA failover workload exceeds configured task safety bound")

    @property
    def total_active_tasks(self) -> int:
        return self.active_task_count * self.repetitions

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "total_active_tasks": self.total_active_tasks,
            "expected_invariants": [
                "promotion acquires a strictly newer fencing epoch",
                "the #89 distributed promotion reconciler expires every seeded stale reservation",
                "the old leader is rejected by the canonical fencing contract",
                "replaying identical Task/ready/start commands preserves Task and Run identities",
                "replay does not cause a second lifecycle dispatch",
                "Task history retains exactly one task.created and one run.created event",
                "coordination outage removes write authority fail-closed",
                "authority can be re-established only through a later strictly newer epoch",
            ],
            "captured_metrics": [
                "promotion + distributed reconciliation latency p50/p95/p99",
                "reconciled stale Worker reservation count",
                "stale-leader fencing rejection latency p50/p95/p99",
                "per-Task idempotent recovery replay latency p50/p95/p99",
                "coordination-outage authority rejection latency p50/p95/p99",
                "post-outage recovery promotion latency p50/p95/p99",
                "recovered Task throughput and process resource evidence",
            ],
            "scope_limitations": [
                "uses the process-local deterministic #89 InMemoryCoordinationProvider",
                "does not claim independent-process or cross-host HA compatibility",
                "real production-shaped multi-instance HA remains owned by issue #566",
            ],
        }


@dataclass(frozen=True, slots=True)
class HAFailoverEpochEvidence:
    repetition: int
    initial_epoch: int
    promoted_epoch: int
    recovery_epoch: int


@dataclass(frozen=True, slots=True)
class HAFailoverCorrectnessSummary:
    expected_repetitions: int
    completed_repetitions: int
    expected_tasks: int
    prepared_tasks: int
    recovered_tasks: int
    task_identity_failures: int
    run_identity_failures: int
    duplicate_lifecycle_dispatches: int
    history_failures: int
    reconciled_stale_reservations: int
    reconciliation_failures: int
    stale_leader_rejections: int
    expected_stale_leader_rejections: int
    outage_fail_closed_rejections: int
    expected_outage_fail_closed_rejections: int
    recovery_promotions: int
    expected_recovery_promotions: int
    epoch_monotonicity_failures: int
    passed: bool


@dataclass(frozen=True, slots=True)
class HAFailoverBenchmarkReport:
    schema_version: str
    benchmark: HAFailoverBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_recovered_tasks_per_second: float
    promotion_latency: LatencyDistribution
    stale_leader_rejection_latency: LatencyDistribution
    replay_task_latency: LatencyDistribution
    coordination_outage_rejection_latency: LatencyDistribution
    coordination_recovery_promotion_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: HAFailoverCorrectnessSummary
    epoch_evidence: tuple[HAFailoverEpochEvidence, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["benchmark"] = self.benchmark.to_dict()
        return cast(dict[str, Any], _json_compatible(document))


@dataclass(frozen=True, slots=True)
class _IterationResult:
    prepared_tasks: int
    recovered_tasks: int
    task_identity_failures: int
    run_identity_failures: int
    duplicate_lifecycle_dispatches: int
    history_failures: int
    reconciled_stale_reservations: int
    reconciliation_failures: int
    stale_leader_rejected: bool
    outage_fail_closed: bool
    recovery_promoted: bool
    epoch_monotonicity_failures: int
    promotion_latency: float
    stale_rejection_latency: float
    replay_task_latencies: tuple[float, ...]
    outage_rejection_latency: float
    recovery_promotion_latency: float
    epoch_evidence: HAFailoverEpochEvidence
    errors: tuple[str, ...]


class HAFailoverBenchmarkHarness:
    """Measure deterministic #89 promotion/fencing/replay behavior over durable canonical state."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(self, spec: HAFailoverBenchmarkSpec) -> HAFailoverBenchmarkReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        promotion_samples: list[float] = []
        stale_rejection_samples: list[float] = []
        replay_samples: list[float] = []
        outage_rejection_samples: list[float] = []
        recovery_promotion_samples: list[float] = []
        epoch_evidence: list[HAFailoverEpochEvidence] = []
        errors: list[str] = []

        completed_repetitions = 0
        prepared_tasks = 0
        recovered_tasks = 0
        task_identity_failures = 0
        run_identity_failures = 0
        duplicate_lifecycle_dispatches = 0
        history_failures = 0
        reconciled_stale_reservations = 0
        reconciliation_failures = 0
        stale_leader_rejections = 0
        outage_fail_closed_rejections = 0
        recovery_promotions = 0
        epoch_monotonicity_failures = 0

        storage_before = _directory_size(self._data_dir)
        cpu_before = time.process_time()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        tracemalloc.start()
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                for repetition in range(spec.repetitions):
                    result = await self._run_iteration(spec, repetition)
                    completed_repetitions += 1
                    prepared_tasks += result.prepared_tasks
                    recovered_tasks += result.recovered_tasks
                    task_identity_failures += result.task_identity_failures
                    run_identity_failures += result.run_identity_failures
                    duplicate_lifecycle_dispatches += result.duplicate_lifecycle_dispatches
                    history_failures += result.history_failures
                    reconciled_stale_reservations += result.reconciled_stale_reservations
                    reconciliation_failures += result.reconciliation_failures
                    stale_leader_rejections += int(result.stale_leader_rejected)
                    outage_fail_closed_rejections += int(result.outage_fail_closed)
                    recovery_promotions += int(result.recovery_promoted)
                    epoch_monotonicity_failures += result.epoch_monotonicity_failures
                    promotion_samples.append(result.promotion_latency)
                    stale_rejection_samples.append(result.stale_rejection_latency)
                    replay_samples.extend(result.replay_task_latencies)
                    outage_rejection_samples.append(result.outage_rejection_latency)
                    recovery_promotion_samples.append(result.recovery_promotion_latency)
                    epoch_evidence.append(result.epoch_evidence)
                    errors.extend(result.errors)
        except TimeoutError:
            errors.append(f"HA failover benchmark timed out after {spec.timeout_seconds:g} seconds")
        finally:
            duration = max(0.0, time.perf_counter() - started)
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        expected_rejections = spec.repetitions
        correctness = HAFailoverCorrectnessSummary(
            expected_repetitions=spec.repetitions,
            completed_repetitions=completed_repetitions,
            expected_tasks=spec.total_active_tasks,
            prepared_tasks=prepared_tasks,
            recovered_tasks=recovered_tasks,
            task_identity_failures=task_identity_failures,
            run_identity_failures=run_identity_failures,
            duplicate_lifecycle_dispatches=duplicate_lifecycle_dispatches,
            history_failures=history_failures,
            reconciled_stale_reservations=reconciled_stale_reservations,
            reconciliation_failures=reconciliation_failures,
            stale_leader_rejections=stale_leader_rejections,
            expected_stale_leader_rejections=expected_rejections,
            outage_fail_closed_rejections=outage_fail_closed_rejections,
            expected_outage_fail_closed_rejections=expected_rejections,
            recovery_promotions=recovery_promotions,
            expected_recovery_promotions=spec.repetitions,
            epoch_monotonicity_failures=epoch_monotonicity_failures,
            passed=(
                not errors
                and completed_repetitions == spec.repetitions
                and prepared_tasks == spec.total_active_tasks
                and recovered_tasks == spec.total_active_tasks
                and task_identity_failures == 0
                and run_identity_failures == 0
                and duplicate_lifecycle_dispatches == 0
                and history_failures == 0
                and reconciled_stale_reservations == spec.total_active_tasks
                and reconciliation_failures == 0
                and stale_leader_rejections == expected_rejections
                and outage_fail_closed_rejections == expected_rejections
                and recovery_promotions == spec.repetitions
                and epoch_monotonicity_failures == 0
            ),
        )
        if not correctness.passed and not errors:
            errors.append("HA failover correctness invariants failed")

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
        throughput = recovered_tasks / duration if duration > 0 else 0.0

        return HAFailoverBenchmarkReport(
            schema_version=HA_FAILOVER_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at.isoformat(),
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_recovered_tasks_per_second=round(throughput, 6),
            promotion_latency=LatencyDistribution.from_seconds(promotion_samples),
            stale_leader_rejection_latency=LatencyDistribution.from_seconds(
                stale_rejection_samples
            ),
            replay_task_latency=LatencyDistribution.from_seconds(replay_samples),
            coordination_outage_rejection_latency=LatencyDistribution.from_seconds(
                outage_rejection_samples
            ),
            coordination_recovery_promotion_latency=LatencyDistribution.from_seconds(
                recovery_promotion_samples
            ),
            resources=resources,
            correctness=correctness,
            epoch_evidence=tuple(epoch_evidence),
            errors=tuple(errors),
        )

    async def _run_iteration(
        self,
        spec: HAFailoverBenchmarkSpec,
        repetition: int,
    ) -> _IterationResult:
        iteration_dir = self._data_dir / f"iteration-{repetition:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=False)
        database = iteration_dir / "kernel.sqlite3"

        base_time = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=repetition)
        clock = _MutableClock(base_time)
        coordinator = InMemoryCoordinationProvider(clock=clock)
        ttl = timedelta(seconds=spec.lease_ttl_seconds)
        registry = DistributedRegistry(
            heartbeat_timeout=ttl * 10,
            reservation_ttl=ttl,
        )
        runtime = DistributedRuntime(registry)
        node, worker = _reconciliation_worker(spec.active_task_count)
        registry.register(RegistrationRequest(node=node, workers=(worker,)), now=base_time)
        reconciler = DistributedRuntimeFailoverReconciler(
            runtime,
            coordinator,
            clock=clock,
        )
        first = _service(f"control-a-{repetition}", coordinator, ttl)
        second = _service(
            f"control-b-{repetition}",
            coordinator,
            ttl,
            reconciler=reconciler,
        )
        recovery = _service(
            f"control-c-{repetition}",
            coordinator,
            ttl,
            reconciler=reconciler,
        )
        errors: list[str] = []

        if not await first.start(reason="benchmark-initial"):
            errors.append("initial Control Plane did not acquire leadership")
        if await second.start(reason="benchmark-standby"):
            errors.append("standby unexpectedly acquired leadership before failover")
        initial_grant = await first.require_authority()
        if initial_grant.fencing_token is None:
            raise RuntimeError("HA benchmark initial authority did not include a fencing token")
        initial_epoch = initial_grant.fencing_token.epoch

        lifecycle = FakeLifecycleBackend()
        kernel = _kernel(database, lifecycle)
        task_run_pairs: list[tuple[str, str]] = []
        for index in range(spec.active_task_count):
            task = await kernel.create_task(
                idempotency_key=_idempotency_key(repetition, index, "create"),
                title=_task_title(repetition, index),
                objective="Measure deterministic active/passive failover recovery",
                owner_type="user",
                owner_id="benchmark",
            )
            await kernel.ready_task(
                idempotency_key=_idempotency_key(repetition, index, "ready"),
                task_id=task.task_id,
            )
            run = await kernel.start_task(
                idempotency_key=_idempotency_key(repetition, index, "start"),
                task_id=task.task_id,
            )
            registry.reserve(
                worker_job_id=new_id("worker_job"),
                worker_id=worker.worker_id,
                requirements=JobRequirements(cpu_cores_min=1.0, ram_min_bytes=1),
                now=base_time,
            )
            task_run_pairs.append((task.task_id, run.run_id))

        duplicate_lifecycle_dispatches = max(0, len(lifecycle.start_calls) - spec.active_task_count)
        pre_replay_start_calls = len(lifecycle.start_calls)
        if len(lifecycle.start_calls) != spec.active_task_count:
            errors.append("pre-failover lifecycle dispatch count did not match prepared Tasks")

        clock.advance(ttl + timedelta(milliseconds=1))
        promotion_started = time.perf_counter()
        promoted = await second.try_promote(reason="benchmark-leader-loss")
        promotion_latency = time.perf_counter() - promotion_started
        if not promoted:
            errors.append("standby did not promote after the initial lease expired")
        promoted_grant = await second.require_authority()
        if promoted_grant.fencing_token is None:
            raise RuntimeError("promoted HA authority did not include a fencing token")
        promoted_epoch = promoted_grant.fencing_token.epoch

        promotion_status = await second.status()
        reconciliation = promotion_status.last_reconciliation
        reconciled_stale_reservations = (
            0 if reconciliation is None else reconciliation.rejected_stale_items
        )
        reconciliation_failures = 0
        if registry.active_reservations():
            reconciliation_failures += 1
            errors.append("promotion reconciliation left stale Worker reservations active")
        if reconciled_stale_reservations != spec.active_task_count:
            reconciliation_failures += 1
            errors.append("promotion reconciliation did not report every seeded stale reservation")

        stale_rejection_started = time.perf_counter()
        stale_leader_rejected = False
        try:
            await first.require_authority()
        except StaleFencingToken:
            stale_leader_rejected = True
        stale_rejection_latency = time.perf_counter() - stale_rejection_started
        if not stale_leader_rejected:
            errors.append("old leader was not rejected with a stale fencing token")

        promoted_kernel = _kernel(database, lifecycle)
        recovered_tasks = 0
        task_identity_failures = 0
        run_identity_failures = 0
        history_failures = 0
        replay_task_latencies: list[float] = []

        for index, (task_id, run_id) in enumerate(task_run_pairs):
            replay_started = time.perf_counter()
            same_task = await promoted_kernel.create_task(
                idempotency_key=_idempotency_key(repetition, index, "create"),
                title=_task_title(repetition, index),
                objective="Measure deterministic active/passive failover recovery",
                owner_type="user",
                owner_id="benchmark",
            )
            await promoted_kernel.ready_task(
                idempotency_key=_idempotency_key(repetition, index, "ready"),
                task_id=task_id,
            )
            same_run = await promoted_kernel.start_task(
                idempotency_key=_idempotency_key(repetition, index, "start"),
                task_id=task_id,
            )
            replay_task_latencies.append(time.perf_counter() - replay_started)

            identity_ok = True
            if same_task.task_id != task_id:
                task_identity_failures += 1
                identity_ok = False
            if same_run.run_id != run_id:
                run_identity_failures += 1
                identity_ok = False

            event_types = [event.event_type for event in await promoted_kernel.history(task_id)]
            history_ok = (
                event_types.count("task.created") == 1 and event_types.count("run.created") == 1
            )
            if not history_ok:
                history_failures += 1
            if identity_ok and history_ok:
                recovered_tasks += 1

        replay_dispatches = max(0, len(lifecycle.start_calls) - pre_replay_start_calls)
        duplicate_lifecycle_dispatches += replay_dispatches
        if replay_dispatches:
            errors.append("idempotent recovery replay caused duplicate lifecycle dispatch")

        coordinator.set_available(False)
        outage_started = time.perf_counter()
        outage_fail_closed = False
        try:
            await second.require_authority()
        except CoordinationUnavailable:
            outage_fail_closed = True
        outage_rejection_latency = time.perf_counter() - outage_started
        if not outage_fail_closed:
            errors.append("coordination outage did not remove active write authority")

        coordinator.set_available(True)
        clock.advance(ttl + timedelta(milliseconds=1))
        recovery_started = time.perf_counter()
        recovery_promoted = await recovery.try_promote(reason="benchmark-coordination-recovery")
        recovery_promotion_latency = time.perf_counter() - recovery_started
        if not recovery_promoted:
            errors.append("Control Plane did not recover authority after coordination restoration")
        recovery_grant = await recovery.require_authority()
        if recovery_grant.fencing_token is None:
            raise RuntimeError("recovered HA authority did not include a fencing token")
        recovery_epoch = recovery_grant.fencing_token.epoch

        epoch_monotonicity_failures = 0
        if not initial_epoch < promoted_epoch < recovery_epoch:
            epoch_monotonicity_failures = 1
            errors.append("HA fencing epochs did not increase monotonically across recovery")

        return _IterationResult(
            prepared_tasks=len(task_run_pairs),
            recovered_tasks=recovered_tasks,
            task_identity_failures=task_identity_failures,
            run_identity_failures=run_identity_failures,
            duplicate_lifecycle_dispatches=duplicate_lifecycle_dispatches,
            history_failures=history_failures,
            reconciled_stale_reservations=reconciled_stale_reservations,
            reconciliation_failures=reconciliation_failures,
            stale_leader_rejected=stale_leader_rejected,
            outage_fail_closed=outage_fail_closed,
            recovery_promoted=recovery_promoted,
            epoch_monotonicity_failures=epoch_monotonicity_failures,
            promotion_latency=promotion_latency,
            stale_rejection_latency=stale_rejection_latency,
            replay_task_latencies=tuple(replay_task_latencies),
            outage_rejection_latency=outage_rejection_latency,
            recovery_promotion_latency=recovery_promotion_latency,
            epoch_evidence=HAFailoverEpochEvidence(
                repetition=repetition,
                initial_epoch=initial_epoch,
                promoted_epoch=promoted_epoch,
                recovery_epoch=recovery_epoch,
            ),
            errors=tuple(errors),
        )


def _service(
    instance_id: str,
    coordinator: InMemoryCoordinationProvider,
    ttl: timedelta,
    *,
    reconciler: DistributedRuntimeFailoverReconciler | None = None,
) -> ControlPlaneFailoverService:
    return ControlPlaneFailoverService(
        instance_id=instance_id,
        mode=AvailabilityMode.ACTIVE_PASSIVE,
        coordinator=coordinator,
        lease_ttl=ttl,
        reconciler=reconciler,
    )


def _reconciliation_worker(active_task_count: int) -> tuple[NodeRecord, WorkerRecord]:
    capacity = float(active_task_count + 1)
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="HA benchmark reconciliation node",
        resources=ResourceSnapshot(
            cpu_cores_total=capacity,
            cpu_cores_available=capacity,
            ram_total_bytes=active_task_count + 1024,
            ram_available_bytes=active_task_count + 1024,
            storage_total_bytes=1024 * 1024,
            storage_available_bytes=1024 * 1024,
        ),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        concurrency_limit=active_task_count,
    )
    return node, worker


def _kernel(database: Path, lifecycle: FakeLifecycleBackend) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        repository=SqliteKernelRepository(database),
    )


def _idempotency_key(repetition: int, index: int, phase: str) -> str:
    return f"ha-benchmark:{repetition}:{index}:{phase}"


def _task_title(repetition: int, index: int) -> str:
    return f"HA benchmark task {repetition}-{index}"


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value
