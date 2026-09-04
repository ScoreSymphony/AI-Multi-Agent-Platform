"""Restart-safe SQLite storage for the complete notification preference policy."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    NotificationCategory,
    NotificationPreference,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
)
from .preferences import NotificationPreferenceRepository


class SqliteNotificationPreferenceRepository(NotificationPreferenceRepository):
    """Durable per-recipient policy including reminder timing and quiet hours."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notification_preferences (
                        recipient_type TEXT NOT NULL,
                        recipient_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        PRIMARY KEY(recipient_type, recipient_id)
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize notification preference storage",
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, recipient: RecipientRef) -> NotificationPreference:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload FROM notification_preferences
                    WHERE recipient_type = ? AND recipient_id = ?
                    """,
                    (recipient.type.value, recipient.id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read notification preferences",
            ) from exc
        if row is None:
            return NotificationPreference(recipient=recipient)
        return _decode(cast(str, row["payload"]))

    def save(self, preference: NotificationPreference) -> NotificationPreference:
        encoded = json.dumps(_encode(preference), sort_keys=True, separators=(",", ":"))
        try:
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
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist notification preferences",
            ) from exc
        return preference


def _encode(preference: NotificationPreference) -> dict[str, JsonValue]:
    return {
        "recipient": {
            "type": preference.recipient.type.value,
            "id": preference.recipient.id,
        },
        "enabled_categories": _json_strings(
            sorted(item.value for item in preference.enabled_categories)
        ),
        "minimum_severity": preference.minimum_severity.value,
        "project_ids": _json_strings(sorted(preference.project_ids)),
        "muted": preference.muted,
        "in_app_enabled": preference.in_app_enabled,
        "external_channels": _json_strings(sorted(preference.external_channels)),
        "aggregate_duplicates": preference.aggregate_duplicates,
        "deadline_reminders_enabled": preference.deadline_reminders_enabled,
        "deadline_reminder_lead_seconds": preference.deadline_reminder_lead_seconds,
        "overdue_reminders_enabled": preference.overdue_reminders_enabled,
        "quiet_hours_start": preference.quiet_hours_start,
        "quiet_hours_end": preference.quiet_hours_end,
        "quiet_hours_timezone": preference.quiet_hours_timezone,
    }


def _decode(encoded: str) -> NotificationPreference:
    raw = cast(dict[str, Any], json.loads(encoded))
    recipient = _required_object(raw, "recipient")
    return NotificationPreference(
        recipient=RecipientRef(
            RecipientType(_required_string(recipient, "type")),
            _required_string(recipient, "id"),
        ),
        enabled_categories=frozenset(
            NotificationCategory(item)
            for item in _string_list(raw.get("enabled_categories"), "enabled_categories")
        ),
        minimum_severity=NotificationSeverity(_required_string(raw, "minimum_severity")),
        project_ids=frozenset(_string_list(raw.get("project_ids"), "project_ids")),
        muted=_bool(raw, "muted", False),
        in_app_enabled=_bool(raw, "in_app_enabled", True),
        external_channels=frozenset(
            _string_list(raw.get("external_channels"), "external_channels")
        ),
        aggregate_duplicates=_bool(raw, "aggregate_duplicates", True),
        deadline_reminders_enabled=_bool(raw, "deadline_reminders_enabled", True),
        deadline_reminder_lead_seconds=_int(raw, "deadline_reminder_lead_seconds", 24 * 60 * 60),
        overdue_reminders_enabled=_bool(raw, "overdue_reminders_enabled", True),
        quiet_hours_start=_optional_string(raw.get("quiet_hours_start")),
        quiet_hours_end=_optional_string(raw.get("quiet_hours_end")),
        quiet_hours_timezone=_optional_string(raw.get("quiet_hours_timezone")),
    )


def _required_object(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _required_string(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid notification preference string")
    return value


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
        items.append(item)
    return tuple(items)


def _bool(raw: dict[str, Any], name: str, default: bool) -> bool:
    value = raw.get(name, default)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _int(raw: dict[str, Any], name: str, default: int) -> int:
    value = raw.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _json_strings(values: list[str]) -> list[JsonValue]:
    return list(values)
