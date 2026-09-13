"""Awaitable Notification preference persistence adapters."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError

from . import sqlite as _legacy_notifications
from ._sqlite_async import AsyncSqliteOffload, map_sqlite_error
from .models import NotificationPreference, RecipientRef
from .sqlite import SqliteNotificationPreferenceRepository as _SyncPreferenceRepository


class SqliteNotificationPreferenceRepository:
    """Async SQLite preference store with worker-owned runtime connections."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        # Directory/schema setup is explicit synchronous deployment work. Runtime reads/writes
        # below never call sqlite3 on the asyncio event-loop thread.
        self._sync = _SyncPreferenceRepository(db_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    def _connect(self) -> sqlite3.Connection:
        return self._sync._connect()

    async def _run_sqlite[T](
        self,
        operation: Callable[[], T],
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

    async def get(self, recipient: RecipientRef) -> NotificationPreference:
        def operation() -> NotificationPreference:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload FROM notification_preferences
                    WHERE recipient_type = ? AND recipient_id = ?
                    """,
                    (recipient.type.value, recipient.id),
                ).fetchone()
            if row is None:
                return NotificationPreference(recipient=recipient)
            return _legacy_notifications._decode_preference(cast(str, row["payload"]))

        return await self._run_sqlite(
            operation,
            message="failed to read notification preferences",
        )

    async def save(self, preference: NotificationPreference) -> NotificationPreference:
        encoded = json.dumps(
            _legacy_notifications._preference_json(preference),
            sort_keys=True,
            separators=(",", ":"),
        )

        def operation() -> NotificationPreference:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO notification_preferences(recipient_type, recipient_id, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT(recipient_type, recipient_id)
                    DO UPDATE SET payload = excluded.payload
                    """,
                    (
                        preference.recipient.type.value,
                        preference.recipient.id,
                        encoded,
                    ),
                )
            return preference

        return await self._run_sqlite(
            operation,
            message="failed to persist notification preferences",
            write=True,
        )


__all__ = ["SqliteNotificationPreferenceRepository"]
