"""Real SQLite writer-contention benchmark evidence for issue #440."""

from __future__ import annotations

import asyncio
import sqlite3
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Lock
from typing import Any, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import TaskStatus
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

PERSISTENCE_CONTENTION_REPORT_SCHEMA_VERSION = "1.0"
_PHASES = ("create", "ready")


@dataclass(frozen=True, slots=True)
class PersistenceContentionBenchmarkSpec:
    """Bounded synchronized writer pressure against one SQLite reference database."""

    writer_count: int = 4
    tasks_per_writer: int = 8
    barrier_timeout_seconds: float = 10.0
    safety_max_writers: int = 16
    safety_max_tasks_per_writer: int = 50
    benchmark_id: str = "persistence.sqlite.writer-contention"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-reference"

    def __post_init__(self) -> None:
        if self.writer_count < 2:
            raise ValueError("writer_count must be at least 2 for contention evidence")
        if self.tasks_per_writer < 1:
            raise ValueError("tasks_per_writer must be at least 1")
        if self.barrier_timeout_seconds <= 0:
            raise ValueError("barrier_timeout_seconds must be positive")
        if self.safety_max_writers < 2:
            raise ValueError("safety_max_writers must be at least 2")
        if self.safety_max_tasks_per_writer < 1:
            raise ValueError("safety_max_tasks_per_writer must be at least 1")
        if self.writer_count > self.safety_max_writers:
            raise ValueError("writer_count exceeds configured safety limit")
        if self.tasks_per_writer > self.safety_max_tasks_per_writer:
            raise ValueError("tasks_per_writer exceeds configured safety limit")

    @property
    def task_count(self) -> int:
        return self.writer_count * self.tasks_per_writer

    @property
    def logical_operation_count(self) -> int:
        return self.task_count * len(_PHASES)

    @property
    def synchronized_rounds(self) -> int:
        return self.tasks_per_writer * len(_PHASES)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "task_count": self.task_count,
            "logical_operation_count": self.logical_operation_count,
            "synchronized_rounds": self.synchronized_rounds,
            "expected_invariants": [
                "all writes traverse PlatformKernel and SqliteKernelRepository",
                "independent repository instances share one SQLite database",
                "writers synchronize before every canonical mutation to create real overlap",
                "no benchmark-only database tables, locks or private mutations are used",
                "every logical operation uses a unique stable idempotency key",
                "all Tasks reopen as READY with exactly one created and one ready event",
            ],
            "captured_metrics": [
                "canonical mutation latency p50/p95/p99",
                "logical mutation throughput",
                "peak concurrently in-flight canonical mutations",
                "SQLite busy/locked failures and canonical conflict counts",
                "per-writer successful operation counts",
                "process CPU, traced memory, peak RSS, descriptor and storage evidence",
                "post-reopen canonical integrity checks",
            ],
            "scope_limitations": [
                "this characterizes the stdlib SQLite reference backend, not a canonical database",
                "SQLite wait time is observed through end-to-end canonical mutation latency",
                "the benchmark does not alter SQLite busy timeout, WAL settings or private tables",
                "PR-scale runs prove harness semantics only and are not operating-envelope claims",
            ],
        }


@dataclass(frozen=True, slots=True)
class PersistenceContentionEvidence:
    writer_instances: int
    synchronized_rounds: int
    peak_in_flight_operations: int
    sqlite_busy_errors: int
    contract_conflicts: int
    unexpected_errors: int
    per_writer_successful_operations: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PersistenceContentionCorrectnessSummary:
    expected_tasks: int
    completed_tasks: int
    expected_logical_operations: int
    successful_logical_operations: int
    failed_logical_operations: int
    task_state_failures: int
    history_failures: int
    command_record_failures: int
    stream_set_failures: int
    passed: bool


@dataclass(frozen=True, slots=True)
class PersistenceContentionBenchmarkReport:
    schema_version: str
    benchmark: PersistenceContentionBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_logical_operations_per_second: float
    operation_latency: LatencyDistribution
    resources: ResourceMetrics
    contention: PersistenceContentionEvidence
    correctness: PersistenceContentionCorrectnessSummary
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["benchmark"] = self.benchmark.to_dict()
        payload["errors"] = list(self.errors)
        return cast(dict[str, Any], _json_compatible(payload))


@dataclass(frozen=True, slots=True)
class _WriterResult:
    operation_latencies: tuple[float, ...]
    successful_operations: int
    completed_tasks: int
    sqlite_busy_errors: int
    contract_conflicts: int
    unexpected_errors: int
    errors: tuple[str, ...]


class _InFlightProbe:
    def __init__(self) -> None:
        self._lock = Lock()
        self._current = 0
        self._peak = 0

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak

    def enter(self) -> None:
        with self._lock:
            self._current += 1
            self._peak = max(self._peak, self._current)

    def leave(self) -> None:
        with self._lock:
            self._current -= 1


class PersistenceContentionBenchmarkHarness:
    """Measure synchronized real writer pressure through the canonical persistence path."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(
        self, spec: PersistenceContentionBenchmarkSpec
    ) -> PersistenceContentionBenchmarkReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        database = self._data_dir / "kernel.sqlite3"

        # Initialize sequentially so schema setup is not itself the measured contention workload.
        repositories = [SqliteKernelRepository(database) for _ in range(spec.writer_count)]
        kernels = [_kernel(repository) for repository in repositories]
        barrier = Barrier(spec.writer_count, timeout=spec.barrier_timeout_seconds)
        probe = _InFlightProbe()

        storage_before = _directory_size(self._data_dir)
        cpu_before = time.process_time()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        tracemalloc.start()
        errors: list[str] = []

        loop = asyncio.get_running_loop()
        try:
            with ThreadPoolExecutor(
                max_workers=spec.writer_count,
                thread_name_prefix="sqlite-contention",
            ) as executor:
                results = await asyncio.gather(
                    *(
                        loop.run_in_executor(
                            executor,
                            _run_writer,
                            kernels[writer_index],
                            spec,
                            writer_index,
                            barrier,
                            probe,
                        )
                        for writer_index in range(spec.writer_count)
                    )
                )
        finally:
            duration = max(0.0, time.perf_counter() - started)
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        operation_latencies = [
            latency for result in results for latency in result.operation_latencies
        ]
        successful_operations = sum(result.successful_operations for result in results)
        completed_tasks = sum(result.completed_tasks for result in results)
        errors.extend(error for result in results for error in result.errors)

        (
            task_state_failures,
            history_failures,
            command_record_failures,
            stream_set_failures,
        ) = await _verify_reopened_state(database, spec)

        contention = PersistenceContentionEvidence(
            writer_instances=spec.writer_count,
            synchronized_rounds=spec.synchronized_rounds,
            peak_in_flight_operations=probe.peak,
            sqlite_busy_errors=sum(result.sqlite_busy_errors for result in results),
            contract_conflicts=sum(result.contract_conflicts for result in results),
            unexpected_errors=sum(result.unexpected_errors for result in results),
            per_writer_successful_operations=tuple(
                result.successful_operations for result in results
            ),
        )
        correctness = PersistenceContentionCorrectnessSummary(
            expected_tasks=spec.task_count,
            completed_tasks=completed_tasks,
            expected_logical_operations=spec.logical_operation_count,
            successful_logical_operations=successful_operations,
            failed_logical_operations=spec.logical_operation_count - successful_operations,
            task_state_failures=task_state_failures,
            history_failures=history_failures,
            command_record_failures=command_record_failures,
            stream_set_failures=stream_set_failures,
            passed=(
                not errors
                and completed_tasks == spec.task_count
                and successful_operations == spec.logical_operation_count
                and contention.sqlite_busy_errors == 0
                and contention.contract_conflicts == 0
                and contention.unexpected_errors == 0
                and task_state_failures == 0
                and history_failures == 0
                and command_record_failures == 0
                and stream_set_failures == 0
            ),
        )
        if not correctness.passed and not errors:
            errors.append("persistence contention correctness invariants failed")

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

        return PersistenceContentionBenchmarkReport(
            schema_version=PERSISTENCE_CONTENTION_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at.isoformat(),
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_logical_operations_per_second=round(throughput, 6),
            operation_latency=LatencyDistribution.from_seconds(operation_latencies),
            resources=resources,
            contention=contention,
            correctness=correctness,
            errors=tuple(errors),
        )


def _run_writer(
    kernel: PlatformKernel,
    spec: PersistenceContentionBenchmarkSpec,
    writer_index: int,
    barrier: Barrier,
    probe: _InFlightProbe,
) -> _WriterResult:
    return asyncio.run(_run_writer_async(kernel, spec, writer_index, barrier, probe))


async def _run_writer_async(
    kernel: PlatformKernel,
    spec: PersistenceContentionBenchmarkSpec,
    writer_index: int,
    barrier: Barrier,
    probe: _InFlightProbe,
) -> _WriterResult:
    operation_latencies: list[float] = []
    successful_operations = 0
    completed_tasks = 0
    sqlite_busy_errors = 0
    contract_conflicts = 0
    unexpected_errors = 0
    errors: list[str] = []

    for item_index in range(spec.tasks_per_writer):
        global_index = writer_index * spec.tasks_per_writer + item_index
        task_id = _task_id(global_index)
        task_succeeded = True
        for phase in _PHASES:
            try:
                barrier.wait()
            except BrokenBarrierError:
                errors.append(f"writer {writer_index} synchronization barrier broke during {phase}")
                unexpected_errors += 1
                task_succeeded = False
                break

            started = time.perf_counter()
            probe.enter()
            try:
                if phase == "create":
                    await kernel.create_task(
                        idempotency_key=_key(global_index, phase),
                        title=f"SQLite contention benchmark task {global_index}",
                        objective="Measure canonical SQLite writer contention",
                        owner_type="user",
                        owner_id="benchmark",
                        task_id=task_id,
                    )
                else:
                    await kernel.ready_task(
                        idempotency_key=_key(global_index, phase),
                        task_id=task_id,
                    )
            except sqlite3.OperationalError as exc:
                message = str(exc)
                if "locked" in message.lower() or "busy" in message.lower():
                    sqlite_busy_errors += 1
                else:
                    unexpected_errors += 1
                errors.append(f"writer {writer_index} {phase}: OperationalError: {message}")
                task_succeeded = False
            except ContractError as exc:
                if exc.code is ErrorCode.CONFLICT:
                    contract_conflicts += 1
                else:
                    unexpected_errors += 1
                errors.append(f"writer {writer_index} {phase}: {exc.code.value}: {exc.message}")
                task_succeeded = False
            except Exception as exc:  # pragma: no cover - defensive evidence path
                unexpected_errors += 1
                errors.append(f"writer {writer_index} {phase}: {type(exc).__name__}: {exc}")
                task_succeeded = False
            else:
                successful_operations += 1
            finally:
                probe.leave()
                operation_latencies.append(time.perf_counter() - started)

        if task_succeeded:
            completed_tasks += 1

    return _WriterResult(
        operation_latencies=tuple(operation_latencies),
        successful_operations=successful_operations,
        completed_tasks=completed_tasks,
        sqlite_busy_errors=sqlite_busy_errors,
        contract_conflicts=contract_conflicts,
        unexpected_errors=unexpected_errors,
        errors=tuple(errors),
    )


async def _verify_reopened_state(
    database: Path,
    spec: PersistenceContentionBenchmarkSpec,
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
            if await repository.find_command(scope, _key(index, phase)) is None:
                command_record_failures += 1

    return (
        task_state_failures,
        history_failures,
        command_record_failures,
        stream_set_failures,
    )


def _kernel(repository: SqliteKernelRepository) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )


def _key(index: int, phase: str) -> str:
    return f"persistence-contention:{index}:{phase}"


def _task_id(index: int) -> str:
    return f"task_00000000-0000-0000-0000-{index:012x}"


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_compatible(item) for item in value]
    return value
