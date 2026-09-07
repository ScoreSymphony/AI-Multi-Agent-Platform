"""Deterministic transient persistence-fault benchmarks for issue #440."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import TaskStatus
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.kernel.repository import CommandRecord, CommitResult, EventRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

PERSISTENCE_FAULT_REPORT_SCHEMA_VERSION = "1.0"

_FAULT_MODES = {"before-commit", "after-commit", "mixed"}
_PHASES = ("create", "ready")


@dataclass(frozen=True, slots=True)
class PersistenceFaultBenchmarkSpec:
    """One bounded durable-state recovery workload over the canonical EventRepository seam."""

    task_count: int = 24
    concurrency: int = 4
    failure_mode: str = "mixed"
    failure_every: int = 3
    max_attempts: int = 3
    timeout_seconds: float = 30.0
    safety_max_tasks: int = 500
    safety_max_concurrency: int = 32
    safety_max_attempts: int = 5
    benchmark_id: str = "persistence.sqlite.transient-fault"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-reference"

    def __post_init__(self) -> None:
        if self.task_count < 1:
            raise ValueError("task_count must be at least 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.failure_mode not in _FAULT_MODES:
            raise ValueError("unsupported persistence failure_mode")
        if self.failure_every < 1:
            raise ValueError("failure_every must be at least 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.safety_max_tasks < 1:
            raise ValueError("safety_max_tasks must be at least 1")
        if self.safety_max_concurrency < 1:
            raise ValueError("safety_max_concurrency must be at least 1")
        if self.safety_max_attempts < 1:
            raise ValueError("safety_max_attempts must be at least 1")
        if self.task_count > self.safety_max_tasks:
            raise ValueError("task_count exceeds configured safety limit")
        if self.concurrency > self.safety_max_concurrency:
            raise ValueError("concurrency exceeds configured safety limit")
        if self.max_attempts > self.safety_max_attempts:
            raise ValueError("max_attempts exceeds configured safety limit")

    @property
    def logical_operation_count(self) -> int:
        return self.task_count * len(_PHASES)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "logical_operation_count": self.logical_operation_count,
            "expected_invariants": [
                "transient persistence faults cross the platform-owned EventRepository boundary",
                "retry uses the exact same idempotency key and canonical Task identity",
                "pre-commit faults leave no partial canonical event or command record",
                "post-commit response loss is recovered without duplicate canonical events",
                "all recovered Tasks contain exactly one task.created and one task.ready event",
                "reopening the SQLite repository preserves the recovered canonical state",
            ],
            "captured_metrics": [
                "logical operation latency p50/p95/p99",
                "individual persistence attempt latency p50/p95/p99",
                "fault-to-recovery latency p50/p95/p99",
                "logical operation throughput",
                "retry and injected-fault counts",
                "process CPU, traced memory, peak RSS, descriptor and storage evidence",
                "post-reopen canonical integrity checks",
            ],
            "scope_limitations": [
                "fault injection wraps the canonical EventRepository boundary",
                "this profile does not claim operating-system SQLite lock contention coverage",
                "this profile does not mutate SQLite internals or private database tables",
            ],
        }


@dataclass(frozen=True, slots=True)
class PersistenceFaultEvidence:
    planned_faults: int
    injected_failures: int
    injected_before_commit: int
    injected_after_commit: int
    repository_commit_calls: int
    delegated_commit_calls: int


@dataclass(frozen=True, slots=True)
class PersistenceFaultCorrectnessSummary:
    expected_tasks: int
    completed_tasks: int
    expected_logical_operations: int
    successful_logical_operations: int
    retryable_failures_observed: int
    recovered_faulted_operations: int
    exhausted_retries: int
    unexpected_failures: int
    task_state_failures: int
    history_failures: int
    command_record_failures: int
    stream_set_failures: int
    passed: bool


@dataclass(frozen=True, slots=True)
class PersistenceFaultBenchmarkReport:
    schema_version: str
    benchmark: PersistenceFaultBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    throughput_logical_operations_per_second: float
    logical_operation_latency: LatencyDistribution
    attempt_latency: LatencyDistribution
    recovery_latency: LatencyDistribution
    resources: ResourceMetrics
    faults: PersistenceFaultEvidence
    correctness: PersistenceFaultCorrectnessSummary
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["benchmark"] = self.benchmark.to_dict()
        payload["environment"] = dict(self.environment)
        payload["errors"] = list(self.errors)
        return cast(dict[str, Any], _json_compatible(payload))


@dataclass(frozen=True, slots=True)
class _OperationResult:
    success: bool
    attempts: int
    retryable_failures: int
    logical_latency: float
    attempt_latencies: tuple[float, ...]
    recovery_latency: float | None
    error: str | None


class _FaultInjectingEventRepository:
    """Decorator that injects one retryable persistence fault per planned command key."""

    def __init__(self, delegate: EventRepository, plan: Mapping[str, str]) -> None:
        self._delegate = delegate
        self._plan = dict(plan)
        self._fired: set[str] = set()
        self.commit_calls = 0
        self.delegated_commit_calls = 0
        self.injected_before_commit = 0
        self.injected_after_commit = 0

    @property
    def injected_failures(self) -> int:
        return self.injected_before_commit + self.injected_after_commit

    async def read_events(self, stream_id: str) -> tuple[PlatformEvent, ...]:
        return await self._delegate.read_events(stream_id)

    async def revision(self, stream_id: str) -> int:
        return await self._delegate.revision(stream_id)

    async def find_command(self, scope: str, idempotency_key: str) -> CommandRecord | None:
        return await self._delegate.find_command(scope, idempotency_key)

    async def list_stream_ids(self) -> tuple[str, ...]:
        return await self._delegate.list_stream_ids()

    async def commit(
        self,
        *,
        stream_id: str,
        expected_revision: int,
        events: tuple[PlatformEvent, ...],
        command: CommandRecord | None = None,
    ) -> CommitResult:
        self.commit_calls += 1
        key = None if command is None else command.idempotency_key
        mode = None if key is None else self._plan.get(key)
        should_inject = mode is not None and key not in self._fired
        if should_inject and key is not None:
            self._fired.add(key)
            if mode == "before-commit":
                self.injected_before_commit += 1
                raise _transient_fault(key, mode)

            self.delegated_commit_calls += 1
            await self._delegate.commit(
                stream_id=stream_id,
                expected_revision=expected_revision,
                events=events,
                command=command,
            )
            self.injected_after_commit += 1
            raise _transient_fault(key, cast(str, mode))

        self.delegated_commit_calls += 1
        return await self._delegate.commit(
            stream_id=stream_id,
            expected_revision=expected_revision,
            events=events,
            command=command,
        )


class PersistenceFaultBenchmarkHarness:
    """Measure bounded transient persistence failure and idempotent durable recovery."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(self, spec: PersistenceFaultBenchmarkSpec) -> PersistenceFaultBenchmarkReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        database = self._data_dir / "kernel.sqlite3"
        plan = _fault_plan(spec)
        durable = SqliteKernelRepository(database)
        repository = _FaultInjectingEventRepository(durable, plan)
        kernel = _kernel(repository)

        logical_latencies: list[float] = []
        attempt_latencies: list[float] = []
        recovery_latencies: list[float] = []
        errors: list[str] = []
        retryable_failures = 0
        recovered_faulted_operations = 0
        exhausted_retries = 0
        unexpected_failures = 0
        successful_operations = 0
        completed_tasks = 0

        storage_before = _directory_size(self._data_dir)
        cpu_before = time.process_time()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        tracemalloc.start()

        semaphore = asyncio.Semaphore(spec.concurrency)

        async def run_task(index: int) -> None:
            nonlocal retryable_failures
            nonlocal recovered_faulted_operations
            nonlocal exhausted_retries
            nonlocal unexpected_failures
            nonlocal successful_operations
            nonlocal completed_tasks

            task_id = _task_id(index)
            async with semaphore:
                await asyncio.sleep(0)
                create_result = await _with_retry(
                    lambda: kernel.create_task(
                        idempotency_key=_key(index, "create"),
                        title=f"Persistence fault benchmark task {index}",
                        objective="Verify durable recovery after transient persistence failure",
                        owner_type="user",
                        owner_id="benchmark",
                        task_id=task_id,
                    ),
                    max_attempts=spec.max_attempts,
                )
                _record_operation(
                    create_result,
                    logical_latencies,
                    attempt_latencies,
                    recovery_latencies,
                )
                retryable_failures += create_result.retryable_failures
                successful_operations += int(create_result.success)
                recovered_faulted_operations += int(
                    create_result.success and create_result.retryable_failures > 0
                )
                if not create_result.success:
                    exhausted_retries += int(create_result.retryable_failures > 0)
                    unexpected_failures += int(create_result.retryable_failures == 0)
                    if create_result.error is not None:
                        errors.append(f"task {index} create failed: {create_result.error}")
                    return

                await asyncio.sleep(0)
                ready_result = await _with_retry(
                    lambda: kernel.ready_task(
                        idempotency_key=_key(index, "ready"),
                        task_id=task_id,
                    ),
                    max_attempts=spec.max_attempts,
                )
                _record_operation(
                    ready_result,
                    logical_latencies,
                    attempt_latencies,
                    recovery_latencies,
                )
                retryable_failures += ready_result.retryable_failures
                successful_operations += int(ready_result.success)
                recovered_faulted_operations += int(
                    ready_result.success and ready_result.retryable_failures > 0
                )
                if not ready_result.success:
                    exhausted_retries += int(ready_result.retryable_failures > 0)
                    unexpected_failures += int(ready_result.retryable_failures == 0)
                    if ready_result.error is not None:
                        errors.append(f"task {index} ready failed: {ready_result.error}")
                    return
                completed_tasks += 1

        try:
            async with asyncio.timeout(spec.timeout_seconds):
                await asyncio.gather(*(run_task(index) for index in range(spec.task_count)))
        except TimeoutError:
            errors.append(
                f"persistence fault benchmark timed out after {spec.timeout_seconds:g} seconds"
            )
        finally:
            duration = max(0.0, time.perf_counter() - started)
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        (
            task_state_failures,
            history_failures,
            command_record_failures,
            stream_set_failures,
        ) = await _verify_reopened_state(database, spec)

        faults = PersistenceFaultEvidence(
            planned_faults=len(plan),
            injected_failures=repository.injected_failures,
            injected_before_commit=repository.injected_before_commit,
            injected_after_commit=repository.injected_after_commit,
            repository_commit_calls=repository.commit_calls,
            delegated_commit_calls=repository.delegated_commit_calls,
        )
        correctness = PersistenceFaultCorrectnessSummary(
            expected_tasks=spec.task_count,
            completed_tasks=completed_tasks,
            expected_logical_operations=spec.logical_operation_count,
            successful_logical_operations=successful_operations,
            retryable_failures_observed=retryable_failures,
            recovered_faulted_operations=recovered_faulted_operations,
            exhausted_retries=exhausted_retries,
            unexpected_failures=unexpected_failures,
            task_state_failures=task_state_failures,
            history_failures=history_failures,
            command_record_failures=command_record_failures,
            stream_set_failures=stream_set_failures,
            passed=(
                not errors
                and completed_tasks == spec.task_count
                and successful_operations == spec.logical_operation_count
                and retryable_failures == len(plan)
                and recovered_faulted_operations == len(plan)
                and exhausted_retries == 0
                and unexpected_failures == 0
                and faults.injected_failures == len(plan)
                and task_state_failures == 0
                and history_failures == 0
                and command_record_failures == 0
                and stream_set_failures == 0
            ),
        )
        if not correctness.passed and not errors:
            errors.append("persistence fault correctness invariants failed")

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
        throughput = successful_operations / duration if duration > 0 else 0.0

        return PersistenceFaultBenchmarkReport(
            schema_version=PERSISTENCE_FAULT_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at.isoformat(),
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_logical_operations_per_second=round(throughput, 6),
            logical_operation_latency=LatencyDistribution.from_seconds(logical_latencies),
            attempt_latency=LatencyDistribution.from_seconds(attempt_latencies),
            recovery_latency=LatencyDistribution.from_seconds(recovery_latencies),
            resources=resources,
            faults=faults,
            correctness=correctness,
            errors=tuple(errors),
        )


async def _with_retry(
    operation: Callable[[], Awaitable[object]],
    *,
    max_attempts: int,
) -> _OperationResult:
    logical_started = time.perf_counter()
    first_fault_at: float | None = None
    attempt_latencies: list[float] = []
    retryable_failures = 0

    for attempt in range(1, max_attempts + 1):
        attempt_started = time.perf_counter()
        try:
            await operation()
        except ContractError as exc:
            attempt_latencies.append(time.perf_counter() - attempt_started)
            if exc.code is ErrorCode.TRANSIENT_FAILURE and exc.retryable:
                retryable_failures += 1
                if first_fault_at is None:
                    first_fault_at = time.perf_counter()
                if attempt < max_attempts:
                    await asyncio.sleep(0)
                    continue
                return _OperationResult(
                    success=False,
                    attempts=attempt,
                    retryable_failures=retryable_failures,
                    logical_latency=time.perf_counter() - logical_started,
                    attempt_latencies=tuple(attempt_latencies),
                    recovery_latency=None,
                    error=f"{exc.code.value}: {exc.message}",
                )
            return _OperationResult(
                success=False,
                attempts=attempt,
                retryable_failures=retryable_failures,
                logical_latency=time.perf_counter() - logical_started,
                attempt_latencies=tuple(attempt_latencies),
                recovery_latency=None,
                error=f"{exc.code.value}: {exc.message}",
            )
        except Exception as exc:  # pragma: no cover - defensive evidence path
            attempt_latencies.append(time.perf_counter() - attempt_started)
            return _OperationResult(
                success=False,
                attempts=attempt,
                retryable_failures=retryable_failures,
                logical_latency=time.perf_counter() - logical_started,
                attempt_latencies=tuple(attempt_latencies),
                recovery_latency=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        attempt_latencies.append(time.perf_counter() - attempt_started)
        completed_at = time.perf_counter()
        return _OperationResult(
            success=True,
            attempts=attempt,
            retryable_failures=retryable_failures,
            logical_latency=completed_at - logical_started,
            attempt_latencies=tuple(attempt_latencies),
            recovery_latency=(
                None if first_fault_at is None else max(0.0, completed_at - first_fault_at)
            ),
            error=None,
        )

    raise AssertionError("retry loop must return from at least one attempt")


def _record_operation(
    result: _OperationResult,
    logical_latencies: list[float],
    attempt_latencies: list[float],
    recovery_latencies: list[float],
) -> None:
    logical_latencies.append(result.logical_latency)
    attempt_latencies.extend(result.attempt_latencies)
    if result.recovery_latency is not None:
        recovery_latencies.append(result.recovery_latency)


async def _verify_reopened_state(
    database: Path,
    spec: PersistenceFaultBenchmarkSpec,
) -> tuple[int, int, int, int]:
    repository = SqliteKernelRepository(database)
    kernel = _kernel(repository)
    task_state_failures = 0
    history_failures = 0
    command_record_failures = 0

    expected_streams = {_task_id(index) for index in range(spec.task_count)}
    observed_streams = set(await repository.list_stream_ids())
    stream_set_failures = len(expected_streams.symmetric_difference(observed_streams))

    for index in range(spec.task_count):
        task_id = _task_id(index)
        try:
            state = await kernel.get_task(task_id)
        except Exception:
            task_state_failures += 1
            history_failures += 1
            command_record_failures += len(_PHASES)
            continue

        if state.status is not TaskStatus.READY:
            task_state_failures += 1

        history = await kernel.history(task_id)
        event_types = [event.event_type for event in history]
        if (
            len(history) != 2
            or event_types.count("task.created") != 1
            or event_types.count("task.ready") != 1
        ):
            history_failures += 1

        for phase in _PHASES:
            scope = "task:create" if phase == "create" else task_id
            command = await repository.find_command(scope, _key(index, phase))
            if command is None:
                command_record_failures += 1

    return (
        task_state_failures,
        history_failures,
        command_record_failures,
        stream_set_failures,
    )


def _fault_plan(spec: PersistenceFaultBenchmarkSpec) -> dict[str, str]:
    plan: dict[str, str] = {}
    selected = 0
    ordinal = 0
    for index in range(spec.task_count):
        for phase in _PHASES:
            if ordinal % spec.failure_every == 0:
                if spec.failure_mode == "mixed":
                    mode = "before-commit" if selected % 2 == 0 else "after-commit"
                else:
                    mode = spec.failure_mode
                plan[_key(index, phase)] = mode
                selected += 1
            ordinal += 1
    return plan


def _transient_fault(key: str, mode: str) -> ContractError:
    return ContractError(
        ErrorCode.TRANSIENT_FAILURE,
        f"deterministic persistence fault ({mode}) for {key}",
        retryable=True,
        details={"fault_mode": mode, "idempotency_key": key},
    )


def _kernel(repository: EventRepository) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )


def _key(index: int, phase: str) -> str:
    return f"persistence-fault:{index}:{phase}"


def _task_id(index: int) -> str:
    return f"task_00000000-0000-0000-0000-{index:012x}"


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value
