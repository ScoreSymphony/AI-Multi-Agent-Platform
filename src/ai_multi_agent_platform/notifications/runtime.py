"""Autonomous restart-safe notification projection runtime for issue #75."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.kernel.repository import EventRepository

from .models import Notification
from .service import NotificationService

ReminderEvaluator = Callable[[], Awaitable[tuple[Notification, ...]]]


class NotificationRuntimeState(Protocol):
    """Persist which canonical Events have already been projected into attention."""

    async def processed(self, event_id: str) -> bool: ...

    async def mark_processed(self, event_id: str, *, event_type: str) -> None: ...


class InMemoryNotificationRuntimeState:
    def __init__(self) -> None:
        self._processed: set[str] = set()

    async def processed(self, event_id: str) -> bool:
        validate_id(event_id, "event")
        return event_id in self._processed

    async def mark_processed(self, event_id: str, *, event_type: str) -> None:
        validate_id(event_id, "event")
        if not event_type.strip():
            raise ValueError("event_type must not be blank")
        self._processed.add(event_id)


class SqliteNotificationRuntimeState:
    """Durable event projection checkpoint sharing the notification database when desired."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_processed_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    async def processed(self, event_id: str) -> bool:
        validate_id(event_id, "event")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM notification_processed_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return row is not None

    async def mark_processed(self, event_id: str, *, event_type: str) -> None:
        validate_id(event_id, "event")
        if not event_type.strip():
            raise ValueError("event_type must not be blank")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO notification_processed_events(
                    event_id, event_type, processed_at
                )
                VALUES (?, ?, ?)
                """,
                (event_id, event_type, datetime.now(UTC).isoformat()),
            )


@dataclass(frozen=True, slots=True)
class NotificationRuntimeTick:
    examined_events: int = 0
    projected_notifications: int = 0
    failed_events: int = 0
    reminder_notifications: int = 0
    reminder_failed: bool = False


class NotificationRuntime:
    """Continuously project canonical Events and #88 reminder state into Notifications.

    The runtime never owns source lifecycle state. Canonical Events remain in ``EventRepository``;
    the runtime state only checkpoints successful projection so restarts do not inflate duplicate
    notification aggregation counts. Projection failures remain retryable on the next tick and do
    not block later independent Events.
    """

    def __init__(
        self,
        *,
        events: EventRepository,
        notifications: NotificationService,
        state: NotificationRuntimeState | None = None,
        reminder_evaluator: ReminderEvaluator | None = None,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("notification poll interval must be positive")
        self._events = events
        self._notifications = notifications
        self._state = state or InMemoryNotificationRuntimeState()
        self._reminder_evaluator = reminder_evaluator
        self._poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def state(self) -> NotificationRuntimeState:
        return self._state

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="notification-runtime")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        await task
        self._task = None

    async def run_once(self) -> NotificationRuntimeTick:
        examined = 0
        projected = 0
        failed = 0
        for stream_id in await self._events.list_stream_ids():
            for event in await self._events.read_events(stream_id):
                if await self._state.processed(event.id):
                    continue
                examined += 1
                try:
                    projected += len(await self._notifications.project_event(event))
                except Exception:
                    failed += 1
                    continue
                await self._state.mark_processed(event.id, event_type=event.event_type)

        reminder_count = 0
        reminder_failed = False
        if self._reminder_evaluator is not None:
            try:
                reminder_count = len(await self._reminder_evaluator())
            except Exception:
                reminder_failed = True

        return NotificationRuntimeTick(
            examined_events=examined,
            projected_notifications=projected,
            failed_events=failed,
            reminder_notifications=reminder_count,
            reminder_failed=reminder_failed,
        )

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue
