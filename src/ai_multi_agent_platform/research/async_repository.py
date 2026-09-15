"""Awaitable Research persistence for runtime-critical paths."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

from .models import (
    Claim,
    EvidenceRecord,
    ResearchItem,
    ResearchVerificationBinding,
    SourceObservation,
    SourceRecord,
)
from .repository import InMemoryResearchRepository, ResearchRepository

_T = TypeVar("_T")

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)
_STATE_ATTRIBUTES = (
    "_items",
    "_sources",
    "_observations",
    "_claims",
    "_evidence",
    "_bindings",
)


class AsyncResearchRepository(Protocol):
    """Backend-neutral awaitable Research repository used by async runtime callers."""

    async def create_item(self, item: ResearchItem) -> ResearchItem: ...

    async def get_item(self, research_item_id: str) -> ResearchItem: ...

    async def save_item(self, item: ResearchItem, *, expected_revision: int) -> ResearchItem: ...

    async def list_items(self) -> tuple[ResearchItem, ...]: ...

    async def create_source(self, source: SourceRecord) -> SourceRecord: ...

    async def get_source(self, source_id: str) -> SourceRecord: ...

    async def save_source(self, source: SourceRecord) -> SourceRecord: ...

    async def create_observation(self, observation: SourceObservation) -> SourceObservation: ...

    async def get_observation(self, observation_id: str) -> SourceObservation: ...

    async def list_observations(self, source_id: str) -> tuple[SourceObservation, ...]: ...

    async def observation_for_idempotency(
        self,
        source_id: str,
        key: str,
    ) -> SourceObservation | None: ...

    async def create_claim(self, claim: Claim) -> Claim: ...

    async def get_claim(self, claim_id: str) -> Claim: ...

    async def save_claim(self, claim: Claim, *, expected_revision: int) -> Claim: ...

    async def list_claims(self, research_item_id: str) -> tuple[Claim, ...]: ...

    async def create_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord: ...

    async def get_evidence(self, evidence_id: str) -> EvidenceRecord: ...

    async def list_evidence(self, research_item_id: str) -> tuple[EvidenceRecord, ...]: ...

    async def create_verification_binding(
        self,
        binding: ResearchVerificationBinding,
    ) -> ResearchVerificationBinding: ...

    async def list_verification_bindings(
        self,
        research_item_id: str,
    ) -> tuple[ResearchVerificationBinding, ...]: ...


class ResearchPersistenceOffload:
    """Bound Research-owned blocking persistence outside asyncio's default executor."""

    def __init__(self, *, max_concurrency: int = 2) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="research-persistence",
        )
        self._repository_lock = threading.Lock()

    async def run(self, operation: Callable[[], _T]) -> _T:
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        return await _await_persistence_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        # The reference repository combines canonical in-memory Research state with one
        # durable SQLite snapshot. Reads and writes therefore share one serialization
        # boundary so no awaitable caller can observe an intermediate mutation.
        with self._repository_lock:
            return operation()


_SHARED_RESEARCH_OFFLOADS = SharedPersistenceOffloadRegistry[ResearchPersistenceOffload]()


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


def _snapshot_state(repository: InMemoryResearchRepository) -> dict[str, dict[str, Any]]:
    return {attribute: dict(getattr(repository, attribute)) for attribute in _STATE_ATTRIBUTES}


def _restore_state(
    repository: InMemoryResearchRepository,
    snapshot: dict[str, dict[str, Any]],
) -> None:
    for attribute, values in snapshot.items():
        setattr(repository, attribute, values)


class AsyncResearchRepositoryAdapter:
    """Awaitable facade over the canonical synchronous Research repository."""

    def __init__(
        self,
        repository: ResearchRepository,
        *,
        offload: ResearchPersistenceOffload | None = None,
    ) -> None:
        self._repository = repository
        self._offload = _SHARED_RESEARCH_OFFLOADS.resolve(
            repository,
            owner=self,
            requested=offload,
            factory=ResearchPersistenceOffload,
        )

    @property
    def offload(self) -> ResearchPersistenceOffload:
        return self._offload

    async def _run[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        mutation: bool = False,
    ) -> T:
        def execute() -> T:
            if not mutation or not isinstance(self._repository, InMemoryResearchRepository):
                return operation()
            # InMemoryResearchRepository publishes the new object before its SQLite subclass
            # persists the aggregate snapshot. Keep a reversible worker-local snapshot so a
            # failed durable write cannot leave memory ahead of SQLite.
            with self._repository._lock:
                snapshot = _snapshot_state(self._repository)
                try:
                    return operation()
                except Exception:
                    _restore_state(self._repository, snapshot)
                    raise

        try:
            return await self._offload.run(execute)
        except ContractError as exc:
            sqlite_error = _sqlite_error(exc)
            if sqlite_error is None:
                raise
            raise _map_sqlite_error(sqlite_error, message) from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def create_item(self, item: ResearchItem) -> ResearchItem:
        return await self._run(
            lambda: self._repository.create_item(item),
            message="failed to persist Research Item",
            mutation=True,
        )

    async def get_item(self, research_item_id: str) -> ResearchItem:
        return await self._run(
            lambda: self._repository.get_item(research_item_id),
            message="failed to read Research Item",
        )

    async def save_item(self, item: ResearchItem, *, expected_revision: int) -> ResearchItem:
        return await self._run(
            lambda: self._repository.save_item(item, expected_revision=expected_revision),
            message="failed to persist Research Item",
            mutation=True,
        )

    async def list_items(self) -> tuple[ResearchItem, ...]:
        return await self._run(
            self._repository.list_items,
            message="failed to list Research Items",
        )

    async def create_source(self, source: SourceRecord) -> SourceRecord:
        return await self._run(
            lambda: self._repository.create_source(source),
            message="failed to persist Research Source",
            mutation=True,
        )

    async def get_source(self, source_id: str) -> SourceRecord:
        return await self._run(
            lambda: self._repository.get_source(source_id),
            message="failed to read Research Source",
        )

    async def save_source(self, source: SourceRecord) -> SourceRecord:
        return await self._run(
            lambda: self._repository.save_source(source),
            message="failed to persist Research Source",
            mutation=True,
        )

    async def create_observation(self, observation: SourceObservation) -> SourceObservation:
        return await self._run(
            lambda: self._repository.create_observation(observation),
            message="failed to persist Research Source Observation",
            mutation=True,
        )

    async def get_observation(self, observation_id: str) -> SourceObservation:
        return await self._run(
            lambda: self._repository.get_observation(observation_id),
            message="failed to read Research Source Observation",
        )

    async def list_observations(self, source_id: str) -> tuple[SourceObservation, ...]:
        return await self._run(
            lambda: self._repository.list_observations(source_id),
            message="failed to list Research Source Observations",
        )

    async def observation_for_idempotency(
        self,
        source_id: str,
        key: str,
    ) -> SourceObservation | None:
        return await self._run(
            lambda: self._repository.observation_for_idempotency(source_id, key),
            message="failed to read Research Source Observation idempotency state",
        )

    async def create_claim(self, claim: Claim) -> Claim:
        return await self._run(
            lambda: self._repository.create_claim(claim),
            message="failed to persist Research Claim",
            mutation=True,
        )

    async def get_claim(self, claim_id: str) -> Claim:
        return await self._run(
            lambda: self._repository.get_claim(claim_id),
            message="failed to read Research Claim",
        )

    async def save_claim(self, claim: Claim, *, expected_revision: int) -> Claim:
        return await self._run(
            lambda: self._repository.save_claim(claim, expected_revision=expected_revision),
            message="failed to persist Research Claim",
            mutation=True,
        )

    async def list_claims(self, research_item_id: str) -> tuple[Claim, ...]:
        return await self._run(
            lambda: self._repository.list_claims(research_item_id),
            message="failed to list Research Claims",
        )

    async def create_evidence(self, evidence: EvidenceRecord) -> EvidenceRecord:
        return await self._run(
            lambda: self._repository.create_evidence(evidence),
            message="failed to persist Research Evidence",
            mutation=True,
        )

    async def get_evidence(self, evidence_id: str) -> EvidenceRecord:
        return await self._run(
            lambda: self._repository.get_evidence(evidence_id),
            message="failed to read Research Evidence",
        )

    async def list_evidence(self, research_item_id: str) -> tuple[EvidenceRecord, ...]:
        return await self._run(
            lambda: self._repository.list_evidence(research_item_id),
            message="failed to list Research Evidence",
        )

    async def create_verification_binding(
        self,
        binding: ResearchVerificationBinding,
    ) -> ResearchVerificationBinding:
        return await self._run(
            lambda: self._repository.create_verification_binding(binding),
            message="failed to persist Research Verification binding",
            mutation=True,
        )

    async def list_verification_bindings(
        self,
        research_item_id: str,
    ) -> tuple[ResearchVerificationBinding, ...]:
        return await self._run(
            lambda: self._repository.list_verification_bindings(research_item_id),
            message="failed to list Research Verification bindings",
        )


__all__ = [
    "AsyncResearchRepository",
    "AsyncResearchRepositoryAdapter",
    "ResearchPersistenceOffload",
]
