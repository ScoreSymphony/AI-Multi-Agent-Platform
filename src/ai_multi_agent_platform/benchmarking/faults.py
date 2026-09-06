"""Deterministic fault-under-load benchmark profiles for issue #440."""

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

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
    _require_mapping,
)
from .workloads import SingleNodeWorkloadHarness

FAULT_REPORT_SCHEMA_VERSION = "1.0"
_FAULT_ADMIN = "benchmark-fault-admin"
_FAULT_PROJECT_KEY = "performance-fault-project-v1"


@dataclass(frozen=True, slots=True)
class FaultUnderLoadSpec:
    """Versioned specification for a bounded deterministic fault during moderate load."""

    benchmark_id: str
    benchmark_version: str
    scenario: str
    deployment_profile: str
    persistence_profile: str
    operation_count: int
    concurrency: int
    fault_after_operations: int
    seed_tasks: int
    warmup_operations: int
    timeout_seconds: float
    safety_max_operations: int
    safety_max_concurrency: int
    read_weight: int = 4
    write_weight: int = 1
    optional_subsystems: tuple[str, ...] = ()
    expected_invariants: tuple[str, ...] = (
        "authorized-canonical-load-before-and-after-fault",
        "durable-authentication-survives-restart",
        "unauthenticated-access-remains-blocked",
        "canonical-state-survives-restart",
        "no-duplicate-write-identities",
        "post-fault-load-recovers",
    )
    captured_metrics: tuple[str, ...] = (
        "restart-latency",
        "pre-post-fault-throughput",
        "operation-read-write-latency-p50-p95-p99",
        "health-readiness-after-fault",
        "cpu-memory-storage",
        "correctness",
    )

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.benchmark_version.strip():
            raise ValueError("benchmark_id and benchmark_version must not be empty")
        if self.scenario != "control-plane-restart":
            raise ValueError("unsupported fault-under-load scenario")
        if self.deployment_profile != "single-node-reference":
            raise ValueError("fault benchmark requires single-node-reference deployment")
        if self.operation_count < 2:
            raise ValueError("fault benchmark requires at least two operations")
        if not 0 < self.fault_after_operations < self.operation_count:
            raise ValueError("fault_after_operations must split the measured workload")
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.seed_tasks < 1:
            raise ValueError("seed_tasks must be positive")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.read_weight < 0 or self.write_weight < 0:
            raise ValueError("read/write weights must not be negative")
        if self.read_weight + self.write_weight < 1:
            raise ValueError("fault benchmark requires a non-empty read/write mix")
        if self.safety_max_operations < 2 or self.safety_max_concurrency < 1:
            raise ValueError("fault benchmark safety limits must be positive")
        if self.operation_count > self.safety_max_operations:
            raise ValueError("requested fault workload exceeds explicit operation safety limit")
        if self.concurrency > self.safety_max_concurrency:
            raise ValueError("requested fault concurrency exceeds explicit safety limit")


@dataclass(frozen=True, slots=True)
class FaultCorrectnessSummary:
    attempted_operations: int
    completed_operations: int
    failed_operations: int
    pre_fault_completed_operations: int
    post_fault_completed_operations: int
    seeded_tasks: int
    observed_tasks: int
    observed_runs: int
    duplicate_write_task_ids: int
    duplicate_write_run_ids: int
    authentication_preserved: bool
    unauthorized_access_blocked: bool
    health_recovered: bool
    readiness_recovered: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class FaultUnderLoadReport:
    schema_version: str
    benchmark: FaultUnderLoadSpec
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
    correctness: FaultCorrectnessSummary
    measurements: Mapping[str, int | float | str | bool | None]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["errors"] = list(self.errors)
        benchmark = payload["benchmark"]
        if isinstance(benchmark, dict):
            for key in ("optional_subsystems", "expected_invariants", "captured_metrics"):
                benchmark[key] = list(benchmark[key])
        return payload


@dataclass(slots=True)
class _FaultSamples:
    attempted: int = 0
    completed: int = 0
    failed: int = 0
    reads: int = 0
    writes: int = 0
    operation: list[float] = field(default_factory=list)
    read: list[float] = field(default_factory=list)
    write: list[float] = field(default_factory=list)
    write_task_ids: list[str] = field(default_factory=list)
    write_run_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class SingleNodeFaultUnderLoadHarness(SingleNodeWorkloadHarness):
    """Inject a real deployment reconstruction between bounded load phases."""

    async def run_fault(self, spec: FaultUnderLoadSpec) -> FaultUnderLoadReport:
        _require_fresh_data_root(self._config.data_dir)
        deployment = build_single_node_deployment(self._config)
        admin = deployment.bootstrap_admin(_FAULT_ADMIN, secrets.token_urlsafe(32))
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="performance-fault-control-plane-restart",
        )
        headers = {
            "authorization": f"Bearer {credential.secret}",
            "content-type": "application/json",
        }
        project = deployment.scopes.create_project(
            key=_FAULT_PROJECT_KEY,
            name="Performance fault-under-load benchmark",
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

        samples = _FaultSamples()
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        storage_before = _directory_size(self._config.data_dir)
        cpu_before = time.process_time()
        started_at = datetime.now(UTC).isoformat()
        measurement_started = time.perf_counter()
        token = uuid.uuid4().hex

        await self._run_phase(
            spec=spec,
            deployment=deployment,
            headers=headers,
            owner_id=admin.user_id,
            project_id=project.id,
            seed=seed,
            indices=range(0, spec.fault_after_operations),
            token=token,
            samples=samples,
        )
        pre_fault_completed = samples.completed

        restart_started = time.perf_counter()
        deployment = build_single_node_deployment(self._config)
        restart_elapsed = time.perf_counter() - restart_started

        authentication_preserved = False
        try:
            actor = deployment.authentication.authenticate_bearer(credential.secret)
            authentication_preserved = actor.identity.actor_id == admin.user_id
        except Exception as exc:
            samples.errors.append(f"post-restart authentication: {type(exc).__name__}: {exc}")

        unauthorized_response = await deployment.http.handle(
            HTTPRequest(method="GET", path="/api/v1/tasks", headers={})
        )
        unauthorized_access_blocked = unauthorized_response.status in {401, 403}
        if not unauthorized_access_blocked:
            samples.errors.append(
                f"unauthenticated task access returned HTTP {unauthorized_response.status}"
            )

        health_recovered, health_status = await self._status_probe(
            deployment,
            headers=headers,
            path="/health",
        )
        readiness_recovered, readiness_status = await self._status_probe(
            deployment,
            headers=headers,
            path="/readiness",
        )

        await self._run_phase(
            spec=spec,
            deployment=deployment,
            headers=headers,
            owner_id=admin.user_id,
            project_id=project.id,
            seed=seed,
            indices=range(spec.fault_after_operations, spec.operation_count),
            token=token,
            samples=samples,
        )
        post_fault_completed = samples.completed - pre_fault_completed
        duration = time.perf_counter() - measurement_started
        process_cpu_seconds = time.process_time() - cpu_before
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
        duplicate_tasks = len(samples.write_task_ids) - len(set(samples.write_task_ids))
        duplicate_runs = len(samples.write_run_ids) - len(set(samples.write_run_ids))
        expected_tasks = spec.seed_tasks + samples.writes
        state_ok = observed_tasks >= expected_tasks and observed_runs >= expected_tasks
        if not state_ok:
            samples.errors.append(
                f"post-fault canonical totals are incomplete: tasks={observed_tasks}, "
                f"runs={observed_runs}, expected_at_least={expected_tasks}"
            )

        correctness = FaultCorrectnessSummary(
            attempted_operations=samples.attempted,
            completed_operations=samples.completed,
            failed_operations=samples.failed,
            pre_fault_completed_operations=pre_fault_completed,
            post_fault_completed_operations=post_fault_completed,
            seeded_tasks=spec.seed_tasks,
            observed_tasks=observed_tasks,
            observed_runs=observed_runs,
            duplicate_write_task_ids=duplicate_tasks,
            duplicate_write_run_ids=duplicate_runs,
            authentication_preserved=authentication_preserved,
            unauthorized_access_blocked=unauthorized_access_blocked,
            health_recovered=health_recovered,
            readiness_recovered=readiness_recovered,
            passed=(
                samples.failed == 0
                and samples.completed == spec.operation_count
                and pre_fault_completed > 0
                and post_fault_completed > 0
                and state_ok
                and duplicate_tasks == 0
                and duplicate_runs == 0
                and authentication_preserved
                and unauthorized_access_blocked
                and health_recovered
                and readiness_recovered
            ),
        )
        throughput = samples.completed / duration if duration > 0 else 0.0
        return FaultUnderLoadReport(
            schema_version=FAULT_REPORT_SCHEMA_VERSION,
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
            restart_latency=LatencyDistribution.from_seconds([restart_elapsed]),
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
                "fault_operation_index": spec.fault_after_operations,
                "pre_fault_operations": spec.fault_after_operations,
                "post_fault_operations": spec.operation_count - spec.fault_after_operations,
                "read_operations": samples.reads,
                "write_operations": samples.writes,
                "health_status_after_restart": health_status,
                "readiness_status_after_restart": readiness_status,
                "unauthenticated_http_status": unauthorized_response.status,
            },
            errors=tuple(samples.errors),
        )

    async def _run_phase(
        self,
        *,
        spec: FaultUnderLoadSpec,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        seed: Any,
        indices: range,
        token: str,
        samples: _FaultSamples,
    ) -> None:
        semaphore = asyncio.Semaphore(spec.concurrency)

        async def run_one(index: int) -> None:
            async with semaphore:
                samples.attempted += 1
                operation_started = time.perf_counter()
                try:
                    if _is_write_operation(spec, index):
                        started = time.perf_counter()
                        task_id, run_id = await asyncio.wait_for(
                            self._create_completed_task(
                                deployment=deployment,
                                headers=headers,
                                owner_id=owner_id,
                                project_id=project_id,
                                key=f"fault:{token}:{index}",
                            ),
                            timeout=spec.timeout_seconds,
                        )
                        samples.write.append(time.perf_counter() - started)
                        samples.write_task_ids.append(task_id)
                        samples.write_run_ids.append(run_id)
                        samples.writes += 1
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
                        samples.reads += 1
                    samples.operation.append(time.perf_counter() - operation_started)
                    samples.completed += 1
                except Exception as exc:
                    samples.failed += 1
                    samples.errors.append(f"operation {index}: {type(exc).__name__}: {exc}")

        await asyncio.gather(*(run_one(index) for index in indices))

    async def _status_probe(
        self,
        deployment: Any,
        *,
        headers: dict[str, str],
        path: str,
    ) -> tuple[bool, str]:
        response = await deployment.http.handle(HTTPRequest(method="GET", path=path, headers=headers))
        if response.status != 200:
            return False, f"http-{response.status}"
        body = _require_mapping(response, 200, f"fault status probe {path}")
        status = body.get("status")
        return True, status if isinstance(status, str) else "unknown"


def _is_write_operation(spec: FaultUnderLoadSpec, index: int) -> bool:
    cycle = spec.read_weight + spec.write_weight
    return spec.write_weight > 0 and index % cycle < spec.write_weight
