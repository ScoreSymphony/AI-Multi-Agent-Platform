from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.decisions import (
    DECISION_COLLECTION,
    AsyncDecisionRuntime,
    AsyncDecisionService,
    DecisionAlternative,
    DecisionAlternativeStatus,
    DecisionOutcome,
    DecisionPersistenceOffload,
    DecisionRecord,
    DecisionRecordView,
    DecisionReference,
    DecisionService,
    SqliteDecisionRepository,
    as_async_decision_service,
    decision_record_resource_services,
)


class _SharedState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.thread_names: list[str] = []


class _SlowListService:
    def __init__(self, repository: object, state: _SharedState) -> None:
        self.repository = repository
        self._state = state

    def list_views(self) -> tuple[DecisionRecordView, ...]:
        with self._state.lock:
            self._state.active += 1
            self._state.max_active = max(self._state.max_active, self._state.active)
            self._state.thread_names.append(threading.current_thread().name)
        try:
            time.sleep(0.03)
            return ()
        finally:
            with self._state.lock:
                self._state.active -= 1


class _NativeAsyncDecisionService:
    async def create(self, record: DecisionRecord) -> DecisionRecordView:
        raise AssertionError(record)

    async def supersede(
        self,
        previous_id: str,
        replacement: DecisionRecord,
    ) -> DecisionRecordView:
        raise AssertionError(previous_id, replacement)

    async def withdraw(
        self,
        decision_record_id: str,
        *,
        actor_ref: str,
        reason: str,
    ) -> DecisionRecordView:
        raise AssertionError(decision_record_id, actor_ref, reason)

    async def link_downstream_provenance(
        self,
        decision_record_id: str,
        reference: DecisionReference,
    ) -> DecisionRecordView:
        raise AssertionError(decision_record_id, reference)

    async def action_provenance(self, decision_record_id: str) -> dict[str, object]:
        raise AssertionError(decision_record_id)

    async def view(self, decision_record_id: str) -> DecisionRecordView:
        raise AssertionError(decision_record_id)

    async def list_views(self) -> tuple[DecisionRecordView, ...]:
        return ()

    async def supersession_chain(
        self,
        decision_record_id: str,
    ) -> tuple[DecisionRecordView, ...]:
        raise AssertionError(decision_record_id)


def _record() -> DecisionRecord:
    return DecisionRecord(
        title="Choose runtime",
        subject="runtime",
        category="architecture",
        scope_type="platform",
        question="Which runtime should be used?",
        alternatives=(
            DecisionAlternative(
                label="Reference runtime",
                status=DecisionAlternativeStatus.SELECTED,
            ),
        ),
        outcome=DecisionOutcome.ADOPT,
        rationale="Evidence supports the reference runtime.",
        actor_ref="user_decision_async_test",
    )


def _runtime(service: object) -> AsyncDecisionRuntime:
    return AsyncDecisionRuntime(cast(DecisionService, service))


def test_decision_runtime_offloads_and_serializes_shared_repository() -> None:
    repository = object()
    state = _SharedState()
    first = _runtime(_SlowListService(repository, state))
    second = _runtime(_SlowListService(repository, state))

    async def scenario() -> None:
        await asyncio.gather(first.list_views(), second.list_views())

    asyncio.run(scenario())

    assert state.max_active == 1
    assert state.thread_names
    assert all(name.startswith("decision-persistence") for name in state.thread_names)


def test_decision_control_plane_factory_offloads_sync_service() -> None:
    repository = object()
    state = _SharedState()
    decisions = cast(DecisionService, _SlowListService(repository, state))
    resource = decision_record_resource_services(decisions)[DECISION_COLLECTION]

    async def scenario() -> None:
        await resource.list_resources(
            cast(RequestContext, object()),
            cast(PageQuery, object()),
        )
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert state.thread_names
    assert all(name.startswith("decision-persistence") for name in state.thread_names)


def test_decision_persistence_queue_is_bounded_without_blocking_loop() -> None:
    offload = DecisionPersistenceOffload(max_concurrency=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()

    def blocking_operation() -> str:
        started.set()
        release.wait(timeout=2)
        return threading.current_thread().name

    async def scenario() -> None:
        first = asyncio.create_task(offload.run(blocking_operation, message="first"))
        while not started.is_set():
            await asyncio.sleep(0)

        await asyncio.sleep(0)
        with pytest.raises(ContractError) as exc_info:
            await offload.run(lambda: None, message="second")
        assert exc_info.value.code is ErrorCode.TRANSIENT_FAILURE
        assert exc_info.value.retryable is True

        release.set()
        worker_name = await first
        assert worker_name.startswith("decision-persistence")

    asyncio.run(scenario())


def test_decision_persistence_maps_sqlite_failures() -> None:
    offload = DecisionPersistenceOffload()

    def locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    def broken() -> None:
        raise sqlite3.DatabaseError("corrupt database")

    async def scenario() -> None:
        with pytest.raises(ContractError) as transient:
            await offload.run(locked, message="decision persistence failed")
        assert transient.value.code is ErrorCode.TRANSIENT_FAILURE
        assert transient.value.retryable is True

        with pytest.raises(ContractError) as backend:
            await offload.run(broken, message="decision persistence failed")
        assert backend.value.code is ErrorCode.BACKEND_ERROR
        assert backend.value.retryable is False

    asyncio.run(scenario())


def test_decision_persistence_preserves_contract_errors() -> None:
    offload = DecisionPersistenceOffload()
    original = ContractError(ErrorCode.CONFLICT, "semantic conflict")

    def fail() -> None:
        raise original

    async def scenario() -> None:
        with pytest.raises(ContractError) as exc_info:
            await offload.run(fail, message="decision persistence failed")
        assert exc_info.value is original

    asyncio.run(scenario())


def test_decision_persistence_settles_started_work_before_cancellation() -> None:
    offload = DecisionPersistenceOffload()
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def operation() -> None:
        started.set()
        release.wait(timeout=2)
        completed.set()

    async def scenario() -> None:
        task = asyncio.create_task(offload.run(operation, message="cancelled operation"))
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()

    asyncio.run(scenario())


def test_decision_worker_error_wins_pending_cancellation() -> None:
    offload = DecisionPersistenceOffload()
    started = threading.Event()
    release = threading.Event()

    def operation() -> None:
        started.set()
        release.wait(timeout=2)
        raise RuntimeError("worker failed")

    async def scenario() -> None:
        task = asyncio.create_task(offload.run(operation, message="failed operation"))
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(RuntimeError, match="worker failed"):
            await task

    asyncio.run(scenario())


def test_native_async_decision_service_is_not_forced_through_offload() -> None:
    native = _NativeAsyncDecisionService()
    resolved = as_async_decision_service(cast(AsyncDecisionService, native))

    assert resolved is native
    assert asyncio.run(resolved.list_views()) == ()


def test_sqlite_decision_runtime_preserves_contract_error_and_restart_durability(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "decisions.sqlite3"
    record = _record()

    async def scenario() -> None:
        first = AsyncDecisionRuntime(DecisionService(SqliteDecisionRepository(database_path)))
        created = await first.create(record)
        assert created.record.id == record.id

        with pytest.raises(ContractError) as duplicate:
            await first.create(record)
        assert duplicate.value.code is ErrorCode.CONFLICT

        restarted = AsyncDecisionRuntime(DecisionService(SqliteDecisionRepository(database_path)))
        persisted = await restarted.view(record.id)
        assert persisted.record.content_digest == record.content_digest
        assert persisted.status.value == "current"

    asyncio.run(scenario())
