"""Awaitable Coordination repository boundary for blocking persistence backends."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Plan, Step
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

from .models import CoordinatorClaim, PlanRuntimeState, StepCoordinationRecord
from .repository import CoordinatorRepository

if TYPE_CHECKING:
    from .retirement import PlanRetirement

_T = TypeVar("_T")
_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class _RetirementRepository(Protocol):
    def retire_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        retired_at: datetime,
    ) -> PlanRetirement: ...

    def plan_retirement(self, plan_id: str) -> PlanRetirement | None: ...


class AsyncCoordinatorRepository(Protocol):
    """Backend-neutral awaitable persistence contract for Coordination runtime callers."""

    async def create_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        records: tuple[StepCoordinationRecord, ...],
    ) -> PlanRuntimeState: ...

    async def get_plan(self, plan_id: str) -> PlanRuntimeState: ...

    async def get_plan_snapshot(
        self,
        plan_id: str,
    ) -> tuple[PlanRuntimeState, tuple[StepCoordinationRecord, ...]]: ...

    async def get_step_record(self, step_id: str) -> StepCoordinationRecord: ...

    async def list_step_records(self, plan_id: str) -> tuple[StepCoordinationRecord, ...]: ...

    async def list_active_plans(self) -> tuple[PlanRuntimeState, ...]: ...

    async def save_step(
        self,
        *,
        step: Step,
        record: StepCoordinationRecord,
        expected_revision: int,
        claim: CoordinatorClaim | None = None,
        now: datetime | None = None,
    ) -> StepCoordinationRecord: ...

    async def acquire_claim(
        self,
        *,
        step_id: str,
        owner_id: str,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None: ...

    async def renew_claim(
        self,
        *,
        claim: CoordinatorClaim,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None: ...

    async def release_claim(self, claim: CoordinatorClaim) -> bool: ...

    async def retire_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        retired_at: datetime,
    ) -> PlanRetirement: ...

    async def plan_retirement(self, plan_id: str) -> PlanRetirement | None: ...


class CoordinationPersistenceOffload:
    """Serialize and bound synchronous repository work outside the asyncio event loop."""

    def __init__(self, *, max_concurrency: int = 2, max_pending: int = 64) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_pending < max_concurrency:
            raise ValueError("max_pending must be >= max_concurrency")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="coordination-persistence",
        )
        self._repository_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending

    async def run(self, operation: Callable[[], _T]) -> _T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "Coordination persistence queue capacity exhausted",
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
            with self._repository_lock:
                return operation()
        finally:
            self._capacity.release()


_SHARED_COORDINATION_OFFLOADS = SharedPersistenceOffloadRegistry[CoordinationPersistenceOffload]()


def _coordination_offload(
    repository: CoordinatorRepository,
    requested: CoordinationPersistenceOffload | None,
    *,
    owner: object,
) -> CoordinationPersistenceOffload:
    return _SHARED_COORDINATION_OFFLOADS.resolve(
        repository,
        owner=owner,
        requested=requested,
        factory=CoordinationPersistenceOffload,
    )


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


class AsyncCoordinatorRepositoryAdapter:
    """Awaitable facade over the canonical synchronous Coordination repository."""

    def __init__(
        self,
        repository: CoordinatorRepository,
        *,
        offload: CoordinationPersistenceOffload | None = None,
    ) -> None:
        self._repository = repository
        self._offload = _coordination_offload(repository, offload, owner=self)

    @property
    def offload(self) -> CoordinationPersistenceOffload:
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

    async def create_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        records: tuple[StepCoordinationRecord, ...],
    ) -> PlanRuntimeState:
        return await self._run(
            lambda: self._repository.create_plan(plan, steps, records),
            message="failed to persist Coordination plan",
        )

    async def get_plan(self, plan_id: str) -> PlanRuntimeState:
        return await self._run(
            lambda: self._repository.get_plan(plan_id),
            message="failed to read Coordination plan",
        )

    async def get_plan_snapshot(
        self,
        plan_id: str,
    ) -> tuple[PlanRuntimeState, tuple[StepCoordinationRecord, ...]]:
        snapshot_reader = getattr(self._repository, "get_plan_snapshot", None)
        if callable(snapshot_reader):
            return await self._run(
                lambda: cast(
                    tuple[PlanRuntimeState, tuple[StepCoordinationRecord, ...]],
                    snapshot_reader(plan_id),
                ),
                message="failed to read Coordination plan snapshot",
            )

        # Preserve the original synchronous repository compatibility seam. Current platform
        # durable backends implement the optional atomic snapshot capability; older/custom
        # repositories remain usable and their split reads still execute in one serialized
        # offload operation instead of on the event loop.
        return await self._run(
            lambda: (
                self._repository.get_plan(plan_id),
                self._repository.list_step_records(plan_id),
            ),
            message="failed to read Coordination plan snapshot",
        )

    async def get_step_record(self, step_id: str) -> StepCoordinationRecord:
        return await self._run(
            lambda: self._repository.get_step_record(step_id),
            message="failed to read Coordination step",
        )

    async def list_step_records(self, plan_id: str) -> tuple[StepCoordinationRecord, ...]:
        return await self._run(
            lambda: self._repository.list_step_records(plan_id),
            message="failed to list Coordination steps",
        )

    async def list_active_plans(self) -> tuple[PlanRuntimeState, ...]:
        return await self._run(
            self._repository.list_active_plans,
            message="failed to list active Coordination plans",
        )

    async def save_step(
        self,
        *,
        step: Step,
        record: StepCoordinationRecord,
        expected_revision: int,
        claim: CoordinatorClaim | None = None,
        now: datetime | None = None,
    ) -> StepCoordinationRecord:
        return await self._run(
            lambda: self._repository.save_step(
                step=step,
                record=record,
                expected_revision=expected_revision,
                claim=claim,
                now=now,
            ),
            message="failed to persist Coordination step",
        )

    async def acquire_claim(
        self,
        *,
        step_id: str,
        owner_id: str,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None:
        return await self._run(
            lambda: self._repository.acquire_claim(
                step_id=step_id,
                owner_id=owner_id,
                ttl=ttl,
                now=now,
            ),
            message="failed to acquire Coordination claim",
        )

    async def renew_claim(
        self,
        *,
        claim: CoordinatorClaim,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None:
        return await self._run(
            lambda: self._repository.renew_claim(claim=claim, ttl=ttl, now=now),
            message="failed to renew Coordination claim",
        )

    async def release_claim(self, claim: CoordinatorClaim) -> bool:
        return await self._run(
            lambda: self._repository.release_claim(claim),
            message="failed to release Coordination claim",
        )

    async def retire_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        retired_at: datetime,
    ) -> PlanRetirement:
        repository = cast(_RetirementRepository, self._repository)
        return await self._run(
            lambda: repository.retire_plan(
                plan_id,
                superseded_by_plan_id=superseded_by_plan_id,
                reason=reason,
                retired_at=retired_at,
            ),
            message="failed to retire Coordination plan",
        )

    async def plan_retirement(self, plan_id: str) -> PlanRetirement | None:
        repository = cast(_RetirementRepository, self._repository)
        return await self._run(
            lambda: repository.plan_retirement(plan_id),
            message="failed to read Coordination plan retirement",
        )


def runtime_coordinator_repository(
    repository: CoordinatorRepository,
    *,
    runtime_repository: AsyncCoordinatorRepository | None = None,
) -> AsyncCoordinatorRepository:
    """Resolve the awaitable runtime contract while preserving the sync compatibility seam."""

    if runtime_repository is not None:
        return runtime_repository
    return AsyncCoordinatorRepositoryAdapter(repository)


__all__ = [
    "AsyncCoordinatorRepository",
    "AsyncCoordinatorRepositoryAdapter",
    "CoordinationPersistenceOffload",
    "runtime_coordinator_repository",
]