"""Non-blocking wrappers for Notification-owned synchronous SQLite adapters."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import validate_id

from . import delivery_sqlite as _legacy_delivery
from . import sqlite as _legacy_notifications
from ._sqlite_async import AsyncSqliteOffload, map_sqlite_error
from .delivery import DeliveryAttempt
from .delivery_sqlite import SqliteDeliveryAttemptRepository as _SyncDeliveryAttemptRepository
from .models import Notification, NotificationQuery, NotificationState, RecipientRef
from .runtime import SqliteNotificationRuntimeState as _SyncNotificationRuntimeState
from .sqlite import SqliteNotificationRepository as _SyncNotificationRepository


class SqliteNotificationRepository(_SyncNotificationRepository):
    """SQLite notification repository whose awaitable operations never run on the event loop."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        super().__init__(db_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    async def _run_sqlite[T](
        self,
        operation: callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._async_sqlite.run(operation, write=write)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise map_sqlite_error(exc, message) from exc

    async def save(self, notification: Notification) -> Notification:
        encoded = _legacy_notifications._encode_notification(notification)

        def operation() -> Notification:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO notifications(
                        id, recipient_type, recipient_id, aggregation_key,
                        state, expires_at, updated_at, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        recipient_type = excluded.recipient_type,
                        recipient_id = excluded.recipient_id,
                        aggregation_key = excluded.aggregation_key,
                        state = excluded.state,
                        expires_at = excluded.expires_at,
                        updated_at = excluded.updated_at,
                        payload = excluded.payload
                    """,
                    _legacy_notifications._row_values(notification, encoded),
                )
            return notification

        return await self._run_sqlite(
            operation,
            message="failed to persist notification",
            write=True,
        )

    async def get(self, notification_id: str) -> Notification:
        validate_id(notification_id, "notification")

        def operation() -> str | None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM notifications WHERE id = ?",
                    (notification_id,),
                ).fetchone()
            return None if row is None else cast(str, row["payload"])

        encoded = await self._run_sqlite(operation, message="failed to read notification")
        if encoded is None:
            from ai_multi_agent_platform.contracts import ErrorCode

            raise ContractError(ErrorCode.NOT_FOUND, "notification not found")
        return _legacy_notifications._decode_notification(encoded)

    async def list(self, query: NotificationQuery) -> tuple[Notification, ...]:
        def operation() -> tuple[str, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload FROM notifications
                    WHERE recipient_type = ? AND recipient_id = ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (query.recipient.type.value, query.recipient.id),
                ).fetchall()
            return tuple(cast(str, row["payload"]) for row in rows)

        encoded = await self._run_sqlite(operation, message="failed to list notifications")
        now = datetime.now(UTC)
        items = [
            item
            for payload in encoded
            if _legacy_notifications._matches_query(
                item := _legacy_notifications._decode_notification(payload), query, now
            )
        ]
        if query.limit is None:
            return tuple(items[query.offset :])
        return tuple(items[query.offset : query.offset + query.limit])

    async def list_all(self) -> tuple[Notification, ...]:
        def operation() -> tuple[str, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload FROM notifications
                    ORDER BY updated_at DESC, id DESC
                    """
                ).fetchall()
            return tuple(cast(str, row["payload"]) for row in rows)

        encoded = await self._run_sqlite(operation, message="failed to enumerate notifications")
        return tuple(_legacy_notifications._decode_notification(payload) for payload in encoded)

    async def find_active_aggregate(
        self,
        *,
        recipient: RecipientRef,
        aggregation_key: str,
    ) -> Notification | None:
        if not aggregation_key.strip():
            raise ValueError("aggregation_key must not be blank")

        def operation() -> tuple[str, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload FROM notifications
                    WHERE recipient_type = ? AND recipient_id = ? AND aggregation_key = ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (recipient.type.value, recipient.id, aggregation_key),
                ).fetchall()
            return tuple(cast(str, row["payload"]) for row in rows)

        encoded = await self._run_sqlite(
            operation,
            message="failed to query notification aggregation",
        )
        now = datetime.now(UTC)
        for payload in encoded:
            item = _legacy_notifications._decode_notification(payload)
            if item.state in {NotificationState.DISMISSED, NotificationState.ARCHIVED}:
                continue
            if item.expires_at is not None and item.expires_at <= now:
                continue
            return item
        return None

    async def count_unread(self, recipient: RecipientRef) -> int:
        now = datetime.now(UTC).isoformat()

        def operation() -> int:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM notifications
                    WHERE recipient_type = ? AND recipient_id = ?
                      AND state = ?
                      AND (expires_at IS NULL OR expires_at > ?)
                    """,
                    (
                        recipient.type.value,
                        recipient.id,
                        NotificationState.UNREAD.value,
                        now,
                    ),
                ).fetchone()
            return 0 if row is None else int(row["count"])

        return await self._run_sqlite(operation, message="failed to count unread notifications")

    async def mark_all_read(
        self,
        recipient: RecipientRef,
        *,
        at: datetime | None = None,
    ) -> tuple[Notification, ...]:
        current = at or datetime.now(UTC)
        if current.utcoffset() is None:
            raise ValueError("at must be timezone-aware")

        def operation() -> tuple[Notification, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload FROM notifications
                    WHERE recipient_type = ? AND recipient_id = ? AND state = ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (
                        recipient.type.value,
                        recipient.id,
                        NotificationState.UNREAD.value,
                    ),
                ).fetchall()
                updated: list[Notification] = []
                for row in rows:
                    item = _legacy_notifications._decode_notification(cast(str, row["payload"]))
                    if item.expires_at is not None and item.expires_at <= current:
                        continue
                    next_item = replace(
                        item,
                        state=NotificationState.READ,
                        read_at=current,
                        updated_at=current,
                    )
                    connection.execute(
                        """
                        UPDATE notifications
                        SET state = ?, updated_at = ?, payload = ?
                        WHERE id = ?
                        """,
                        (
                            next_item.state.value,
                            next_item.updated_at.isoformat(),
                            _legacy_notifications._encode_notification(next_item),
                            next_item.id,
                        ),
                    )
                    updated.append(next_item)
            return tuple(updated)

        return await self._run_sqlite(
            operation,
            message="failed to mark notifications read",
            write=True,
        )


class SqliteDeliveryAttemptRepository(_SyncDeliveryAttemptRepository):
    """SQLite delivery history with bounded event-loop-safe offload."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        super().__init__(db_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    async def _run_sqlite[T](
        self,
        operation: callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._async_sqlite.run(operation, write=write)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise map_sqlite_error(exc, message) from exc

    async def save(self, attempt: DeliveryAttempt) -> DeliveryAttempt:
        import json

        payload = json.dumps(
            _legacy_delivery._encode(attempt),
            sort_keys=True,
            separators=(",", ":"),
        )

        def operation() -> DeliveryAttempt:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO notification_delivery_attempts(
                        id, notification_id, channel, attempt, attempted_at, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        notification_id = excluded.notification_id,
                        channel = excluded.channel,
                        attempt = excluded.attempt,
                        attempted_at = excluded.attempted_at,
                        payload = excluded.payload
                    """,
                    (
                        attempt.id,
                        attempt.notification_id,
                        attempt.channel,
                        attempt.attempt,
                        attempt.attempted_at.isoformat(),
                        payload,
                    ),
                )
            return attempt

        return await self._run_sqlite(
            operation,
            message="failed to persist notification delivery attempt",
            write=True,
        )

    async def latest(self, notification_id: str, channel: str) -> DeliveryAttempt | None:
        validate_id(notification_id, "notification")
        if not channel.strip():
            raise ValueError("channel must not be blank")

        def operation() -> str | None:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload FROM notification_delivery_attempts
                    WHERE notification_id = ? AND channel = ?
                    ORDER BY attempt DESC, attempted_at DESC, id DESC
                    LIMIT 1
                    """,
                    (notification_id, channel),
                ).fetchone()
            return None if row is None else cast(str, row["payload"])

        payload = await self._run_sqlite(
            operation,
            message="failed to read notification delivery attempt",
        )
        return None if payload is None else _legacy_delivery._decode(payload)

    async def list_for_notification(self, notification_id: str) -> tuple[DeliveryAttempt, ...]:
        validate_id(notification_id, "notification")

        def operation() -> tuple[str, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload FROM notification_delivery_attempts
                    WHERE notification_id = ?
                    ORDER BY channel, attempt, attempted_at, id
                    """,
                    (notification_id,),
                ).fetchall()
            return tuple(cast(str, row["payload"]) for row in rows)

        payloads = await self._run_sqlite(
            operation,
            message="failed to list notification delivery attempts",
        )
        return tuple(_legacy_delivery._decode(payload) for payload in payloads)


class SqliteNotificationRuntimeState(_SyncNotificationRuntimeState):
    """Durable notification event cursor with bounded event-loop-safe SQLite offload."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        super().__init__(db_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    async def _run_sqlite[T](
        self,
        operation: callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._async_sqlite.run(operation, write=write)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise map_sqlite_error(exc, message) from exc

    async def processed(self, event_id: str) -> bool:
        validate_id(event_id, "event")

        def operation() -> bool:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM notification_processed_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            return row is not None

        return await self._run_sqlite(operation, message="failed to read notification event cursor")

    async def mark_processed(self, event_id: str, *, event_type: str) -> None:
        validate_id(event_id, "event")
        if not event_type.strip():
            raise ValueError("event_type must not be blank")
        processed_at = datetime.now(UTC).isoformat()

        def operation() -> None:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_processed_events(
                        event_id, event_type, processed_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (event_id, event_type, processed_at),
                )

        await self._run_sqlite(
            operation,
            message="failed to persist notification event cursor",
            write=True,
        )


__all__ = [
    "SqliteDeliveryAttemptRepository",
    "SqliteNotificationRepository",
    "SqliteNotificationRuntimeState",
]
