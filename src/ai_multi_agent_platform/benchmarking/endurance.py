"""Idle-footprint and bounded soak/endurance benchmarks for issue #440."""

from __future__ import annotations

import asyncio
import secrets
import time
import tracemalloc
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import RunStatus, TaskStatus

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
    _require_id,
    _require_mapping,
    _require_status,
)

ENDURANCE_REPORT_SCHEMA_VERSION = "1.0"
_ENDURANCE_ADMIN = "benchmark-endurance-admin"
_ENDURANCE_PROJECT_KEY = "performance-endurance-project-v1"
_SUPPORTED_SCENARIOS = frozenset({"idle", "soak"})


@dataclass(frozen=True, slots=True)
class EnduranceBenchmarkSpec:
    """Versioned specification for idle-footprint and bounded endurance evidence."""

    benchmark_id: str
    benchmark_version: str
    scenario: str
    deployment_profile: str
    persistence_profile: str
    duration_seconds: float
    sample_interval_seconds: float
    max_operations: int
    concurrency: int
    seed_tasks: int
    warmup_operations: int
    timeout_seconds: float
    read_weight: int = 4
    write_weight: int = 1
    repetition_count: int = 1
    optional_subsystems: tuple[str, ...] = ()
    expected_invariants: tuple[str, ...] = (
        "single-deployment-measurement-window",
        "canonical-state-correctness",
        "bounded-operation-count",
        "no-duplicate-write-identities",
    )
    captured_metrics: tuple[str, ...] = (
        "startup-latency",
        "throughput",
        "operation-read-write-latency-p50-p95-p99",
        "periodic-memory-rss-storage-fd-snapshots",
        "memory-and-descriptor-growth",
        "latency-drift",
        "correctness",
    )

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.benchmark_version.strip():
            raise ValueError("benchmark_id and benchmark_version must not be empty")
        if self.scenario not in _SUPPORTED_SCENARIOS:
            raise ValueError(f"unsupported endurance scenario: {self.scenario}")
        if self.deployment_profile != "single-node-reference":
            raise ValueError("endurance benchmark requires single-node-reference deployment")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.sample_interval_seconds <= 0:
            raise ValueError("sample_interval_seconds must be positive")
        if self.max_operations < 0:
            raise ValueError("max_operations must not be negative")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.seed_tasks < 0:
            raise ValueError("seed_tasks must not be negative")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.repetition_count != 1:
            raise ValueError("one endurance report represents exactly one repetition")
        if self.read_weight < 0 or self.write_weight < 0:
            raise ValueError("read/write weights must not be negative")
        if self.scenario == "idle":
            if self.max_operations != 0 or self.seed_tasks != 0 or self.warmup_operations != 0:
                raise ValueError("idle benchmark cannot seed, warm up or execute workload operations")
            if self.read_weight != 0 or self.write_weight != 0:
                raise ValueError("idle benchmark must use zero read/write weights")
        else:
            if self.max_operations < 1:
                raise ValueError("soak benchmark requires max_operations >= 1")
            if self.seed_tasks < 1:
                raise ValueError("soak benchmark requires seed_tasks >= 1")
            if self.read_weight + self.write_weight < 1:
                raise ValueError("soak benchmark requires a non-empty read/write mix")


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """One periodic resource and latency sample from a single deployment process."""

    elapsed_seconds: float
    process_cpu_seconds: float
    traced_memory_current_bytes: int
    traced_memory_peak_bytes: int
    peak_rss_bytes: int | None
    storage_bytes: int
    open_file_descriptors: int | None
    attempted_operations: int
    completed_operations: int
    failed_operations: int
    read_operations: int
    write_operations: int
    window_operation_latency: LatencyDistribution


@dataclass(frozen=True, slots=True)
class EnduranceCorrectnessSummary:
    attempted_operations: int
    completed_operations: int
    failed_operations: int
    seeded_tasks: int
    observed_tasks: int
    observed_runs: int
    duplicate_write_task_ids: int
    duplicate_write_run_ids: int
    passed: bool


@dataclass(frozen=True, slots=True)
class EnduranceBenchmarkReport:
    """Machine-readable idle/soak evidence with periodic drift samples."""

    schema_version: str
    benchmark: EnduranceBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    startup_latency: LatencyDistribution
    throughput_operations_per_second: float
    operation_latency: LatencyDistribution
    read_latency: LatencyDistribution
    write_latency: LatencyDistribution
    snapshots: tuple[ResourceSnapshot, ...]
    resources: ResourceMetrics
    correctness: EnduranceCorrectnessSummary
    measurements: Mapping[str, int | float | str | None]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["snapshots"] = [asdict(snapshot) for snapshot in self.snapshots]
        payload["errors"] = list(self.errors)
        benchmark = payload["benchmark"]
        if isinstance(benchmark, dict):
            for key in ("optional_subsystems", "expected_invariants", "captured_metrics"):
                benchmark[key] = list(benchmark[key])
        return payload


@dataclass(slots=True)
class _Samples:
    attempted: int = 0
    completed: int = 0
    failed: int = 0
    read_operations: int = 0
    write_operations: int = 0
    operation: list[float] = field(default_factory=list)
    read: list[float] = field(default_factory=list)
    write: list[float] = field(default_factory=list)
    window_operation: list[float] = field(default_factory=list)
    write_task_ids: list[str] = field(default_factory=list)
    write_run_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _SeedState:
    task_ids: tuple[str, ...]
    run_ids: tuple[str, ...]


class SingleNodeEnduranceHarness:
    """Measure idle footprint or bounded long-running stability on one deployment."""

    def __init__(self, config: SingleNodeConfig, *, platform_commit: str = "unknown") -> None:
        self._config = config
        self._platform_commit = platform_commit

    async def run(self, spec: EnduranceBenchmarkSpec) -> EnduranceBenchmarkReport:
        _require_fresh_data_root(self._config.data_dir)
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()

        started_at = datetime.now(UTC).isoformat()
        startup_started = time.perf_counter()
        deployment = build_single_node_deployment(self._config)
        startup_elapsed = time.perf_counter() - startup_started
        admin = deployment.bootstrap_admin(_ENDURANCE_ADMIN, secrets.token_urlsafe(32))
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose=f"performance-endurance-{spec.scenario}",
        )
        headers = {
            "authorization": f"Bearer {credential.secret}",
            "content-type": "application/json",
        }
        project = deployment.scopes.create_project(
            key=_ENDURANCE_PROJECT_KEY,
            name="Performance endurance benchmark",
            owner_type="user",
            owner_id=admin.user_id,
        )

        seed = _SeedState((), ())
        if spec.scenario == "soak":
            seed = await self._seed_completed_tasks(
                deployment=deployment,
                headers=headers,
                owner_id=admin.user_id,
                project_id=project.id,
                count=spec.seed_tasks,
                timeout_seconds=spec.timeout_seconds,
            )
            for index in range(spec.warmup_operations):
                await asyncio.wait_for(
                    self._read_operation(
                        deployment=deployment,
                        headers=headers,
                        seed=seed,
                        index=index,
                    ),
                    timeout=spec.timeout_seconds,
                )

        samples = _Samples()
        storage_before = _directory_size(self._config.data_dir)
        cpu_before = time.process_time()
        measurement_started = time.perf_counter()
        snapshots: list[ResourceSnapshot] = []
        self._append_snapshot(
            snapshots,
            samples,
            measurement_started=measurement_started,
            cpu_before=cpu_before,
        )

        if spec.scenario == "idle":
            await self._run_idle_window(
                spec=spec,
                snapshots=snapshots,
                samples=samples,
                measurement_started=measurement_started,
                cpu_before=cpu_before,
            )
            stop_reason = "duration"
        else:
            stop_reason = await self._run_soak_window(
                spec=spec,
                deployment=deployment,
                headers=headers,
                owner_id=admin.user_id,
                project_id=project.id,
                seed=seed,
                snapshots=snapshots,
                samples=samples,
                measurement_started=measurement_started,
                cpu_before=cpu_before,
            )

        duration = time.perf_counter() - measurement_started
        self._append_snapshot(
            snapshots,
            samples,
            measurement_started=measurement_started,
            cpu_before=cpu_before,
        )
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()

        storage_after = _directory_size(self._config.data_dir)
        observed_tasks = await self._resource_total(
            deployment=deployment,
            headers=headers,
            path="/api/v1/tasks",
        )
        observed_runs = await self._resource_total(
            deployment=deployment,
            headers=headers,
            path="/api/v1/runs",
        )
        duplicate_task_ids = len(samples.write_task_ids) - len(set(samples.write_task_ids))
        duplicate_run_ids = len(samples.write_run_ids) - len(set(samples.write_run_ids))
        expected_tasks = spec.seed_tasks + samples.write_operations
        expected_runs = spec.seed_tasks + samples.write_operations
        state_ok = observed_tasks >= expected_tasks and observed_runs >= expected_runs
        if spec.scenario == "idle":
            state_ok = observed_tasks == 0 and observed_runs == 0
        correctness = EnduranceCorrectnessSummary(
            attempted_operations=samples.attempted,
            completed_operations=samples.completed,
            failed_operations=samples.failed,
            seeded_tasks=spec.seed_tasks,
            observed_tasks=observed_tasks,
            observed_runs=observed_runs,
            duplicate_write_task_ids=duplicate_task_ids,
            duplicate_write_run_ids=duplicate_run_ids,
            passed=(
                samples.failed == 0
                and state_ok
                and duplicate_task_ids == 0
                and duplicate_run_ids == 0
            ),
        )
        if not state_ok:
            samples.errors.append(
                f"canonical totals do not match expected state: tasks={observed_tasks}, "
                f"runs={observed_runs}"
            )

        first = snapshots[0]
        last = snapshots[-1]
        fd_growth = _optional_growth(
            first.open_file_descriptors,
            last.open_file_descriptors,
        )
        rss_growth = _optional_growth(first.peak_rss_bytes, last.peak_rss_bytes)
        throughput = samples.completed / duration if duration > 0 else 0.0
        return EnduranceBenchmarkReport(
            schema_version=ENDURANCE_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            startup_latency=LatencyDistribution.from_seconds([startup_elapsed]),
            throughput_operations_per_second=round(throughput, 6),
            operation_latency=LatencyDistribution.from_seconds(samples.operation),
            read_latency=LatencyDistribution.from_seconds(samples.read),
            write_latency=LatencyDistribution.from_seconds(samples.write),
            snapshots=tuple(snapshots),
            resources=ResourceMetrics(
                process_cpu_seconds=round(time.process_time() - cpu_before, 6),
                traced_memory_current_bytes=traced_current,
                traced_memory_peak_bytes=traced_peak,
                peak_rss_bytes=_peak_rss_bytes(),
                storage_bytes_before=storage_before,
                storage_bytes_after=storage_after,
                storage_growth_bytes=storage_after - storage_before,
                open_file_descriptors=_open_file_descriptor_count(),
            ),
            correctness=correctness,
            measurements={
                "stop_reason": stop_reason,
                "resource_snapshot_count": len(snapshots),
                "traced_memory_growth_bytes": (
                    last.traced_memory_current_bytes - first.traced_memory_current_bytes
                ),
                "peak_rss_growth_bytes": rss_growth,
                "open_file_descriptor_growth": fd_growth,
                "storage_growth_bytes": storage_after - storage_before,
                "latency_drift_ratio": _latency_drift_ratio(snapshots),
            },
            errors=tuple(samples.errors),
        )

    async def _run_idle_window(
        self,
        *,
        spec: EnduranceBenchmarkSpec,
        snapshots: list[ResourceSnapshot],
        samples: _Samples,
        measurement_started: float,
        cpu_before: float,
    ) -> None:
        next_sample = spec.sample_interval_seconds
        while True:
            elapsed = time.perf_counter() - measurement_started
            remaining = spec.duration_seconds - elapsed
            if remaining <= 0:
                return
            await asyncio.sleep(min(spec.sample_interval_seconds, remaining))
            elapsed = time.perf_counter() - measurement_started
            if elapsed >= next_sample or elapsed >= spec.duration_seconds:
                self._append_snapshot(
                    snapshots,
                    samples,
                    measurement_started=measurement_started,
                    cpu_before=cpu_before,
                )
                while next_sample <= elapsed:
                    next_sample += spec.sample_interval_seconds

    async def _run_soak_window(
        self,
        *,
        spec: EnduranceBenchmarkSpec,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        seed: _SeedState,
        snapshots: list[ResourceSnapshot],
        samples: _Samples,
        measurement_started: float,
        cpu_before: float,
    ) -> str:
        next_sample = spec.sample_interval_seconds
        run_token = uuid.uuid4().hex
        operation_index = 0
        while operation_index < spec.max_operations:
            if time.perf_counter() - measurement_started >= spec.duration_seconds:
                return "duration"
            batch_size = min(spec.concurrency, spec.max_operations - operation_index)
            indices = tuple(range(operation_index, operation_index + batch_size))
            await asyncio.gather(
                *(
                    self._execute_soak_operation(
                        spec=spec,
                        deployment=deployment,
                        headers=headers,
                        owner_id=owner_id,
                        project_id=project_id,
                        seed=seed,
                        index=index,
                        key=f"{run_token}:{index}",
                        samples=samples,
                    )
                    for index in indices
                )
            )
            operation_index += batch_size
            elapsed = time.perf_counter() - measurement_started
            if elapsed >= next_sample:
                self._append_snapshot(
                    snapshots,
                    samples,
                    measurement_started=measurement_started,
                    cpu_before=cpu_before,
                )
                while next_sample <= elapsed:
                    next_sample += spec.sample_interval_seconds
        return "operation-limit"

    async def _execute_soak_operation(
        self,
        *,
        spec: EnduranceBenchmarkSpec,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        seed: _SeedState,
        index: int,
        key: str,
        samples: _Samples,
    ) -> None:
        samples.attempted += 1
        operation_started = time.perf_counter()
        try:
            if self._is_write_operation(spec, index):
                started = time.perf_counter()
                task_id, run_id = await asyncio.wait_for(
                    self._create_completed_task(
                        deployment=deployment,
                        headers=headers,
                        owner_id=owner_id,
                        project_id=project_id,
                        key=key,
                    ),
                    timeout=spec.timeout_seconds,
                )
                samples.write.append(time.perf_counter() - started)
                samples.write_task_ids.append(task_id)
                samples.write_run_ids.append(run_id)
                samples.write_operations += 1
            else:
                started = time.perf_counter()
                await asyncio.wait_for(
                    self._read_operation(
                        deployment=deployment,
                        headers=headers,
                        seed=seed,
                        index=index,
                    ),
                    timeout=spec.timeout_seconds,
                )
                samples.read.append(time.perf_counter() - started)
                samples.read_operations += 1
            elapsed = time.perf_counter() - operation_started
            samples.operation.append(elapsed)
            samples.window_operation.append(elapsed)
            samples.completed += 1
        except Exception as exc:  # endurance evidence records failures by design
            samples.failed += 1
            samples.errors.append(f"operation {index}: {type(exc).__name__}: {exc}")

    async def _seed_completed_tasks(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        count: int,
        timeout_seconds: float,
    ) -> _SeedState:
        task_ids: list[str] = []
        run_ids: list[str] = []
        token = uuid.uuid4().hex
        for index in range(count):
            task_id, run_id = await asyncio.wait_for(
                self._create_completed_task(
                    deployment=deployment,
                    headers=headers,
                    owner_id=owner_id,
                    project_id=project_id,
                    key=f"seed:{token}:{index}",
                ),
                timeout=timeout_seconds,
            )
            task_ids.append(task_id)
            run_ids.append(run_id)
        return _SeedState(tuple(task_ids), tuple(run_ids))

    async def _create_completed_task(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        key: str,
    ) -> tuple[str, str]:
        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={**headers, "idempotency-key": f"{key}:create"},
                body={
                    "title": "Deterministic endurance task",
                    "objective": "Exercise bounded canonical state during endurance measurement",
                    "owner_type": "user",
                    "owner_id": owner_id,
                    "project_id": project_id,
                },
            )
        )
        task_id = _require_id(created, expected_status=201, label="endurance task create")
        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers={**headers, "idempotency-key": f"{key}:queue"},
            )
        )
        _require_status(queued, 200, "endurance task queue")
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers={**headers, "idempotency-key": f"{key}:start"},
            )
        )
        run_id = _require_id(started, expected_status=200, label="endurance task start")
        refreshed = await deployment.kernel.refresh_run(
            idempotency_key=f"{key}:refresh",
            task_id=task_id,
            run_id=run_id,
        )
        if refreshed.status is not RunStatus.SUCCEEDED:
            raise RuntimeError(f"endurance run {run_id} did not succeed")
        task = await deployment.kernel.get_task(task_id)
        if task.status is not TaskStatus.SUCCEEDED:
            raise RuntimeError(f"endurance task {task_id} did not succeed")
        return task_id, run_id

    async def _read_operation(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        seed: _SeedState,
        index: int,
    ) -> None:
        task_id = seed.task_ids[index % len(seed.task_ids)]
        run_id = seed.run_ids[index % len(seed.run_ids)]
        variant = index % 4
        if variant == 0:
            body = _require_mapping(
                await deployment.http.handle(
                    HTTPRequest(
                        method="GET",
                        path="/api/v1/tasks",
                        headers=headers,
                        query={"limit": "50", "sort": "title", "direction": "asc"},
                    )
                ),
                200,
                "endurance task list",
            )
            if not isinstance(body.get("items"), list):
                raise RuntimeError("endurance task list returned no items")
            return
        if variant == 1:
            body = _require_mapping(
                await deployment.http.handle(
                    HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}", headers=headers)
                ),
                200,
                "endurance task detail",
            )
            if body.get("id") != task_id:
                raise RuntimeError("endurance task detail returned wrong task")
            return
        if variant == 2:
            body = _require_mapping(
                await deployment.http.handle(
                    HTTPRequest(method="GET", path=f"/api/v1/runs/{run_id}", headers=headers)
                ),
                200,
                "endurance run detail",
            )
            if body.get("id") != run_id:
                raise RuntimeError("endurance run detail returned wrong run")
            return
        body = _require_mapping(
            await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path=f"/api/v1/tasks/{task_id}/timeline",
                    headers=headers,
                )
            ),
            200,
            "endurance timeline",
        )
        if not isinstance(body.get("items"), list) or not body["items"]:
            raise RuntimeError("endurance timeline returned no canonical events")

    async def _resource_total(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        path: str,
    ) -> int:
        body = _require_mapping(
            await deployment.http.handle(HTTPRequest(method="GET", path=path, headers=headers)),
            200,
            f"endurance resource total {path}",
        )
        total = body.get("total")
        if not isinstance(total, int):
            raise RuntimeError(f"endurance resource total {path} returned no integer total")
        return total

    def _append_snapshot(
        self,
        snapshots: list[ResourceSnapshot],
        samples: _Samples,
        *,
        measurement_started: float,
        cpu_before: float,
    ) -> None:
        current, peak = tracemalloc.get_traced_memory()
        snapshots.append(
            ResourceSnapshot(
                elapsed_seconds=round(time.perf_counter() - measurement_started, 6),
                process_cpu_seconds=round(time.process_time() - cpu_before, 6),
                traced_memory_current_bytes=current,
                traced_memory_peak_bytes=peak,
                peak_rss_bytes=_peak_rss_bytes(),
                storage_bytes=_directory_size(self._config.data_dir),
                open_file_descriptors=_open_file_descriptor_count(),
                attempted_operations=samples.attempted,
                completed_operations=samples.completed,
                failed_operations=samples.failed,
                read_operations=samples.read_operations,
                write_operations=samples.write_operations,
                window_operation_latency=LatencyDistribution.from_seconds(
                    samples.window_operation
                ),
            )
        )
        samples.window_operation.clear()

    @staticmethod
    def _is_write_operation(spec: EnduranceBenchmarkSpec, index: int) -> bool:
        cycle = spec.read_weight + spec.write_weight
        return spec.write_weight > 0 and index % cycle < spec.write_weight


def _optional_growth(first: int | None, last: int | None) -> int | None:
    if first is None or last is None:
        return None
    return last - first


def _latency_drift_ratio(snapshots: list[ResourceSnapshot]) -> float | None:
    observed = [
        snapshot.window_operation_latency.p95_ms
        for snapshot in snapshots
        if snapshot.window_operation_latency.count > 0
        and snapshot.window_operation_latency.p95_ms > 0
    ]
    if len(observed) < 2 or observed[0] <= 0:
        return None
    return round(observed[-1] / observed[0] - 1.0, 6)
