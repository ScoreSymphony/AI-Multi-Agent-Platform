from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.learning import (
    InMemoryLearningRepository,
    LearningQualityGate,
    LearningService,
    PromotionRegistry,
    SQLiteLearningRepository,
)
from ai_multi_agent_platform.learning.async_persistence import (
    LearningPersistenceOffload,
    learning_persistence_offload,
    post_promotion_persistence_offload,
)
from ai_multi_agent_platform.learning.async_service import RuntimeGovernedLearningService
from ai_multi_agent_platform.learning.runtime_adapter import LearningRuntimeAdapter
from ai_multi_agent_platform.security import AuthorizationGate, LocalAuthorizationProvider


class _Owner:
    pass


class _BlockingSQLiteLearningRepository(SQLiteLearningRepository):
    def __init__(self, database_path) -> None:  # type: ignore[no-untyped-def]
        super().__init__(database_path)
        self.started = threading.Event()
        self.release = threading.Event()
        self.worker_name: str | None = None

    def list_candidates(self):  # type: ignore[no-untyped-def]
        self.worker_name = threading.current_thread().name
        self.started.set()
        self.release.wait(timeout=5)
        return super().list_candidates()


async def _wait_for_event(event: threading.Event, *, timeout: float = 1.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    return event.is_set()


def _authorization_gate() -> AuthorizationGate:
    return AuthorizationGate(LocalAuthorizationProvider(()))


def _learning_service(repository) -> LearningService:  # type: ignore[no-untyped-def]
    return LearningService(
        repository,
        quality_gate=LearningQualityGate(),
        promotion_registry=PromotionRegistry(()),
        authorization_gate=_authorization_gate(),
    )


def _runtime_learning_service(repository) -> RuntimeGovernedLearningService:  # type: ignore[no-untyped-def]
    return RuntimeGovernedLearningService(
        repository,
        quality_gate=LearningQualityGate(),
        promotion_registry=PromotionRegistry(()),
        authorization_gate=_authorization_gate(),
    )


def test_runtime_learning_sqlite_read_keeps_event_loop_responsive(tmp_path) -> None:
    repository = _BlockingSQLiteLearningRepository(tmp_path / "learning.sqlite3")
    service = _runtime_learning_service(repository)

    async def scenario() -> None:
        operation = asyncio.create_task(service.async_list_candidates())
        assert await _wait_for_event(repository.started)

        heartbeats = 0
        for _ in range(5):
            await asyncio.sleep(0.005)
            heartbeats += 1

        assert heartbeats == 5
        assert not operation.done()
        assert repository.worker_name is not None
        assert repository.worker_name.startswith("learning-persistence")

        repository.release.set()
        assert await operation == ()

    asyncio.run(scenario())


def test_learning_offload_rejects_work_beyond_bounded_capacity() -> None:
    offload = LearningPersistenceOffload(max_concurrency=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()

    def blocking_operation() -> str:
        started.set()
        release.wait(timeout=5)
        return "committed"

    async def scenario() -> None:
        first = asyncio.create_task(offload.run(blocking_operation, message="first"))
        assert await _wait_for_event(started)

        with pytest.raises(ContractError) as caught:
            await offload.run(lambda: "overflow", message="overflow")
        assert caught.value.code is ErrorCode.TRANSIENT_FAILURE
        assert caught.value.retryable is True

        release.set()
        assert await first == "committed"

    asyncio.run(scenario())


def test_learning_offload_waits_for_started_persistence_before_cancellation() -> None:
    offload = LearningPersistenceOffload(max_concurrency=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()
    committed = threading.Event()

    def blocking_operation() -> None:
        started.set()
        release.wait(timeout=5)
        committed.set()

    async def scenario() -> None:
        operation = asyncio.create_task(offload.run(blocking_operation, message="cancelled"))
        assert await _wait_for_event(started)

        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert committed.is_set()

    asyncio.run(scenario())


def test_learning_offload_maps_sqlite_busy_to_retryable_transient_failure() -> None:
    offload = LearningPersistenceOffload(max_concurrency=1, max_pending=1)

    def locked_operation() -> None:
        raise sqlite3.OperationalError("database is locked")

    async def scenario() -> None:
        with pytest.raises(ContractError) as caught:
            await offload.run(locked_operation, message="learning write failed")
        assert caught.value.code is ErrorCode.TRANSIENT_FAILURE
        assert caught.value.retryable is True

    asyncio.run(scenario())


def test_learning_offload_maps_other_sqlite_failures_to_backend_error() -> None:
    offload = LearningPersistenceOffload(max_concurrency=1, max_pending=1)

    def broken_operation() -> None:
        raise sqlite3.DatabaseError("corrupt database")

    async def scenario() -> None:
        with pytest.raises(ContractError) as caught:
            await offload.run(broken_operation, message="learning read failed")
        assert caught.value.code is ErrorCode.BACKEND_ERROR
        assert caught.value.retryable is False

    asyncio.run(scenario())


def test_learning_repository_offload_is_shared_but_post_promotion_is_separate() -> None:
    repository = object()
    recorder = object()
    first_owner = _Owner()
    second_owner = _Owner()

    first = learning_persistence_offload(repository, owner=first_owner)
    second = learning_persistence_offload(repository, owner=second_owner)
    post_promotion = post_promotion_persistence_offload(recorder, owner=first_owner)

    assert first is second
    assert post_promotion is not first


def test_learning_runtime_adapter_preserves_inmemory_sqlite_read_parity(tmp_path) -> None:
    in_memory = LearningRuntimeAdapter(_learning_service(InMemoryLearningRepository()))
    sqlite = LearningRuntimeAdapter(
        _learning_service(SQLiteLearningRepository(tmp_path / "learning-parity.sqlite3"))
    )

    async def scenario() -> None:
        assert await in_memory.list_feedback() == await sqlite.list_feedback() == ()
        assert await in_memory.list_candidates() == await sqlite.list_candidates() == ()

    asyncio.run(scenario())
