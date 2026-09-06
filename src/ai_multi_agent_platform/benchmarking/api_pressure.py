"""Authenticated Control Plane API pressure evidence for issue #440."""

from __future__ import annotations

import asyncio
import math
import secrets
import time
import tracemalloc
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.control_plane import HTTPRequest, HTTPResponse
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
    _require_id,
    _require_mapping,
)

API_PRESSURE_REPORT_SCHEMA_VERSION = "1.0"
APIPressureOperation = Literal[
    "bearer-list",
    "session-list",
    "authorized-detail",
    "pagination-scan",
]
_API_PRESSURE_OPERATIONS: tuple[APIPressureOperation, ...] = (
    "bearer-list",
    "session-list",
    "authorized-detail",
    "pagination-scan",
)
_API_PRESSURE_ADMIN = "benchmark-api-pressure-admin"
_API_PRESSURE_PROJECT_KEY = "performance-api-pressure-project-v1"


@dataclass(frozen=True, slots=True)
class APIPressureBenchmarkSpec:
    """One bounded composite authenticated Control Plane workload."""

    seed_tasks: int
    operation_count: int
    concurrency: int
    page_size: int
    warmup_operations: int = 4
    timeout_seconds: float = 30.0
    safety_max_seed_tasks: int = 10_000
    repetition_count: int = 1
    benchmark_id: str = "single-node.api-pressure"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-reference"
    workload_distribution: str = "bearer-session-detail-pagination"

    def __post_init__(self) -> None:
        if self.seed_tasks < 1:
            raise ValueError("seed_tasks must be at least 1")
        if self.operation_count < len(_API_PRESSURE_OPERATIONS):
            raise ValueError("operation_count must be at least 4")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if not 1 <= self.page_size <= 200:
            raise ValueError("page_size must be between 1 and 200")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.safety_max_seed_tasks < 1:
            raise ValueError("safety_max_seed_tasks must be at least 1")
        if self.seed_tasks > self.safety_max_seed_tasks:
            raise ValueError("seed_tasks exceeds configured API pressure safety bound")
        if self.repetition_count != 1:
            raise ValueError("one API pressure report represents exactly one fresh-state repetition")
        if self.deployment_profile != "single-node-reference":
            raise ValueError("API pressure v1 requires single-node-reference deployment")

    @property
    def expected_pages_per_scan(self) -> int:
        return math.ceil(self.seed_tasks / self.page_size)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "expected_pages_per_scan": self.expected_pages_per_scan,
            "expected_invariants": [
                "all measured requests pass canonical authentication",
                "session and bearer reads return the same canonical task population",
                "authorization-protected detail reads return the requested canonical Task",
                "cursor pagination returns every seeded Task exactly once per scan",
                "every measured HTTP response has a unique canonical request ID",
            ],
            "captured_metrics": [
                "operation latency p50/p95/p99",
                "bearer list latency p50/p95/p99",
                "browser-session list latency p50/p95/p99",
                "authorization-protected detail latency p50/p95/p99",
                "full pagination scan latency p50/p95/p99",
                "pagination page latency p50/p95/p99",
                "completed operations per second",
                "process CPU and traced memory",
                "SQLite storage growth and open descriptors",
            ],
        }


@dataclass(frozen=True, slots=True)
class APIPressureCorrectnessSummary:
    attempted_operations: int
    completed_operations: int
    failed_operations: int
    seeded_tasks: int
    observed_tasks: int
    bearer_list_operations: int
    session_list_operations: int
    authorized_detail_operations: int
    pagination_scan_operations: int
    pagination_page_requests: int
    pagination_duplicate_ids: int
    pagination_incomplete_scans: int
    measured_http_requests: int
    unique_request_ids: int
    passed: bool


@dataclass(frozen=True, slots=True)
class APIPressureBenchmarkReport:
    schema_version: str
    benchmark: APIPressureBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_operations_per_second: float
    operation_latency: LatencyDistribution
    bearer_list_latency: LatencyDistribution
    session_list_latency: LatencyDistribution
    authorized_detail_latency: LatencyDistribution
    pagination_scan_latency: LatencyDistribution
    pagination_page_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: APIPressureCorrectnessSummary
    measurements: dict[str, int | float]
    sample_task_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["benchmark"] = self.benchmark.to_dict()
        payload["sample_task_ids"] = list(self.sample_task_ids)
        payload["errors"] = list(self.errors)
        return payload


@dataclass(frozen=True, slots=True)
class _OperationEvidence:
    operation: APIPressureOperation
    elapsed_seconds: float
    request_ids: tuple[str, ...]
    page_latencies: tuple[float, ...] = ()
    pagination_duplicate_ids: int = 0
    pagination_complete: bool = True


@dataclass(slots=True)
class _Samples:
    operation: list[float] = field(default_factory=list)
    bearer_list: list[float] = field(default_factory=list)
    session_list: list[float] = field(default_factory=list)
    authorized_detail: list[float] = field(default_factory=list)
    pagination_scan: list[float] = field(default_factory=list)
    pagination_page: list[float] = field(default_factory=list)
    request_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    completed: int = 0
    bearer_list_operations: int = 0
    session_list_operations: int = 0
    authorized_detail_operations: int = 0
    pagination_scan_operations: int = 0
    pagination_duplicate_ids: int = 0
    pagination_incomplete_scans: int = 0


class SingleNodeAPIPressureHarness:
    """Measure authenticated API overhead through the production-shaped Control Plane."""

    def __init__(self, config: SingleNodeConfig, *, platform_commit: str = "unknown") -> None:
        self._config = config
        self._platform_commit = platform_commit

    async def run(self, spec: APIPressureBenchmarkSpec) -> APIPressureBenchmarkReport:
        _require_fresh_data_root(self._config.data_dir)

        deployment = build_single_node_deployment(self._config)
        password = secrets.token_urlsafe(32)
        admin = deployment.bootstrap_admin(_API_PRESSURE_ADMIN, password)
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="performance-api-pressure",
        )
        project = deployment.scopes.create_project(
            key=_API_PRESSURE_PROJECT_KEY,
            name="API pressure benchmark",
            owner_type="user",
            owner_id=admin.user_id,
        )
        bearer_headers = {
            "authorization": f"Bearer {credential.secret}",
            "content-type": "application/json",
        }
        task_ids = await self._seed_tasks(
            deployment=deployment,
            headers=bearer_headers,
            owner_id=admin.user_id,
            project_id=project.id,
            count=spec.seed_tasks,
            timeout_seconds=spec.timeout_seconds,
        )
        session_cookie = await self._login_session(
            deployment=deployment,
            password=password,
            timeout_seconds=spec.timeout_seconds,
        )
        session_headers = {"cookie": session_cookie}

        for index in range(spec.warmup_operations):
            await asyncio.wait_for(
                self._execute_operation(
                    deployment=deployment,
                    spec=spec,
                    index=index,
                    task_ids=task_ids,
                    bearer_headers=bearer_headers,
                    session_headers=session_headers,
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
        semaphore = asyncio.Semaphore(spec.concurrency)

        async def run_one(index: int) -> None:
            async with semaphore:
                try:
                    evidence = await asyncio.wait_for(
                        self._execute_operation(
                            deployment=deployment,
                            spec=spec,
                            index=index,
                            task_ids=task_ids,
                            bearer_headers=bearer_headers,
                            session_headers=session_headers,
                        ),
                        timeout=spec.timeout_seconds,
                    )
                    _record_evidence(samples, evidence)
                except Exception as exc:  # benchmark evidence retains deterministic failures
                    samples.errors.append(f"operation {index}: {type(exc).__name__}: {exc}")

        await asyncio.gather(*(run_one(index) for index in range(spec.operation_count)))
        duration = time.perf_counter() - wall_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()
        storage_after = _directory_size(self._config.data_dir)

        observed_tasks = await self._task_total(
            deployment=deployment,
            headers=bearer_headers,
            timeout_seconds=spec.timeout_seconds,
        )
        failed = spec.operation_count - samples.completed
        measured_http_requests = len(samples.request_ids)
        unique_request_ids = len(set(samples.request_ids))
        all_categories_present = all(
            count > 0
            for count in (
                samples.bearer_list_operations,
                samples.session_list_operations,
                samples.authorized_detail_operations,
                samples.pagination_scan_operations,
            )
        )
        correctness = APIPressureCorrectnessSummary(
            attempted_operations=spec.operation_count,
            completed_operations=samples.completed,
            failed_operations=failed,
            seeded_tasks=spec.seed_tasks,
            observed_tasks=observed_tasks,
            bearer_list_operations=samples.bearer_list_operations,
            session_list_operations=samples.session_list_operations,
            authorized_detail_operations=samples.authorized_detail_operations,
            pagination_scan_operations=samples.pagination_scan_operations,
            pagination_page_requests=len(samples.pagination_page),
            pagination_duplicate_ids=samples.pagination_duplicate_ids,
            pagination_incomplete_scans=samples.pagination_incomplete_scans,
            measured_http_requests=measured_http_requests,
            unique_request_ids=unique_request_ids,
            passed=(
                failed == 0
                and observed_tasks == spec.seed_tasks
                and all_categories_present
                and samples.pagination_duplicate_ids == 0
                and samples.pagination_incomplete_scans == 0
                and measured_http_requests > 0
                and unique_request_ids == measured_http_requests
            ),
        )
        if observed_tasks != spec.seed_tasks:
            samples.errors.append(
                f"observed {observed_tasks} Tasks after benchmark, expected {spec.seed_tasks}"
            )
        if unique_request_ids != measured_http_requests:
            samples.errors.append("measured HTTP response request IDs were not globally unique")
        if not all_categories_present:
            samples.errors.append("not every API pressure operation category was measured")

        throughput = samples.completed / duration if duration > 0 else 0.0
        return APIPressureBenchmarkReport(
            schema_version=API_PRESSURE_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_operations_per_second=round(throughput, 6),
            operation_latency=LatencyDistribution.from_seconds(samples.operation),
            bearer_list_latency=LatencyDistribution.from_seconds(samples.bearer_list),
            session_list_latency=LatencyDistribution.from_seconds(samples.session_list),
            authorized_detail_latency=LatencyDistribution.from_seconds(samples.authorized_detail),
            pagination_scan_latency=LatencyDistribution.from_seconds(samples.pagination_scan),
            pagination_page_latency=LatencyDistribution.from_seconds(samples.pagination_page),
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
                "seed_tasks": spec.seed_tasks,
                "observed_tasks": observed_tasks,
                "page_size": spec.page_size,
                "expected_pages_per_scan": spec.expected_pages_per_scan,
                "pagination_page_requests": len(samples.pagination_page),
                "measured_http_requests": measured_http_requests,
            },
            sample_task_ids=task_ids[:10],
            errors=tuple(samples.errors),
        )

    async def _seed_tasks(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        owner_id: str,
        project_id: str,
        count: int,
        timeout_seconds: float,
    ) -> tuple[str, ...]:
        task_ids: list[str] = []
        token = uuid.uuid4().hex
        for index in range(count):
            response = await asyncio.wait_for(
                deployment.http.handle(
                    HTTPRequest(
                        method="POST",
                        path="/api/v1/tasks",
                        headers={**headers, "idempotency-key": f"{token}:seed:{index}"},
                        body={
                            "title": f"API pressure Task {index:06d}",
                            "objective": "Provide canonical state for authenticated API pressure",
                            "owner_type": "user",
                            "owner_id": owner_id,
                            "project_id": project_id,
                        },
                    )
                ),
                timeout=timeout_seconds,
            )
            task_ids.append(
                _require_id(response, expected_status=201, label="API pressure seed Task create")
            )
        return tuple(task_ids)

    async def _login_session(
        self,
        *,
        deployment: Any,
        password: str,
        timeout_seconds: float,
    ) -> str:
        response = await asyncio.wait_for(
            deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path="/api/v1/auth/login",
                    body={"username": _API_PRESSURE_ADMIN, "password": password},
                )
            ),
            timeout=timeout_seconds,
        )
        if response.status != 200:
            raise RuntimeError(f"browser-session login returned HTTP {response.status}")
        set_cookie = response.headers.get("set-cookie")
        if not isinstance(set_cookie, str) or "=" not in set_cookie:
            raise RuntimeError("browser-session login returned no session cookie")
        return set_cookie.split(";", 1)[0]

    async def _execute_operation(
        self,
        *,
        deployment: Any,
        spec: APIPressureBenchmarkSpec,
        index: int,
        task_ids: tuple[str, ...],
        bearer_headers: dict[str, str],
        session_headers: dict[str, str],
    ) -> _OperationEvidence:
        operation = _API_PRESSURE_OPERATIONS[index % len(_API_PRESSURE_OPERATIONS)]
        started = time.perf_counter()
        if operation == "bearer-list":
            response = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/tasks",
                    headers=bearer_headers,
                    query={
                        "limit": str(spec.page_size),
                        "sort": "id",
                        "direction": "asc",
                    },
                )
            )
            _require_task_list(response, expected_total=spec.seed_tasks, label="bearer Task list")
            return _OperationEvidence(
                operation=operation,
                elapsed_seconds=time.perf_counter() - started,
                request_ids=(_require_request_id(response),),
            )

        if operation == "session-list":
            response = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/tasks",
                    headers=session_headers,
                    query={
                        "limit": str(spec.page_size),
                        "sort": "id",
                        "direction": "asc",
                    },
                )
            )
            _require_task_list(response, expected_total=spec.seed_tasks, label="session Task list")
            return _OperationEvidence(
                operation=operation,
                elapsed_seconds=time.perf_counter() - started,
                request_ids=(_require_request_id(response),),
            )

        if operation == "authorized-detail":
            task_id = task_ids[index % len(task_ids)]
            response = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path=f"/api/v1/tasks/{task_id}",
                    headers=bearer_headers,
                )
            )
            body = _require_mapping(response, 200, "authorization-protected Task detail")
            if body.get("id") != task_id:
                raise RuntimeError("authorization-protected Task detail returned wrong Task")
            return _OperationEvidence(
                operation=operation,
                elapsed_seconds=time.perf_counter() - started,
                request_ids=(_require_request_id(response),),
            )

        request_ids, page_latencies, duplicate_ids, complete = await self._pagination_scan(
            deployment=deployment,
            spec=spec,
            headers=bearer_headers,
        )
        return _OperationEvidence(
            operation=operation,
            elapsed_seconds=time.perf_counter() - started,
            request_ids=request_ids,
            page_latencies=page_latencies,
            pagination_duplicate_ids=duplicate_ids,
            pagination_complete=complete,
        )

    async def _pagination_scan(
        self,
        *,
        deployment: Any,
        spec: APIPressureBenchmarkSpec,
        headers: dict[str, str],
    ) -> tuple[tuple[str, ...], tuple[float, ...], int, bool]:
        cursor: str | None = None
        seen: set[str] = set()
        duplicate_ids = 0
        request_ids: list[str] = []
        page_latencies: list[float] = []
        max_pages = spec.expected_pages_per_scan + 1

        for _ in range(max_pages):
            query = {
                "limit": str(spec.page_size),
                "sort": "id",
                "direction": "asc",
            }
            if cursor is not None:
                query["cursor"] = cursor
            page_started = time.perf_counter()
            response = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/tasks",
                    headers=headers,
                    query=query,
                )
            )
            page_latencies.append(time.perf_counter() - page_started)
            request_ids.append(_require_request_id(response))
            body = _require_task_list(
                response,
                expected_total=spec.seed_tasks,
                label="pagination Task list",
            )
            items = body["items"]
            assert isinstance(items, list)
            for item in items:
                if not isinstance(item, dict):
                    raise RuntimeError("pagination Task list returned a non-object item")
                task_id = item.get("id")
                if not isinstance(task_id, str):
                    raise RuntimeError("pagination Task list returned an item without an ID")
                if task_id in seen:
                    duplicate_ids += 1
                seen.add(task_id)

            next_cursor = body.get("next_cursor")
            if next_cursor is None:
                return (
                    tuple(request_ids),
                    tuple(page_latencies),
                    duplicate_ids,
                    len(seen) == spec.seed_tasks,
                )
            if not isinstance(next_cursor, str) or not next_cursor:
                raise RuntimeError("pagination Task list returned an invalid next cursor")
            if next_cursor == cursor:
                raise RuntimeError("pagination Task list did not advance its cursor")
            cursor = next_cursor

        raise RuntimeError("pagination Task list exceeded its bounded expected page count")

    async def _task_total(
        self,
        *,
        deployment: Any,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> int:
        response = await asyncio.wait_for(
            deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/tasks",
                    headers=headers,
                    query={"limit": "1"},
                )
            ),
            timeout=timeout_seconds,
        )
        body = _require_task_list(response, expected_total=None, label="Task total")
        total = body.get("total")
        if not isinstance(total, int):
            raise RuntimeError("Task total returned no integer total")
        return total


def _record_evidence(samples: _Samples, evidence: _OperationEvidence) -> None:
    samples.operation.append(evidence.elapsed_seconds)
    samples.request_ids.extend(evidence.request_ids)
    samples.completed += 1
    if evidence.operation == "bearer-list":
        samples.bearer_list.append(evidence.elapsed_seconds)
        samples.bearer_list_operations += 1
    elif evidence.operation == "session-list":
        samples.session_list.append(evidence.elapsed_seconds)
        samples.session_list_operations += 1
    elif evidence.operation == "authorized-detail":
        samples.authorized_detail.append(evidence.elapsed_seconds)
        samples.authorized_detail_operations += 1
    else:
        samples.pagination_scan.append(evidence.elapsed_seconds)
        samples.pagination_page.extend(evidence.page_latencies)
        samples.pagination_scan_operations += 1
        samples.pagination_duplicate_ids += evidence.pagination_duplicate_ids
        if not evidence.pagination_complete:
            samples.pagination_incomplete_scans += 1


def _require_task_list(
    response: HTTPResponse,
    *,
    expected_total: int | None,
    label: str,
) -> dict[str, Any]:
    body = _require_mapping(response, 200, label)
    items = body.get("items")
    if not isinstance(items, list):
        raise RuntimeError(f"{label} returned no items list")
    total = body.get("total")
    if not isinstance(total, int):
        raise RuntimeError(f"{label} returned no integer total")
    if expected_total is not None and total != expected_total:
        raise RuntimeError(f"{label} observed {total} Tasks, expected {expected_total}")
    return body


def _require_request_id(response: HTTPResponse) -> str:
    request_id = response.headers.get("x-request-id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise RuntimeError("measured Control Plane response returned no request ID")
    return request_id
