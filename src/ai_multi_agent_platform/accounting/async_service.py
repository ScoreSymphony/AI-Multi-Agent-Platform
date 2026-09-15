"""Awaitable Accounting runtime boundary for blocking persistence backends."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

from .models import (
    AggregationMode,
    BudgetState,
    MeasurementQuality,
    ThresholdLevel,
    UsageAggregate,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
)
from .service import AccountingService

_T = TypeVar("_T")
_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncAccountingService(Protocol):
    """Backend-neutral awaitable Accounting contract for async runtime callers."""

    async def record(self, record: UsageRecord) -> None: ...

    async def record_unavailable(
        self,
        *,
        metric_type: str,
        unit: str,
        source: str,
        scope: UsageScope | None = None,
        provider: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        aggregation_mode: AggregationMode = AggregationMode.ADDITIVE,
    ) -> UsageRecord: ...

    async def record_external_cost(
        self,
        *,
        amount: float,
        currency: str,
        source: str,
        quality: MeasurementQuality,
        scope: UsageScope | None = None,
        provider: str | None = None,
        confidence: float | None = None,
        provenance: dict[str, JsonValue] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> UsageRecord: ...

    async def query(self, query: UsageQuery | None = None) -> tuple[UsageRecord, ...]: ...

    async def aggregate(self, query: UsageQuery) -> UsageAggregate: ...

    async def trend(
        self,
        query: UsageQuery,
        *,
        bucket_seconds: int,
    ) -> tuple[UsageAggregate, ...]: ...

    async def put_budget(self, budget: UsageBudget) -> BudgetState: ...

    async def budget_state(self, budget_id: str) -> BudgetState: ...

    async def list_budgets(self) -> tuple[UsageBudget, ...]: ...

    async def get_budget(self, budget_id: str) -> UsageBudget | None: ...

    async def get_threshold_level(self, budget_id: str) -> ThresholdLevel | None: ...

    async def get_threshold_generation(self, budget_id: str) -> int: ...


class AccountingPersistenceOffload:
    """Run one UsageStore's blocking runtime operations outside the event loop.

    Capacity is bounded before executor submission, so overload cannot create an unbounded
    persistence queue. The per-store serialization lock preserves Accounting ordering even
    when multiple adapters wrap services backed by the same canonical store.
    """

    def __init__(self, *, max_concurrency: int = 2, max_pending: int = 64) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_pending < max_concurrency:
            raise ValueError("max_pending must be >= max_concurrency")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="accounting-persistence",
        )
        self._store_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending

    async def run(self, operation: Callable[[], _T]) -> _T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "Accounting persistence queue capacity exhausted",
                retryable=True,
            )
        loop = asyncio.get_running_loop()
        try:
            worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        except BaseException:
            self._capacity.release()
            raise
        return await _await_persistence_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        try:
            with self._store_lock:
                return operation()
        finally:
            self._capacity.release()


_SHARED_ACCOUNTING_OFFLOADS = SharedPersistenceOffloadRegistry[AccountingPersistenceOffload]()


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


def _sqlite_error(exc: BaseException) -> sqlite3.Error | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, sqlite3.Error):
            return current
        current = current.__cause__
    return None


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(ErrorCode.TRANSIENT_FAILURE, message, retryable=True)
    return ContractError(ErrorCode.BACKEND_ERROR, message)


class AsyncAccountingServiceAdapter:
    """Awaitable facade over the canonical synchronous Accounting service."""

    def __init__(
        self,
        accounting: AccountingService,
        *,
        offload: AccountingPersistenceOffload | None = None,
    ) -> None:
        self._accounting = accounting
        self._offload = _SHARED_ACCOUNTING_OFFLOADS.resolve(
            accounting.store,
            owner=self,
            requested=offload,
            factory=AccountingPersistenceOffload,
        )

    @property
    def offload(self) -> AccountingPersistenceOffload:
        return self._offload

    async def _run[T](self, operation: Callable[[], T], *, message: str) -> T:
        try:
            return await self._offload.run(operation)
        except ContractError as exc:
            sqlite_error = _sqlite_error(exc)
            if sqlite_error is None:
                raise
            raise _map_sqlite_error(sqlite_error, message) from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def record(self, record: UsageRecord) -> None:
        await self._run(
            lambda: self._accounting.record(record),
            message="failed to persist Accounting usage",
        )

    async def record_unavailable(
        self,
        *,
        metric_type: str,
        unit: str,
        source: str,
        scope: UsageScope | None = None,
        provider: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        aggregation_mode: AggregationMode = AggregationMode.ADDITIVE,
    ) -> UsageRecord:
        return await self._run(
            lambda: self._accounting.record_unavailable(
                metric_type=metric_type,
                unit=unit,
                source=source,
                scope=scope,
                provider=provider,
                correlation_id=correlation_id,
                causation_id=causation_id,
                aggregation_mode=aggregation_mode,
            ),
            message="failed to persist unavailable Accounting usage",
        )

    async def record_external_cost(
        self,
        *,
        amount: float,
        currency: str,
        source: str,
        quality: MeasurementQuality,
        scope: UsageScope | None = None,
        provider: str | None = None,
        confidence: float | None = None,
        provenance: dict[str, JsonValue] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> UsageRecord:
        return await self._run(
            lambda: self._accounting.record_external_cost(
                amount=amount,
                currency=currency,
                source=source,
                quality=quality,
                scope=scope,
                provider=provider,
                confidence=confidence,
                provenance=provenance,
                correlation_id=correlation_id,
                causation_id=causation_id,
            ),
            message="failed to persist external Accounting cost",
        )

    async def query(self, query: UsageQuery | None = None) -> tuple[UsageRecord, ...]:
        return await self._run(
            lambda: self._accounting.query(query),
            message="failed to read Accounting usage",
        )

    async def aggregate(self, query: UsageQuery) -> UsageAggregate:
        return await self._run(
            lambda: self._accounting.aggregate(query),
            message="failed to aggregate Accounting usage",
        )

    async def trend(
        self,
        query: UsageQuery,
        *,
        bucket_seconds: int,
    ) -> tuple[UsageAggregate, ...]:
        return await self._run(
            lambda: self._accounting.trend(query, bucket_seconds=bucket_seconds),
            message="failed to read Accounting trend",
        )

    async def put_budget(self, budget: UsageBudget) -> BudgetState:
        return await self._run(
            lambda: self._accounting.put_budget(budget),
            message="failed to persist Accounting budget",
        )

    async def budget_state(self, budget_id: str) -> BudgetState:
        return await self._run(
            lambda: self._accounting.budget_state(budget_id),
            message="failed to read Accounting budget state",
        )

    async def list_budgets(self) -> tuple[UsageBudget, ...]:
        return await self._run(
            self._accounting.store.list_budgets,
            message="failed to list Accounting budgets",
        )

    async def get_budget(self, budget_id: str) -> UsageBudget | None:
        return await self._run(
            lambda: self._accounting.store.get_budget(budget_id),
            message="failed to read Accounting budget",
        )

    async def get_threshold_level(self, budget_id: str) -> ThresholdLevel | None:
        return await self._run(
            lambda: self._accounting.store.get_threshold_level(budget_id),
            message="failed to read Accounting threshold level",
        )

    async def get_threshold_generation(self, budget_id: str) -> int:
        return await self._run(
            lambda: self._accounting.store.get_threshold_generation(budget_id),
            message="failed to read Accounting threshold generation",
        )


def runtime_accounting_service(
    accounting: AccountingService | AsyncAccountingService,
) -> AsyncAccountingService:
    """Resolve the awaitable runtime contract without leaking backend details to callers."""

    if isinstance(accounting, AccountingService):
        return AsyncAccountingServiceAdapter(accounting)
    return accounting


__all__ = [
    "AccountingPersistenceOffload",
    "AsyncAccountingService",
    "AsyncAccountingServiceAdapter",
    "runtime_accounting_service",
]
