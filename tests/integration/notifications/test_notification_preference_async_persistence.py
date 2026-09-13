"""Async Notification preference persistence regressions for issue #892."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    InMemoryNotificationPreferenceRepository,
    NotificationPreference,
    RecipientRef,
    RecipientType,
    SqliteNotificationPreferenceRepository,
)


def _recipient() -> RecipientRef:
    return RecipientRef(RecipientType.USER, new_id("user"))


class _RecordingPreferenceRepository(SqliteNotificationPreferenceRepository):
    def __init__(self, db_path: Path, *, max_concurrency: int = 4) -> None:
        self.connection_threads: list[int] = []
        self.delay_connections = False
        super().__init__(db_path, max_concurrency=max_concurrency)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        if self.delay_connections:
            time.sleep(0.06)
        return super()._connect()


class _BoundedPreferenceRepository(SqliteNotificationPreferenceRepository):
    def __init__(self, db_path: Path, *, max_concurrency: int) -> None:
        self._counter_lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        super().__init__(db_path, max_concurrency=max_concurrency)

    def _connect(self) -> sqlite3.Connection:
        with self._counter_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.04)
            return super()._connect()
        finally:
            with self._counter_lock:
                self.active -= 1


class _BlockingPreferenceRepository(SqliteNotificationPreferenceRepository):
    def __init__(self, db_path: Path) -> None:
        self.block = False
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(db_path)
        self.block = True

    def _connect(self) -> sqlite3.Connection:
        if self.block:
            self.started.set()
            if not self.release.wait(timeout=2):
                raise TimeoutError("test preference worker release timed out")
        return super()._connect()


class _BusyPreferenceRepository(SqliteNotificationPreferenceRepository):
    def _connect(self) -> sqlite3.Connection:
        raise sqlite3.OperationalError("database is locked")


def test_preference_sqlite_runtime_is_responsive_and_worker_owned(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _RecordingPreferenceRepository(tmp_path / "notifications.sqlite3")
        event_loop_thread = threading.get_ident()
        pending = asyncio.create_task(repository.get(_recipient()))

        await asyncio.sleep(0.01)

        assert not pending.done()
        await pending
        assert repository.connection_threads
        assert all(thread_id != event_loop_thread for thread_id in repository.connection_threads)

    asyncio.run(scenario())


def test_preference_sqlite_runtime_concurrency_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _BoundedPreferenceRepository(
            tmp_path / "notifications.sqlite3",
            max_concurrency=2,
        )
        recipient = _recipient()

        await asyncio.gather(*(repository.get(recipient) for _ in range(8)))

        assert 1 < repository.max_active <= 2

    asyncio.run(scenario())


def test_preference_sqlite_runtime_survives_multiple_event_loop_lifetimes(tmp_path: Path) -> None:
    repository = _BoundedPreferenceRepository(
        tmp_path / "notifications.sqlite3",
        max_concurrency=1,
    )
    recipient = _recipient()

    async def contended_batch() -> None:
        preferences = await asyncio.gather(*(repository.get(recipient) for _ in range(3)))
        assert all(preference.recipient == recipient for preference in preferences)

    asyncio.run(contended_batch())
    asyncio.run(contended_batch())


def test_preference_sqlite_cancellation_waits_for_write_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "notifications.sqlite3"
        repository = _BlockingPreferenceRepository(database)
        preference = NotificationPreference(recipient=_recipient(), muted=True)
        pending = asyncio.create_task(repository.save(preference))
        assert await asyncio.to_thread(repository.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()

        repository.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        restarted = SqliteNotificationPreferenceRepository(database)
        assert await restarted.get(preference.recipient) == preference

    asyncio.run(scenario())


def test_preference_sqlite_busy_maps_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _BusyPreferenceRepository(tmp_path / "notifications.sqlite3")

        with pytest.raises(ContractError) as raised:
            await repository.get(_recipient())

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_preference_repository_restart_and_in_memory_parity(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "notifications.sqlite3"
        recipient = _recipient()
        preference = NotificationPreference(
            recipient=recipient,
            muted=True,
            aggregate_duplicates=False,
        )

        memory = InMemoryNotificationPreferenceRepository()
        sqlite = SqliteNotificationPreferenceRepository(database)
        assert await memory.save(preference) == preference
        assert await sqlite.save(preference) == preference
        assert await memory.get(recipient) == preference
        assert await sqlite.get(recipient) == preference

        restarted = SqliteNotificationPreferenceRepository(database)
        assert await restarted.get(recipient) == preference

    asyncio.run(scenario())
