"""Async SQLite Automation persistence integration coverage for issue #892."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.automation import (
    Automation,
    AutomationCommandRecord,
    AutomationRuntimeState,
    IdentityContext,
    InMemoryAutomationRuntimeState,
    SqliteAutomationRepository,
    SqliteAutomationRuntimeState,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode


def _automation(name: str = "async sqlite") -> Automation:
    now = datetime(2026, 9, 13, 1, 0, tzinfo=UTC)
    return Automation.create(
        name=name,
        description="issue 892 automation persistence coverage",
        identity=IdentityContext(
            principal_ref="user:automation-test",
            owner_type="user",
            owner_id="automation-test",
        ),
        trigger=TriggerDefinition(type=TriggerType.MANUAL),
        task_template=TaskTemplate(
            title="Automation task",
            objective="Exercise persistence",
        ),
        now=now,
    )


def _delivery(automation: Automation, dedupe_key: str = "manual:1") -> TriggerDelivery:
    return TriggerDelivery.create(
        automation_id=automation.id,
        trigger_type=TriggerType.MANUAL,
        source="manual",
        dedupe_key=dedupe_key,
        fired_at=datetime(2026, 9, 13, 1, 1, tzinfo=UTC),
    )


class _SlowReadRepository(SqliteAutomationRepository):
    def _list_automations_sync(self) -> tuple[Automation, ...]:
        time.sleep(0.08)
        return super()._list_automations_sync()


class _BlockingWriteRepository(SqliteAutomationRepository):
    def __init__(self, path: str | Path) -> None:
        self.write_started = threading.Event()
        self.release_writes = threading.Event()
        super().__init__(path)

    def _save_automation_sync(self, automation: Automation) -> Automation:
        self.write_started.set()
        if not self.release_writes.wait(timeout=2):
            raise TimeoutError("test write release timed out")
        return super()._save_automation_sync(automation)


class _BusyRepository(SqliteAutomationRepository):
    def _list_automations_sync(self) -> tuple[Automation, ...]:
        raise sqlite3.OperationalError("database is locked")


class _RecordingRepository(SqliteAutomationRepository):
    def __init__(self, path: str | Path) -> None:
        self.connection_threads: list[int] = []
        super().__init__(path)
        self.connection_threads.clear()

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        return super()._connect()


class _ConcurrencyRepository(SqliteAutomationRepository):
    def __init__(self, path: str | Path, *, max_concurrency: int) -> None:
        self._counter_lock = threading.Lock()
        self.active_reads = 0
        self.max_active_reads = 0
        super().__init__(path, max_concurrency=max_concurrency)

    def _list_automations_sync(self) -> tuple[Automation, ...]:
        with self._counter_lock:
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
        try:
            time.sleep(0.04)
            return super()._list_automations_sync()
        finally:
            with self._counter_lock:
                self.active_reads -= 1


class _BusyRuntimeState(SqliteAutomationRuntimeState):
    def _has_processed_event_sync(self, event_id: str) -> bool:
        del event_id
        raise sqlite3.OperationalError("database is busy")


def test_automation_sqlite_read_does_not_block_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _SlowReadRepository(tmp_path / "automation.sqlite3")
        pending = asyncio.create_task(repository.list_automations())

        await asyncio.sleep(0.01)

        assert not pending.done()
        assert await pending == ()

    asyncio.run(scenario())


def test_automation_sqlite_repeated_cancellation_waits_for_write_boundary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "automation.sqlite3"
        repository = _BlockingWriteRepository(database)
        automation = _automation()
        pending = asyncio.create_task(repository.save_automation(automation))

        assert await asyncio.to_thread(repository.write_started.wait, 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)

        assert not pending.done()

        repository.release_writes.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        recovered = SqliteAutomationRepository(database)
        assert await recovered.get_automation(automation.id) == automation

    asyncio.run(scenario())


def test_automation_sqlite_waiting_writers_do_not_starve_reads(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _BlockingWriteRepository(tmp_path / "automation.sqlite3")
        automations = tuple(_automation(f"queued-{index}") for index in range(5))
        writes = tuple(
            asyncio.create_task(repository.save_automation(automation))
            for automation in automations
        )

        assert await asyncio.to_thread(repository.write_started.wait, 1)
        read = asyncio.create_task(repository.list_automations())
        try:
            done, _ = await asyncio.wait({read}, timeout=1.0)
            assert read in done
            assert read.result() == ()
        finally:
            repository.release_writes.set()
            await asyncio.gather(*writes)
            if not read.done():
                await read

    asyncio.run(scenario())


def test_automation_sqlite_maps_busy_to_retryable_transient_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = _BusyRepository(tmp_path / "automation.sqlite3")

        with pytest.raises(ContractError) as raised:
            await repository.list_automations()

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_automation_sqlite_connections_are_opened_in_worker_threads(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = _RecordingRepository(tmp_path / "automation.sqlite3")
        event_loop_thread = threading.get_ident()

        assert await repository.list_automations() == ()

        assert repository.connection_threads
        assert all(
            thread_id != event_loop_thread for thread_id in repository.connection_threads
        )

    asyncio.run(scenario())


def test_automation_sqlite_offload_concurrency_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _ConcurrencyRepository(
            tmp_path / "automation.sqlite3",
            max_concurrency=3,
        )

        results = await asyncio.gather(
            *(repository.list_automations() for _ in range(12))
        )

        assert results == [()] * 12
        assert 1 < repository.max_active_reads <= 3

    asyncio.run(scenario())


def test_automation_repository_preserves_restart_and_dedupe_semantics(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "automation.sqlite3"
        automation = _automation()
        first_delivery = _delivery(automation)
        duplicate_delivery = _delivery(automation)

        first = SqliteAutomationRepository(database)
        await first.save_automation(automation)
        assert await first.save_delivery(first_delivery) == first_delivery
        deduplicated = await first.save_delivery(duplicate_delivery)

        assert deduplicated.id == first_delivery.id

        recovered = SqliteAutomationRepository(database)
        assert await recovered.get_automation(automation.id) == automation
        assert await recovered.get_delivery(first_delivery.id) == first_delivery
        assert (
            await recovered.find_delivery_by_dedupe(
                automation.id,
                first_delivery.dedupe_key,
            )
            == first_delivery
        )
        assert await recovered.list_automations() == (automation,)
        assert await recovered.list_deliveries(automation.id) == (first_delivery,)

    asyncio.run(scenario())


async def _assert_runtime_state_contract(state: AutomationRuntimeState) -> None:
    record = AutomationCommandRecord(
        principal_ref="user:automation-test",
        idempotency_key="command-1",
        command="pause",
        resource_ref="automation:test",
        payload_digest="digest",
        result={"status": "paused"},
    )

    assert (
        await state.get_command(record.principal_ref, record.idempotency_key) is None
    )
    assert await state.save_command(record) == record
    assert await state.save_command(record) == record
    assert (
        await state.get_command(record.principal_ref, record.idempotency_key) == record
    )
    assert await state.has_processed_event("event-1") is False
    await state.mark_processed_event("event-1")
    assert await state.has_processed_event("event-1") is True
    await state.append_audit_event({"type": "test", "sequence": 1})
    assert await state.list_audit_events() == ({"type": "test", "sequence": 1},)


def test_automation_runtime_state_in_memory_and_sqlite_share_contract(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        await _assert_runtime_state_contract(InMemoryAutomationRuntimeState())
        await _assert_runtime_state_contract(
            SqliteAutomationRuntimeState(tmp_path / "runtime.sqlite3")
        )

    asyncio.run(scenario())


def test_automation_runtime_state_maps_busy_to_retryable_transient_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        state = _BusyRuntimeState(tmp_path / "runtime.sqlite3")

        with pytest.raises(ContractError) as raised:
            await state.has_processed_event("event-1")

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())
