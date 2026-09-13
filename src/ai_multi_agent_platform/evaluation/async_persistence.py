"""Awaitable Evaluation persistence adapters for runtime-critical paths."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .aggregation import AggregatedEvaluationResult
from .contracts import EvaluationHistoryRepository, EvaluationRepository
from .manifest_repository import EvalManifestRepository
from .models import ComparisonReport, EvaluationResult, EvaluationRun, EvaluationSuite
from .reproducibility import EvalManifest
from .suite_assets import EvaluationSuiteAssetRepository

_T = TypeVar("_T")

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncEvaluationRepository(Protocol):
    """Backend-neutral awaitable persistence required by Evaluation runtime execution."""

    async def save_run(self, run: EvaluationRun) -> None: ...

    async def get_run(self, run_id: str) -> EvaluationRun | None: ...

    async def save_result(self, result: EvaluationResult) -> None: ...

    async def list_results(self, evaluation_run_id: str) -> tuple[EvaluationResult, ...]: ...

    async def save_aggregate(self, aggregate: AggregatedEvaluationResult) -> None: ...

    async def list_aggregates(
        self,
        evaluation_run_id: str,
        *,
        aggregation_policy_id: str | None = None,
        aggregation_policy_version: str | None = None,
    ) -> tuple[AggregatedEvaluationResult, ...]: ...

    async def save_comparison(
        self,
        comparison: ComparisonReport,
        *,
        candidate_reference_kinds: frozenset[str] = frozenset(),
        performance_sensitive: bool = False,
    ) -> None: ...

    async def get_comparison(self, current_run_id: str) -> ComparisonReport | None: ...

    async def get_comparison_lens(
        self,
        current_run_id: str,
    ) -> tuple[frozenset[str], bool] | None: ...


class AsyncEvaluationHistoryRepository(AsyncEvaluationRepository, Protocol):
    """Awaitable history extension used by Evaluation services and Control Plane reads."""

    async def list_runs(
        self,
        *,
        suite_id: str | None = None,
        suite_version: str | None = None,
        limit: int | None = 100,
    ) -> tuple[EvaluationRun, ...]: ...

    async def list_case_results(
        self,
        *,
        case_id: str,
        evaluator_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationResult, ...]: ...


class AsyncEvalManifestRepository(Protocol):
    """Awaitable immutable EvalManifest persistence for Evaluation runtime execution."""

    async def save_manifest(self, manifest: EvalManifest) -> None: ...

    async def get_manifest(self, evaluation_run_id: str) -> EvalManifest | None: ...


class AsyncEvaluationSuiteAssetRepository(Protocol):
    """Awaitable exact-version Suite asset persistence for runtime/northbound paths."""

    async def create_suite(self, suite: EvaluationSuite) -> str: ...

    async def get_suite(self, suite_ref: str) -> EvaluationSuite | None: ...

    async def list_suites(self) -> tuple[EvaluationSuite, ...]: ...

    async def delete_suite(
        self,
        suite_ref: str,
        *,
        expected_checksum: str | None = None,
    ) -> None: ...


class EvaluationPersistenceOffload:
    """Bound Evaluation-owned blocking persistence outside the shared asyncio executor.

    One instance may be shared by the History, EvalManifest and Suite-asset adapters that target
    the same SQLite database. This keeps durable writes serialized across those logical stores,
    bounds active worker operations, and leaves asyncio's process-wide default executor available
    for unrelated Control Plane work.
    """

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="evaluation-persistence",
        )
        self._write_lock = threading.Lock()

    async def run(self, operation: Callable[[], _T], *, write: bool = False) -> _T:
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(self._executor, self._run_sync, operation, write)
        return await _await_persistence_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T], write: bool) -> _T:
        if write:
            with self._write_lock:
                return operation()
        return operation()


async def _await_persistence_boundary[T](worker: asyncio.Future[T]) -> T:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        failure = worker.exception()
        if failure is not None:
            raise failure from None
        raise


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return ContractError(ErrorCode.BACKEND_ERROR, message)


def _map_contract_sqlite_error(exc: ContractError, message: str) -> ContractError:
    cause = exc.__cause__
    if (
        exc.code is ErrorCode.BACKEND_ERROR
        and isinstance(cause, sqlite3.OperationalError)
        and any(marker in str(cause).casefold() for marker in _BUSY_MARKERS)
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return exc


class _AsyncAdapterBase:
    def __init__(self, *, offload: EvaluationPersistenceOffload | None = None) -> None:
        self._offload = offload or EvaluationPersistenceOffload()

    async def _run[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._offload.run(operation, write=write)
        except ContractError as exc:
            mapped = _map_contract_sqlite_error(exc, message)
            if mapped is exc:
                raise
            raise mapped from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc


class AsyncEvaluationRepositoryAdapter(_AsyncAdapterBase):
    """Awaitable facade over the existing synchronous Evaluation repository contract."""

    def __init__(
        self,
        repository: EvaluationRepository,
        *,
        offload: EvaluationPersistenceOffload | None = None,
    ) -> None:
        super().__init__(offload=offload)
        self._repository = repository

    async def save_run(self, run: EvaluationRun) -> None:
        await self._run(
            lambda: self._repository.save_run(run),
            message="failed to persist evaluation run",
            write=True,
        )

    async def get_run(self, run_id: str) -> EvaluationRun | None:
        return await self._run(
            lambda: self._repository.get_run(run_id),
            message="failed to read evaluation run",
        )

    async def save_result(self, result: EvaluationResult) -> None:
        await self._run(
            lambda: self._repository.save_result(result),
            message="failed to persist evaluation result",
            write=True,
        )

    async def list_results(self, evaluation_run_id: str) -> tuple[EvaluationResult, ...]:
        return await self._run(
            lambda: self._repository.list_results(evaluation_run_id),
            message="failed to list evaluation results",
        )

    async def save_aggregate(self, aggregate: AggregatedEvaluationResult) -> None:
        await self._run(
            lambda: self._repository.save_aggregate(aggregate),
            message="failed to persist evaluation aggregate",
            write=True,
        )

    async def list_aggregates(
        self,
        evaluation_run_id: str,
        *,
        aggregation_policy_id: str | None = None,
        aggregation_policy_version: str | None = None,
    ) -> tuple[AggregatedEvaluationResult, ...]:
        return await self._run(
            lambda: self._repository.list_aggregates(
                evaluation_run_id,
                aggregation_policy_id=aggregation_policy_id,
                aggregation_policy_version=aggregation_policy_version,
            ),
            message="failed to list evaluation aggregates",
        )

    async def save_comparison(
        self,
        comparison: ComparisonReport,
        *,
        candidate_reference_kinds: frozenset[str] = frozenset(),
        performance_sensitive: bool = False,
    ) -> None:
        await self._run(
            lambda: self._repository.save_comparison(
                comparison,
                candidate_reference_kinds=candidate_reference_kinds,
                performance_sensitive=performance_sensitive,
            ),
            message="failed to persist evaluation comparison",
            write=True,
        )

    async def get_comparison(self, current_run_id: str) -> ComparisonReport | None:
        return await self._run(
            lambda: self._repository.get_comparison(current_run_id),
            message="failed to read evaluation comparison",
        )

    async def get_comparison_lens(
        self,
        current_run_id: str,
    ) -> tuple[frozenset[str], bool] | None:
        return await self._run(
            lambda: self._repository.get_comparison_lens(current_run_id),
            message="failed to read evaluation comparison lens",
        )


class AsyncEvaluationHistoryRepositoryAdapter(AsyncEvaluationRepositoryAdapter):
    """Awaitable facade over synchronous Evaluation history persistence."""

    def __init__(
        self,
        repository: EvaluationHistoryRepository,
        *,
        offload: EvaluationPersistenceOffload | None = None,
    ) -> None:
        super().__init__(repository, offload=offload)
        self._history_repository = repository

    async def list_runs(
        self,
        *,
        suite_id: str | None = None,
        suite_version: str | None = None,
        limit: int | None = 100,
    ) -> tuple[EvaluationRun, ...]:
        return await self._run(
            lambda: self._history_repository.list_runs(
                suite_id=suite_id,
                suite_version=suite_version,
                limit=limit,
            ),
            message="failed to list evaluation runs",
        )

    async def list_case_results(
        self,
        *,
        case_id: str,
        evaluator_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationResult, ...]:
        return await self._run(
            lambda: self._history_repository.list_case_results(
                case_id=case_id,
                evaluator_id=evaluator_id,
                limit=limit,
            ),
            message="failed to list evaluation case history",
        )


class AsyncEvalManifestRepositoryAdapter(_AsyncAdapterBase):
    """Awaitable facade over immutable EvalManifest persistence."""

    def __init__(
        self,
        repository: EvalManifestRepository,
        *,
        offload: EvaluationPersistenceOffload | None = None,
    ) -> None:
        super().__init__(offload=offload)
        self._repository = repository

    async def save_manifest(self, manifest: EvalManifest) -> None:
        await self._run(
            lambda: self._repository.save_manifest(manifest),
            message="failed to persist EvalManifest",
            write=True,
        )

    async def get_manifest(self, evaluation_run_id: str) -> EvalManifest | None:
        return await self._run(
            lambda: self._repository.get_manifest(evaluation_run_id),
            message="failed to read EvalManifest",
        )


class AsyncEvaluationSuiteAssetRepositoryAdapter(_AsyncAdapterBase):
    """Awaitable facade over exact-version EvaluationSuite asset persistence."""

    def __init__(
        self,
        repository: EvaluationSuiteAssetRepository,
        *,
        offload: EvaluationPersistenceOffload | None = None,
    ) -> None:
        super().__init__(offload=offload)
        self._repository = repository

    async def create_suite(self, suite: EvaluationSuite) -> str:
        return await self._run(
            lambda: self._repository.create_suite(suite),
            message="failed to persist evaluation suite asset",
            write=True,
        )

    async def get_suite(self, suite_ref: str) -> EvaluationSuite | None:
        return await self._run(
            lambda: self._repository.get_suite(suite_ref),
            message="failed to read evaluation suite asset",
        )

    async def list_suites(self) -> tuple[EvaluationSuite, ...]:
        return await self._run(
            self._repository.list_suites,
            message="failed to list evaluation suite assets",
        )

    async def delete_suite(
        self,
        suite_ref: str,
        *,
        expected_checksum: str | None = None,
    ) -> None:
        await self._run(
            lambda: self._repository.delete_suite(
                suite_ref,
                expected_checksum=expected_checksum,
            ),
            message="failed to delete evaluation suite asset",
            write=True,
        )


__all__ = [
    "AsyncEvalManifestRepository",
    "AsyncEvalManifestRepositoryAdapter",
    "AsyncEvaluationHistoryRepository",
    "AsyncEvaluationHistoryRepositoryAdapter",
    "AsyncEvaluationRepository",
    "AsyncEvaluationRepositoryAdapter",
    "AsyncEvaluationSuiteAssetRepository",
    "AsyncEvaluationSuiteAssetRepositoryAdapter",
    "EvaluationPersistenceOffload",
]
