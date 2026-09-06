"""Single-node API workload profiles for issue #440."""

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

WORKLOAD_REPORT_SCHEMA_VERSION = "1.0"
_WORKLOAD_ADMIN = "benchmark-workload-admin"
_WORKLOAD_PROJECT_KEY = "performance-workload-project-v1"
_SUPPORTED_SCENARIOS = frozenset({"read-heavy", "mixed", "history", "restart"})


@dataclass(frozen=True, slots=True)
class WorkloadBenchmarkSpec:
    """Versioned, reproducible workload definition for non-lifecycle-only profiles."""

    benchmark_id: str
    benchmark_version: str
    scenario: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str
    operation_count: int
    concurrency: int
    seed_tasks: int
    warmup_operations: int
    timeout_seconds: float
    repetition_count: int = 1
    read_weight: int = 1
    write_weight: int = 0
    optional_subsystems: tuple[str, ...] = ()
    expected_invariants: tuple[str, ...] = (
        "authorized-control-plane-only",
        "canonical-task-run-correctness",
        "no-duplicate-write-identities",
    )
    captured_metrics: tuple[str, ...] = (
        "throughput",
        "operation-latency-p50-p95-p99",
        "read-latency-p50-p95-p99",
        "write-latency-p50-p95-p99",
        "restart-latency",
        "cpu-memory-storage",
        "correctness",
    )

    def __post_init__(self) -> None:
        if self.scenario not in _SUPPORTED_SCENARIOS:
            raise ValueError(f"unsupported workload scenario: {self.scenario}")
        if self.operation_count < 1:
            raise ValueError("operation_count must be at least 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.seed_tasks < 1:
            raise ValueError("seed_tasks must be at least 1")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.repetition_count != 1:
            raise ValueError("one workload report represents exactly one fresh-state repetition")
        if self.read_weight < 0 or self.write_weight < 0:
            raise ValueError("read/write weights must not be negative")
        if self.read_weight + self.write_weight < 1:
            raise ValueError("at least one read/write weight must be positive")
        if self.scenario == "mixed" and self.write_weight < 1:
            raise ValueError("mixed workload requires write_weight >= 1")
        if self.scenario != "mixed" and self.write_weight != 0:
            raise ValueError("non-mixed workloads must use write_weight=0")


@dataclass(frozen=True, slots=True)
class WorkloadCorrectnessSummary:
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
class WorkloadBenchmarkReport:
    schema_version: str
    benchmark: WorkloadBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    throughput_operations_per_second: float
    operation_latency: LatencyDistribution
    read_latency: LatencyDistribution
    write_latency: LatencyDistribution
    restart_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: WorkloadCorrectnessSummary
    measurements: Mapping[str, int | float]
    sample_task_ids: tuple[str, ...]
    sample_run_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sample_task_ids"] = list(self.sample_task_ids)
        payload["sample_run_ids"] = list(self.sample_run_ids)
        payload["errors"] = list(self.errors)
        benchmark = payload["benchmark"]
        if isinstance(benchmark, dict):
            for key in ("optional_subsystems", "expected_invariants", "captured_metrics"):
                benchmark[key] = list(benchmark[key])
        return payload


@dataclass(slots=True)
class _Samples:
    operation: list[float] = field(default_factory=list)
    read: list[float] = field(default_factory=list)
    write: list[float] = field(default_factory=list)
    restart: list[float] = field(default_factory=list)
    write_task_ids: list[str] = field(default_factory=list)
    write_run_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    completed: int = 0
    read_operations: int = 0
    write_operations: int = 0


@dataclass(frozen=True, slots=True)
class _SeedState:
    task_ids: tuple[str, ...]
    run_ids: tuple[str, ...]


class SingleNodeWorkloadHarness:
    """Exercise read/mixed/history/restart profiles through canonical public APIs."""

    def __init__(self, config: SingleNodeConfig, *, platform_commit: str = "unknown") -> None:
        self._config = config
        self._platform_commit = platform_commit

    async def run(self, spec: WorkloadBenchmarkSpec) -> WorkloadBenchmarkReport:
        if spec.deployment_profile != "single-node-reference":
            raise ValueError("workload harness requires single-node-reference deployment")
        _require_fresh_data_root(self._config.data_dir)

        deployment = build_single_node_deployment(self._config)
        admin = deployment.bootstrap_admin(_WORKLOAD_ADMIN, secrets.token_urlsafe(32))
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose=f"performance-workload-{spec.scenario}",
        )
        headers = {
            "authorization": f"Bearer {credential.secret}",
            "content-type": "application/json",
        }
        project = deployment.scopes.create_project(
            key=_WORKLOAD_PROJECT_KEY,
            name="Performance workload benchmark",
            owner_type="user",
            owner_id=admin.user_id,
        )
        seed = await self._seed_completed_tasks(
            deployment=deployment,
            headers=headers,
            owner_id=admin.user_id,
            project_id=project.id,
            count=spec.seed_tasks,
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
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        wall_started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()

        active_deployment = deployment
        if spec.scenario == "restart":
            restart_started = time.perf_counter()
            active_deployment = build_single_node_deployment(self._config)
            samples.restart.append(time.perf_counter() - restart_started)
            actor = active_deployment.authentication.authenticate_bearer(credential.secret)
            if actor.identity.actor_id != admin.user_id:
                raise RuntimeError("restart workload did not preserve benchmark authentication")

        semaphore = asyncio.Semaphore(spec.concurrency)
        run_token = uuid.uuid4().hex

        async def run_one(index: int) -> None:
            async with semaphore:
                try:
                    operation_started = time.perf_counter()
                    if self._is_write_operation(spec, index):
                        write_started = time.perf_counter()
                        task_id, run_id = await asyncio.wait_for(
                            self._create_completed_task(
                                deployment=active_deployment,
                                headers=headers,
                                owner_id=admin.user_id,
                                project_id=project.id,
                                key=f"{run_token}:measured:{index}",
                            ),
                            timeout=spec.timeout_seconds,
                        )
                        samples.write.append(time.perf_counter() - write_started)
                        samples.write_task_ids.append(task_id)
                        samples.write_run_ids.append(run_id)
                        samples.write_operations += 1
                    else:
                        read_started = time.perf_counter()
                        await asyncio.wait_for(
                            self._read_operation(
                                deployment=active_deployment,
                                headers=headers,
                                seed=seed,
                                index=index,
                            ),
                            timeout=spec.timeout_seconds,
                        )
                        samples.read.append(time.perf_counter() - read_started)
                        samples.read_operations += 1
                    samples.operation.append(time.perf_counter() - operation_started)
                    samples.completed += 1
                except Exception as exc:  # benchmark evidence records failures by design
                    samples.errors.append(f"operation {index}: {type(exc).__name__}: {exc}")

        await asyncio.gather(*(run_one(index) for index in range(spec.operation_count)))
        duration = time.perf_counter() - wall_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()
        storage_after = _directory_size(self._config.data_dir)

        observed_tasks = await self._resource_total(
            deployment=active_deployment,
            headers=headers,
            path="/api/v1/tasks",
        )
        observed_runs = await self._resource_total(
            deployment=active_deployment,
            headers=headers,
            path="/api/v1/runs",
        )
        duplicate_task_ids = len(samples.write_task_ids) - len(set(samples.write_task_ids))
        duplicate_run_ids = len(samples.write_run_ids) - len(set(samples.write_run_ids))
        expected_tasks = spec.seed_tasks + samples.write_operations
        expected_runs = spec.seed_tasks + samples.write_operations
        failed = spec.operation_count - samples.completed
        correctness = WorkloadCorrectnessSummary(
            attempted_operations=spec.operation_count,
            completed_operations=samples.completed,
            failed_operations=failed,
            seeded_tasks=spec.seed_tasks,
            observed_tasks=observed_tasks,
            observed_runs=observed_runs,
            duplicate_write_task_ids=duplicate_task_ids,
            duplicate_write_run_ids=duplicate_run_ids,
            passed=(
                failed == 0
                and observed_tasks >= expected_tasks
                and observed_runs >= expected_runs
                and duplicate_task_ids == 0
                and duplicate_run_ids == 0
            ),
        )
        if observed_tasks < expected_tasks:
            samples.errors.append(
                f"observed {observed_tasks} tasks, expected at least {expected_tasks}"
            )
        if observed_runs < expected_runs:
            samples.errors.append(
                f"observed {observed_runs} runs, expected at least {expected_runs}"
            )

        throughput = samples.completed / duration if duration > 0 else 0.0
        all_task_ids = seed.task_ids + tuple(samples.write_task_ids)
        all_run_ids = seed.run_ids + tuple(samples.write_run_ids)
        return WorkloadBenchmarkReport(
            schema_version=WORKLOAD_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_operations_per_second=round(throughput, 6),
            operation_latency=LatencyDistribution.from_seconds(samples.operation),
            read_latency=LatencyDistribution.from_seconds(samples.read),
            write_latency=LatencyDistribution.from_seconds(samples.write),
            restart_latency=LatencyDistribution.from_seconds(samples.restart),
            resources=ResourceMetrics(
                process_cpu_seconds=round(process_cpu_seconds, 6),
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
                "read_operations": samples.read_operations,
                "write_operations": samples.write_operations,
                "restart_operations": len(samples.restart),
                "seed_tasks": spec.seed_tasks,
                "observed_tasks": observed_tasks,
                "observed_runs": observed_runs,
            },
            sample_task_ids=all_task_ids[:10],
            sample_run_ids=all_run_ids[:10],
            errors=tuple(samples.errors),
        )

    async def _seed_completed_tasks(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        count: int,
    ) -> _SeedState:
        task_ids: list[str] = []
        run_ids: list[str] = []
        token = uuid.uuid4().hex
        for index in range(count):
            task_id, run_id = await self._create_completed_task(
                deployment=deployment,
                headers=headers,
                owner_id=owner_id,
                project_id=project_id,
                key=f"seed:{token}:{index}",
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
                    "title": "Deterministic workload task",
                    "objective": "Provide canonical state for a deterministic performance workload",
                    "owner_type": "user",
                    "owner_id": owner_id,
                    "project_id": project_id,
                },
            )
        )
        task_id = _require_id(created, expected_status=201, label="workload task create")
        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers={**headers, "idempotency-key": f"{key}:queue"},
            )
        )
        _require_status(queued, 200, "workload task queue")
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers={**headers, "idempotency-key": f"{key}:start"},
            )
        )
        run_id = _require_id(started, expected_status=200, label="workload task start")
        refreshed = await deployment.kernel.refresh_run(
            idempotency_key=f"{key}:refresh",
            task_id=task_id,
            run_id=run_id,
        )
        if refreshed.status is not RunStatus.SUCCEEDED:
            raise RuntimeError(f"workload run {run_id} did not succeed")
        task = await deployment.kernel.get_task(task_id)
        if task.status is not TaskStatus.SUCCEEDED:
            raise RuntimeError(f"workload task {task_id} did not succeed")
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
        variant = index % 5
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
                "task list read",
            )
            if not isinstance(body.get("items"), list):
                raise RuntimeError("task list read returned no items list")
            return
        if variant == 1:
            body = _require_mapping(
                await deployment.http.handle(
                    HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}", headers=headers)
                ),
                200,
                "task detail read",
            )
            if body.get("id") != task_id:
                raise RuntimeError("task detail read returned wrong canonical task")
            return
        if variant == 2:
            body = _require_mapping(
                await deployment.http.handle(
                    HTTPRequest(
                        method="GET",
                        path=f"/api/v1/tasks/{task_id}/runs",
                        headers=headers,
                    )
                ),
                200,
                "task runs read",
            )
            if not isinstance(body.get("items"), list) or not body["items"]:
                raise RuntimeError("task runs read returned no canonical run")
            return
        if variant == 3:
            body = _require_mapping(
                await deployment.http.handle(
                    HTTPRequest(method="GET", path=f"/api/v1/runs/{run_id}", headers=headers)
                ),
                200,
                "run detail read",
            )
            if body.get("id") != run_id:
                raise RuntimeError("run detail read returned wrong canonical run")
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
            "task timeline read",
        )
        if not isinstance(body.get("items"), list) or not body["items"]:
            raise RuntimeError("task timeline read returned no canonical events")

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
            f"resource total {path}",
        )
        total = body.get("total")
        if not isinstance(total, int):
            raise RuntimeError(f"resource total {path} returned no integer total")
        return total

    def _is_write_operation(self, spec: WorkloadBenchmarkSpec, index: int) -> bool:
        if spec.scenario != "mixed":
            return False
        cycle = spec.read_weight + spec.write_weight
        return index % cycle < spec.write_weight
