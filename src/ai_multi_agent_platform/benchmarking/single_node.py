"""Deterministic single-node performance benchmark harness for issue #440."""

from __future__ import annotations

import asyncio
import importlib
import os
import platform
import sys
import time
import tracemalloc
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.control_plane import HTTPRequest, HTTPResponse
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import RunStatus, TaskStatus

from .models import (
    BENCHMARK_REPORT_SCHEMA_VERSION,
    BenchmarkReport,
    BenchmarkSpec,
    CorrectnessSummary,
    LatencyDistribution,
    ResourceMetrics,
)

_BENCHMARK_ADMIN = "benchmark-admin"
_BENCHMARK_PASSWORD = "local benchmark credential only"
_BENCHMARK_PROJECT_KEY = "performance-benchmark-project-v1"


@dataclass(slots=True)
class _Samples:
    operation: list[float] = field(default_factory=list)
    admission: list[float] = field(default_factory=list)
    execution: list[float] = field(default_factory=list)
    inspection: list[float] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timeline_failures: int = 0


class SingleNodeBenchmarkHarness:
    """Exercise the production-shaped single-node composition through canonical APIs."""

    def __init__(self, config: SingleNodeConfig, *, platform_commit: str = "unknown") -> None:
        self._config = config
        self._platform_commit = platform_commit

    async def run(self, spec: BenchmarkSpec) -> BenchmarkReport:
        if spec.deployment_profile != "single-node-reference":
            raise ValueError("SingleNodeBenchmarkHarness requires single-node-reference profile")

        storage_before = _directory_size(self._config.data_dir)
        deployment = build_single_node_deployment(self._config)
        admin = deployment.bootstrap_admin(_BENCHMARK_ADMIN, _BENCHMARK_PASSWORD)
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="performance-benchmark",
        )
        project = deployment.scopes.create_project(
            key=_BENCHMARK_PROJECT_KEY,
            name="Performance benchmark",
            owner_type="user",
            owner_id=admin.user_id,
        )
        headers = {
            "authorization": f"Bearer {credential.secret}",
            "content-type": "application/json",
        }
        run_token = uuid.uuid4().hex

        for index in range(spec.warmup_operations):
            await asyncio.wait_for(
                self._execute_operation(
                    deployment=deployment,
                    headers=headers,
                    owner_id=admin.user_id,
                    project_id=project.id,
                    operation_key=f"{run_token}:warmup:{index}",
                    samples=None,
                ),
                timeout=spec.timeout_seconds,
            )

        samples = _Samples()
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        wall_started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        semaphore = asyncio.Semaphore(spec.concurrency)

        async def run_one(index: int) -> None:
            async with semaphore:
                try:
                    await asyncio.wait_for(
                        self._execute_operation(
                            deployment=deployment,
                            headers=headers,
                            owner_id=admin.user_id,
                            project_id=project.id,
                            operation_key=f"{run_token}:measured:{index}",
                            samples=samples,
                        ),
                        timeout=spec.timeout_seconds,
                    )
                except Exception as exc:  # benchmark evidence records failures by design
                    samples.errors.append(f"operation {index}: {type(exc).__name__}: {exc}")

        await asyncio.gather(*(run_one(index) for index in range(spec.operation_count)))
        duration = time.perf_counter() - wall_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()

        storage_after = _directory_size(self._config.data_dir)
        completed = len(samples.task_ids)
        duplicate_task_ids = completed - len(set(samples.task_ids))
        duplicate_run_ids = len(samples.run_ids) - len(set(samples.run_ids))
        failed = spec.operation_count - completed
        correctness = CorrectnessSummary(
            attempted_operations=spec.operation_count,
            completed_operations=completed,
            failed_operations=failed,
            duplicate_task_ids=duplicate_task_ids,
            duplicate_run_ids=duplicate_run_ids,
            timeline_failures=samples.timeline_failures,
            passed=(
                failed == 0
                and duplicate_task_ids == 0
                and duplicate_run_ids == 0
                and samples.timeline_failures == 0
            ),
        )
        throughput = completed / duration if duration > 0 else 0.0
        return BenchmarkReport(
            schema_version=BENCHMARK_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_operations_per_second=round(throughput, 6),
            operation_latency=LatencyDistribution.from_seconds(samples.operation),
            admission_latency=LatencyDistribution.from_seconds(samples.admission),
            execution_latency=LatencyDistribution.from_seconds(samples.execution),
            inspection_latency=LatencyDistribution.from_seconds(samples.inspection),
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
            errors=tuple(samples.errors),
        )

    async def _execute_operation(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        operation_key: str,
        samples: _Samples | None,
    ) -> None:
        operation_started = time.perf_counter()
        admission_started = time.perf_counter()
        created = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={**headers, "idempotency-key": f"{operation_key}:create"},
                body={
                    "title": "Deterministic benchmark task",
                    "objective": "Measure canonical platform overhead without external model latency",
                    "owner_type": "user",
                    "owner_id": owner_id,
                    "project_id": project_id,
                },
            )
        )
        admission_elapsed = time.perf_counter() - admission_started
        task_id = _require_id(created, expected_status=201, label="task create")

        execution_started = time.perf_counter()
        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers={**headers, "idempotency-key": f"{operation_key}:queue"},
            )
        )
        _require_status(queued, 200, "task queue")
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers={**headers, "idempotency-key": f"{operation_key}:start"},
            )
        )
        run_id = _require_id(started, expected_status=200, label="task start")
        refreshed = await deployment.kernel.refresh_run(
            idempotency_key=f"{operation_key}:refresh",
            task_id=task_id,
            run_id=run_id,
        )
        if refreshed.status is not RunStatus.SUCCEEDED:
            raise RuntimeError(f"run {run_id} finished as {refreshed.status.value}")
        execution_elapsed = time.perf_counter() - execution_started

        inspection_started = time.perf_counter()
        task_response = await deployment.http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}", headers=headers)
        )
        run_response = await deployment.http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/runs/{run_id}", headers=headers)
        )
        timeline_response = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task_id}/timeline",
                headers=headers,
            )
        )
        task_body = _require_mapping(task_response, 200, "task inspect")
        run_body = _require_mapping(run_response, 200, "run inspect")
        timeline_body = _require_mapping(timeline_response, 200, "timeline inspect")
        if task_body.get("status") != TaskStatus.SUCCEEDED.value:
            raise RuntimeError(f"task {task_id} did not persist succeeded state")
        if run_body.get("status") != RunStatus.SUCCEEDED.value:
            raise RuntimeError(f"run {run_id} did not persist succeeded state")
        timeline_items = timeline_body.get("items")
        timeline_ok = isinstance(timeline_items, list) and len(timeline_items) > 0
        inspection_elapsed = time.perf_counter() - inspection_started

        if samples is not None:
            samples.operation.append(time.perf_counter() - operation_started)
            samples.admission.append(admission_elapsed)
            samples.execution.append(execution_elapsed)
            samples.inspection.append(inspection_elapsed)
            samples.task_ids.append(task_id)
            samples.run_ids.append(run_id)
            if not timeline_ok:
                samples.timeline_failures += 1
        if not timeline_ok:
            raise RuntimeError(f"task {task_id} has no canonical timeline events")


def attach_baseline_comparison(report: BenchmarkReport, comparison: Any) -> BenchmarkReport:
    """Return an immutable report copy carrying a baseline comparison."""

    return replace(report, baseline_comparison=comparison)


def _require_status(response: HTTPResponse, expected: int, label: str) -> None:
    if response.status != expected:
        raise RuntimeError(f"{label} returned HTTP {response.status}: {response.body!r}")


def _require_mapping(response: HTTPResponse, expected: int, label: str) -> dict[str, Any]:
    _require_status(response, expected, label)
    if not isinstance(response.body, dict):
        raise RuntimeError(f"{label} returned a non-object body")
    return response.body


def _require_id(response: HTTPResponse, *, expected_status: int, label: str) -> str:
    body = _require_mapping(response, expected_status, label)
    identifier = body.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise RuntimeError(f"{label} response has no canonical id")
    return identifier


def _directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _environment_metadata() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _open_file_descriptor_count() -> int | None:
    proc_fd = Path("/proc/self/fd")
    if not proc_fd.is_dir():
        return None
    try:
        return len(tuple(proc_fd.iterdir()))
    except OSError:
        return None


def _peak_rss_bytes() -> int | None:
    try:
        resource = importlib.import_module("resource")
        usage = resource.getrusage(resource.RUSAGE_SELF)
        peak = int(usage.ru_maxrss)
    except (ImportError, AttributeError, OSError, ValueError):
        return None
    if sys.platform == "darwin":
        return peak
    return peak * 1024
