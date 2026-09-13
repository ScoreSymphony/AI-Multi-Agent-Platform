from __future__ import annotations

import asyncio
import gc
import sqlite3
import threading
import time
import weakref
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.coordination import (
    AsyncCoordinatorRepositoryAdapter,
    CoordinationPersistenceOffload,
    CoordinationPhase,
    InMemoryCoordinatorRepository,
    SQLiteCoordinatorRepository,
    StepCoordinationRecord,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id

OWNER = OwnerRef(type="user", id="async-coordination-test-user")


def _pending_plan() -> tuple[Plan, Step, StepCoordinationRecord]:
    plan = Plan(task_id=new_id("task"), owner_ref=OWNER, active=True)
    step = Step(plan_id=plan.id, title="pending", owner_ref=OWNER)
    record = StepCoordinationRecord(
        task_id=plan.task_id,
        plan_id=plan.id,
        plan_revision=plan.revision,
        step_id=step.id,
        phase=CoordinationPhase.BLOCKED,
    )
    return plan, step, record


class _SlowSQLiteCoordinatorRepository(SQLiteCoordinatorRepository):
    def __init__(self, path: Path) -> None:
        self.delay_seconds = 0.0
        self.connection_threads: list[str] = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()


class _BlockingInMemoryCoordinatorRepository(InMemoryCoordinatorRepository):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_reads = False

    def get_plan(self, plan_id: str):  # type: ignore[no-untyped-def]
        if self.block_reads:
            self.started.set()
            self.release.wait(timeout=5)
        return super().get_plan(plan_id)


class _BlockingCreateRepository(InMemoryCoordinatorRepository):
    def __init__(self, *, fail: BaseException | None = None) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.failure = fail

    def create_plan(self, plan, steps, records):  # type: ignore[no-untyped-def]
        self.started.set()
        self.release.wait(timeout=5)
        if self.failure is not None:
            raise self.failure
        return super().create_plan(plan, steps, records)


class _FailingReadRepository(InMemoryCoordinatorRepository):
    def __init__(self, failure: BaseException) -> None:
        super().__init__()
        self.failure = failure

    def get_plan(self, plan_id: str):  # type: ignore[no-untyped-def]
        raise self.failure


def test_sqlite_connections_are_worker_owned_and_event_loop_remains_responsive(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = _SlowSQLiteCoordinatorRepository(tmp_path / "coordination.sqlite3")
        plan, step, record = _pending_plan()
        repository.create_plan(plan, (step,), (record,))
        repository.connection_threads.clear()
        repository.delay_seconds = 0.08
        adapter = AsyncCoordinatorRepositoryAdapter(repository)

        read = asyncio.create_task(adapter.get_plan(plan.id))
        heartbeat = 0
        while not read.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        assert (await read).plan == plan
        assert heartbeat >= 2
        assert repository.connection_threads
        assert all(name.startswith("coordination-persistence") for name in repository.connection_threads)

    asyncio.run(scenario())


def test_persistence_queue_is_bounded_before_executor_submission() -> None:
    async def scenario() -> None:
        repository = _BlockingInMemoryCoordinatorRepository()
        plan, step, record = _pending_plan()
        repository.create_plan(plan, (step,), (record,))
        repository.block_reads = True
        offload = CoordinationPersistenceOffload(max_concurrency=1, max_pending=2)
        adapter = AsyncCoordinatorRepositoryAdapter(repository, offload=offload)

        first = asyncio.create_task(adapter.get_plan(plan.id))
        assert await asyncio.to_thread(repository.started.wait, 1)
        second = asyncio.create_task(adapter.get_plan(plan.id))
        await asyncio.sleep(0)
        with pytest.raises(ContractError) as captured:
            await adapter.get_plan(plan.id)
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

        repository.release.set()
        assert (await first).plan == plan
        assert (await second).plan == plan

    asyncio.run(scenario())


def test_adapters_share_one_serialization_boundary_per_backing_repository() -> None:
    repository = InMemoryCoordinatorRepository()
    first = AsyncCoordinatorRepositoryAdapter(repository)
    second = AsyncCoordinatorRepositoryAdapter(repository)
    assert first.offload is second.offload


def test_shared_offload_does_not_keep_repository_alive() -> None:
    repository = InMemoryCoordinatorRepository()
    adapter = AsyncCoordinatorRepositoryAdapter(repository)
    reference = weakref.ref(repository)
    del adapter
    del repository
    gc.collect()
    assert reference() is None


def test_cancellation_waits_for_started_persistence_to_settle() -> None:
    async def scenario() -> None:
        repository = _BlockingCreateRepository()
        adapter = AsyncCoordinatorRepositoryAdapter(repository)
        plan, step, record = _pending_plan()

        write = asyncio.create_task(adapter.create_plan(plan, (step,), (record,)))
        assert await asyncio.to_thread(repository.started.wait, 1)
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done()

        repository.release.set()
        with pytest.raises(asyncio.CancelledError):
            await write
        assert repository.get_plan(plan.id).plan == plan

    asyncio.run(scenario())


def test_worker_failure_wins_over_pending_cancellation() -> None:
    async def scenario() -> None:
        repository = _BlockingCreateRepository(fail=sqlite3.OperationalError("database is locked"))
        adapter = AsyncCoordinatorRepositoryAdapter(repository)
        plan, step, record = _pending_plan()

        write = asyncio.create_task(adapter.create_plan(plan, (step,), (record,)))
        assert await asyncio.to_thread(repository.started.wait, 1)
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
        adapter = AsyncCoordinatorRepositoryAdapter(repository)
        with pytest.raises(ContractError) as captured:
            await adapter.get_plan("plan_missing")
        assert captured.value.code is code
        assert captured.value.retryable is retryable

    asyncio.run(scenario())


def test_async_repository_preserves_read_after_write_restart_and_retirement(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "coordination.sqlite3"
        repository = SQLiteCoordinatorRepository(path)
        adapter = AsyncCoordinatorRepositoryAdapter(repository)
        plan, step, record = _pending_plan()

        created = await adapter.create_plan(plan, (step,), (record,))
        assert created.plan == plan
        assert (await adapter.get_plan(plan.id)).plan == plan
        assert (await adapter.get_step_record(step.id)).step_id == step.id

        retired_at = datetime(2026, 9, 13, 18, 0, tzinfo=UTC)
        replacement = new_id("plan")
        retirement = await adapter.retire_plan(
            plan.id,
            superseded_by_plan_id=replacement,
            reason="canonical_plan_superseded",
            retired_at=retired_at,
        )
        assert retirement.superseded_by_plan_id == replacement
        assert await adapter.list_active_plans() == ()

        restarted = AsyncCoordinatorRepositoryAdapter(SQLiteCoordinatorRepository(path))
        persisted = await restarted.plan_retirement(plan.id)
        assert persisted is not None
        assert persisted.superseded_by_plan_id == replacement
        assert persisted.retired_at == retired_at
        assert (await restarted.get_plan(plan.id)).plan == plan

    asyncio.run(scenario())


def test_in_memory_and_sqlite_async_contracts_have_matching_basic_semantics(tmp_path: Path) -> None:
    async def exercise(adapter: AsyncCoordinatorRepositoryAdapter) -> tuple[str, str, int]:
        plan, step, record = _pending_plan()
        await adapter.create_plan(plan, (step,), (record,))
        state = await adapter.get_plan(plan.id)
        stored = await adapter.get_step_record(step.id)
        active = await adapter.list_active_plans()
        return state.plan.task_id, stored.phase.value, len(active)

    async def scenario() -> None:
        memory = await exercise(AsyncCoordinatorRepositoryAdapter(InMemoryCoordinatorRepository()))
        sqlite = await exercise(
            AsyncCoordinatorRepositoryAdapter(
                SQLiteCoordinatorRepository(tmp_path / "coordination.sqlite3")
            )
        )
        assert memory == sqlite

    asyncio.run(scenario())
