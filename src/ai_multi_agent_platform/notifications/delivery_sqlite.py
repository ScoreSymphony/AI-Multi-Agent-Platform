"""Restart-safe SQLite delivery-attempt storage for canonical notifications."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .delivery import DeliveryAttempt, DeliveryAttemptRepository, DeliveryStatus
from .models import RecipientRef, RecipientType


class SqliteDeliveryAttemptRepository(DeliveryAttemptRepository):
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
                    id TEXT PRIMARY KEY,
                    notification_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    attempted_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notification_delivery_lookup
                ON notification_delivery_attempts(notification_id, channel, attempt)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    async def save(self, attempt: DeliveryAttempt) -> DeliveryAttempt:
        payload = json.dumps(_encode(attempt), sort_keys=True, separators=(",", ":"))
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

    async def latest(self, notification_id: str, channel: str) -> DeliveryAttempt | None:
        validate_id(notification_id, "notification")
        if not channel.strip():
            raise ValueError("channel must not be blank")
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
        return None if row is None else _decode(cast(str, row["payload"]))

    async def list_for_notification(self, notification_id: str) -> tuple[DeliveryAttempt, ...]:
        validate_id(notification_id, "notification")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM notification_delivery_attempts
                WHERE notification_id = ?
                ORDER BY channel, attempt, attempted_at, id
                """,
                (notification_id,),
            ).fetchall()
        return tuple(_decode(cast(str, row["payload"])) for row in rows)


def _encode(attempt: DeliveryAttempt) -> dict[str, JsonValue]:
    return {
        "id": attempt.id,
        "notification_id": attempt.notification_id,
        "recipient_type": attempt.recipient.type.value,
        "recipient_id": attempt.recipient.id,
        "channel": attempt.channel,
        "idempotency_key": attempt.idempotency_key,
        "attempt": attempt.attempt,
        "status": attempt.status.value,
        "attempted_at": attempt.attempted_at.isoformat(),
        "provider_reference": attempt.provider_reference,
        "retry_after_seconds": attempt.retry_after_seconds,
        "metadata": dict(attempt.metadata),
    }


def _decode(payload: str) -> DeliveryAttempt:
    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise ValueError("delivery-attempt payload must be an object")
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("delivery-attempt metadata must be an object")
    from datetime import datetime

    return DeliveryAttempt(
        id=_required_string(raw, "id"),
        notification_id=_required_string(raw, "notification_id"),
        recipient=RecipientRef(
            RecipientType(_required_string(raw, "recipient_type")),
            _required_string(raw, "recipient_id"),
        ),
        channel=_required_string(raw, "channel"),
        idempotency_key=_required_string(raw, "idempotency_key"),
        attempt=_required_int(raw, "attempt"),
        status=DeliveryStatus(_required_string(raw, "status")),
        attempted_at=datetime.fromisoformat(_required_string(raw, "attempted_at")),
        provider_reference=_optional_string(raw, "provider_reference"),
        retry_after_seconds=_optional_int(raw, "retry_after_seconds"),
        metadata=cast(dict[str, JsonValue], metadata),
    )


def _required_string(raw: dict[str, object], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_string(raw: dict[str, object], name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string when provided")
    return value


def _required_int(raw: dict[str, object], name: str) -> int:
    value = raw.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_int(raw: dict[str, object], name: str) -> int | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer when provided")
    return value
