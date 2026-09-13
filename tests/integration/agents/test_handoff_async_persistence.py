from __future__ import annotations

import asyncio
import gc
import sqlite3
import threading
import time
import weakref
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.handoffs import (
    AsyncHandoffRepositoryAdapter,
    HandoffConsumption,
    HandoffContent,
    HandoffPersistenceOffload,
    InMemoryHandoffRepository,
    SQLiteHandoffRepository,
    build_handoff,
    compute_creation_request_digest,
)


def _handoff_fixture(*, summary: str = "producer completed work"):
    producer = AgentRevisionRef(new_id("agent"), 1)
    consumer = AgentRevisionRef(new_id("agent"), 1)
    content = HandoffContent(
        task_id=new_id("task"),
        plan_id=new_id("plan"),
        producer_step_id=new_id("step"),
        consumer_step_id=new_id("step"),
        producer_run_id=new_id("run"),
        producer=producer,
        intended_consumer=consumer,
        objective="Transfer durable work to the canonical consumer.",
        completed_work_summary=summary,
        recommended_next_action="Continue the consumer step.",
        requested_output="Verified consumer result.",
    )
    handoff = build_handoff(
        handoff_id=new_id("handoff"),
        revision=1,
        content=content,
    )
    return handoff, consumer


async def _wait_for_event(event: threading.Event, *, timeout: float = 1.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    return event.is_set()


class _SlowSQLiteHandoffRepository(SQLiteHandoffRepository):
    def __init__(self, path: Path) -> None:
        self.delay_seconds = 0.0
        self.connection_threads: list[str] = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()


class _BlockingReadRepository(InMemoryHandoffRepository):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_reads = False

    def get_handoff(self, handoff_id: str, revision: int | None = None):  # type: ignore[no-untyped-def]
        if self.block_reads:
            self.started.set()
            self.release.wait(timeout=5)
        return super().get_handoff(handoff_id, revision)


class _BlockingCreateRepository(InMemoryHandoffRepository):
    def __init__(self, *, fail: BaseException | None = None) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.failure = fail

    def create_handoff(  # type: ignore[no-untyped-def]
        self,
        handoff,
        *,
        idempotency_key,
        request_digest,
        expected_previous_revision,
    ):
        self.started.set()
        self.release.wait(timeout=5)
        if self.failure is not None:
            raise self.failure
        return super().create_handoff(
            handoff,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            expected_previous_revision=expected_previous_revision,
        )


class _FailingReadRepository(InMemoryHandoffRepository):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def get_handoff(self, handoff_id: str, revision: int | None = None):  # type: ignore[no-untyped-def]
        del handoff_id, revision
        raise self.failure


async def _create(
    adapter: AsyncHandoffRepositoryAdapter,
    handoff,
    *,
    key: str = "async-handoff",
):
    return await adapter.create_handoff(
        handoff,
        idempotency_key=key,
        request_digest=compute_creation_request_digest(handoff.content),
        expected_previous_revision=handoff.revision - 1,
    )


def test_sqlite_connections_are_worker_owned_and_event_loop_remains_responsive(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = _SlowSQLiteHandoffRepository(tmp_path / "handoffs.sqlite3")
        handoff, _ = _handoff_fixture()
        repository.create_handoff(
            handoff,
            idempotency_key="seed",
            request_digest=compute_creation_request_digest(handoff.content),
            expected_previous_revision=0,
        )
        repository.connection_threads.clear()
        repository.delay_seconds = 0.08
        adapter = AsyncHandoffRepositoryAdapter(repository)

        read = asyncio.create_task(adapter.get_handoff(handoff.handoff_id, handoff.revision))
        heartbeat = 0
        while not read.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        assert await read == handoff
        assert heartbeat >= 2
        assert repository.connection_threads
        assert all(name.startswith("handoff-persistence") for name in repository.connection_threads)

    asyncio.run(scenario())


def test_persistence_queue_is_bounded_before_executor_submission() -> None:
    async def scenario() -> None:
        repository = _BlockingReadRepository()
        handoff, _ = _handoff_fixture()
        repository.create_handoff(
            handoff,
            idempotency_key="seed",
            request_digest=compute_creation_request_digest(handoff.content),
            expected_previous_revision=0,
        )
        repository.block_reads = True
        offload = HandoffPersistenceOffload(max_concurrency=1, max_pending=2)
        adapter = AsyncHandoffRepositoryAdapter(repository, offload=offload)

        first = asyncio.create_task(adapter.get_handoff(handoff.handoff_id, 1))
        assert await _wait_for_event(repository.started)
        second = asyncio.create_task(adapter.get_handoff(handoff.handoff_id, 1))
        await asyncio.sleep(0)
        with pytest.raises(ContractError) as captured:
            await adapter.get_handoff(handoff.handoff_id, 1)
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

        repository.release.set()
        assert await first == handoff
        assert await second == handoff

    asyncio.run(scenario())


def test_adapters_share_one_serialization_boundary_per_backing_repository() -> None:
    repository = InMemoryHandoffRepository()
    first = AsyncHandoffRepositoryAdapter(repository)
    second = AsyncHandoffRepositoryAdapter(repository)
    assert first.offload is second.offload


def test_shared_offload_does_not_keep_repository_alive() -> None:
    repository = InMemoryHandoffRepository()
    adapter = AsyncHandoffRepositoryAdapter(repository)
    reference = weakref.ref(repository)
    del adapter
    del repository
    gc.collect()
    assert reference() is None


def test_cancellation_waits_for_started_persistence_to_settle() -> None:
    async def scenario() -> None:
        repository = _BlockingCreateRepository()
        adapter = AsyncHandoffRepositoryAdapter(repository)
        handoff, _ = _handoff_fixture()

        write = asyncio.create_task(_create(adapter, handoff))
        assert await _wait_for_event(repository.started)
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done()

        repository.release.set()
        with pytest.raises(asyncio.CancelledError):
            await write
        assert repository.get_handoff(handoff.handoff_id, handoff.revision) == handoff

    asyncio.run(scenario())


def test_worker_failure_wins_over_pending_cancellation() -> None:
    async def scenario() -> None:
        repository = _BlockingCreateRepository(fail=sqlite3.OperationalError("database is locked"))
        adapter = AsyncHandoffRepositoryAdapter(repository)
        handoff, _ = _handoff_fixture()

        write = asyncio.create_task(_create(adapter, handoff))
        assert await _wait_for_event(repository.started)
        write.cancel()
        repository.release.set()

        with pytest.raises(ContractError) as captured:
            await write
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure", "code", "retryable"),
    [
        (sqlite3.OperationalError("database is locked"), ErrorCode.TRANSIENT_FAILURE, True),
        (sqlite3.DatabaseError("corrupt page"), ErrorCode.BACKEND_ERROR, False),
    ],
)
def test_sqlite_failures_map_to_canonical_contract_errors(
    failure: BaseException,
    code: ErrorCode,
    retryable: bool,
) -> None:
    async def scenario() -> None:
        repository = _FailingReadRepository(failure)
        adapter = AsyncHandoffRepositoryAdapter(repository)
        with pytest.raises(ContractError) as captured:
            await adapter.get_handoff("handoff_missing")
        assert captured.value.code is code
        assert captured.value.retryable is retryable

    asyncio.run(scenario())


def test_async_repository_preserves_read_after_write_consumption_and_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "handoffs.sqlite3"
        handoff, consumer = _handoff_fixture()
        run_id = new_id("run")
        repository = SQLiteHandoffRepository(path)
        adapter = AsyncHandoffRepositoryAdapter(repository)

        stored, created = await _create(adapter, handoff)
        assert created is True
        assert stored == handoff
        assert await adapter.get_handoff(handoff.handoff_id, 1) == handoff

        consumption = HandoffConsumption(
            handoff_id=handoff.handoff_id,
            handoff_revision=handoff.revision,
            handoff_digest=handoff.content_digest,
            consuming_run_id=run_id,
            consumer=consumer,
        )
        bound, bound_created = await adapter.bind_consumption(consumption)
        assert bound_created is True
        assert bound == consumption
        assert await adapter.list_consumptions(handoff.handoff_id, 1) == (consumption,)
        assert await adapter.list_consumptions_for_run(run_id) == (consumption,)

        restarted = AsyncHandoffRepositoryAdapter(SQLiteHandoffRepository(path))
        assert await restarted.get_handoff(handoff.handoff_id, 1) == handoff
        assert await restarted.list_consumptions(handoff.handoff_id, 1) == (consumption,)
        assert await restarted.list_consumptions_for_run(run_id) == (consumption,)

    asyncio.run(scenario())


def test_in_memory_and_sqlite_async_contracts_have_matching_basic_semantics(tmp_path: Path) -> None:
    async def exercise(adapter: AsyncHandoffRepositoryAdapter) -> tuple[object, ...]:
        handoff, consumer = _handoff_fixture()
        stored, created = await _create(adapter, handoff)
        run_id = new_id("run")
        consumption = HandoffConsumption(
            handoff_id=handoff.handoff_id,
            handoff_revision=handoff.revision,
            handoff_digest=handoff.content_digest,
            consuming_run_id=run_id,
            consumer=consumer,
        )
        bound, bound_created = await adapter.bind_consumption(consumption)
        replay, replay_created = await adapter.bind_consumption(consumption)
        listed = await adapter.list_handoffs_for_task(handoff.task_id)
        return (
            stored.revision,
            created,
            bound.handoff_revision,
            bound_created,
            replay == bound,
            replay_created,
            len(listed),
            listed[0].revision,
        )

    async def scenario() -> None:
        memory = await exercise(AsyncHandoffRepositoryAdapter(InMemoryHandoffRepository()))
        sqlite = await exercise(
            AsyncHandoffRepositoryAdapter(SQLiteHandoffRepository(tmp_path / "handoffs.sqlite3"))
        )
        assert memory == sqlite

    asyncio.run(scenario())


def test_semantic_contract_errors_are_not_remapped_as_backend_failures() -> None:
    async def scenario() -> None:
        repository = InMemoryHandoffRepository()
        adapter = AsyncHandoffRepositoryAdapter(repository)
        handoff, _ = _handoff_fixture()
        await _create(adapter, handoff, key="same-key")
        changed, _ = _handoff_fixture(summary="different content")

        with pytest.raises(ContractError) as captured:
            await adapter.create_handoff(
                changed,
                idempotency_key="same-key",
                request_digest=compute_creation_request_digest(changed.content),
                expected_previous_revision=0,
            )
        assert captured.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())
