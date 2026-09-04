"""Restart-safe SQLite reference persistence for canonical notifications."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import (
    Notification,
    NotificationAction,
    NotificationCategory,
    NotificationPreference,
    NotificationQuery,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
    RecipientType,
    SourceRef,
)
from .preferences import NotificationPreferenceRepository
from .repository import NotificationRepository


class SqliteNotificationRepository(NotificationRepository):
    """Reference durable inbox store without making SQLite a canonical requirement."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notifications (
                        id TEXT PRIMARY KEY,
                        recipient_type TEXT NOT NULL,
                        recipient_id TEXT NOT NULL,
                        aggregation_key TEXT,
                        state TEXT NOT NULL,
                        expires_at TEXT,
                        updated_at TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_notifications_recipient
                    ON notifications(recipient_type, recipient_id, updated_at)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_notifications_aggregate
                    ON notifications(recipient_type, recipient_id, aggregation_key)
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize notification storage",
            ) from exc

    async def save(self, notification: Notification) -> Notification:
        encoded = _encode_notification(notification)
        try:
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
                    _row_values(notification, encoded),
                )
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist notification") from exc
        return notification

    async def get(self, notification_id: str) -> Notification:
        validate_id(notification_id, "notification")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM notifications WHERE id = ?",
                    (notification_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read notification") from exc
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "notification not found")
        return _decode_notification(cast(str, row["payload"]))

    async def list(self, query: NotificationQuery) -> tuple[Notification, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload FROM notifications
                    WHERE recipient_type = ? AND recipient_id = ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (query.recipient.type.value, query.recipient.id),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to list notifications") from exc
        now = datetime.now(UTC)
        items = [
            item
            for row in rows
            if _matches_query(item := _decode_notification(cast(str, row["payload"])), query, now)
        ]
        if query.limit is None:
            return tuple(items[query.offset :])
        return tuple(items[query.offset : query.offset + query.limit])

    async def find_active_aggregate(
        self,
        *,
        recipient: RecipientRef,
        aggregation_key: str,
    ) -> Notification | None:
        if not aggregation_key.strip():
            raise ValueError("aggregation_key must not be blank")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload FROM notifications
                    WHERE recipient_type = ? AND recipient_id = ? AND aggregation_key = ?
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (recipient.type.value, recipient.id, aggregation_key),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to query notification aggregation",
            ) from exc
        now = datetime.now(UTC)
        for row in rows:
            item = _decode_notification(cast(str, row["payload"]))
            if item.state in {NotificationState.DISMISSED, NotificationState.ARCHIVED}:
                continue
            if item.expires_at is not None and item.expires_at <= now:
                continue
            return item
        return None

    async def count_unread(self, recipient: RecipientRef) -> int:
        now = datetime.now(UTC).isoformat()
        try:
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
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to count unread notifications",
            ) from exc
        return 0 if row is None else int(row["count"])

    async def mark_all_read(
        self,
        recipient: RecipientRef,
        *,
        at: datetime | None = None,
    ) -> tuple[Notification, ...]:
        current = at or datetime.now(UTC)
        if current.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        try:
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
                    item = _decode_notification(cast(str, row["payload"]))
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
                            _encode_notification(next_item),
                            next_item.id,
                        ),
                    )
                    updated.append(next_item)
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to mark notifications read",
            ) from exc
        return tuple(updated)


class SqliteNotificationPreferenceRepository(NotificationPreferenceRepository):
    """Durable per-recipient preference store sharing the notification database if desired."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
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
        return _decode_preference(cast(str, row["payload"]))

    def save(self, preference: NotificationPreference) -> NotificationPreference:
        encoded = json.dumps(
            _preference_json(preference),
            sort_keys=True,
            separators=(",", ":"),
        )
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


def _matches_query(
    item: Notification,
    query: NotificationQuery,
    now: datetime,
) -> bool:
    return (
        (item.expires_at is None or item.expires_at > now)
        and (query.category is None or item.category is query.category)
        and (query.severity is None or item.severity is query.severity)
        and (query.project_id is None or item.project_id == query.project_id)
        and (not query.unread_only or item.state is NotificationState.UNREAD)
        and (query.include_archived or item.state is not NotificationState.ARCHIVED)
    )


def _row_values(notification: Notification, encoded: str) -> tuple[str | None, ...]:
    return (
        notification.id,
        notification.recipient.type.value,
        notification.recipient.id,
        notification.aggregation_key,
        notification.state.value,
        None if notification.expires_at is None else notification.expires_at.isoformat(),
        notification.updated_at.isoformat(),
        encoded,
    )


def _encode_notification(notification: Notification) -> str:
    return json.dumps(
        _notification_json(notification),
        sort_keys=True,
        separators=(",", ":"),
    )


def _notification_json(notification: Notification) -> dict[str, JsonValue]:
    resource_ref: JsonValue = None
    if notification.resource_ref is not None:
        resource_ref = {
            "resource_type": notification.resource_ref.resource_type,
            "resource_id": notification.resource_ref.resource_id,
        }
    return {
        "id": notification.id,
        "category": notification.category.value,
        "severity": notification.severity.value,
        "title": notification.title,
        "summary": dict(notification.summary),
        "recipient": {
            "type": notification.recipient.type.value,
            "id": notification.recipient.id,
        },
        "source": {
            "resource_type": notification.source.resource_type,
            "resource_id": notification.source.resource_id,
        },
        "state": notification.state.value,
        "project_id": notification.project_id,
        "workspace_id": notification.workspace_id,
        "task_id": notification.task_id,
        "run_id": notification.run_id,
        "approval_id": notification.approval_id,
        "verification_id": notification.verification_id,
        "node_id": notification.node_id,
        "automation_id": notification.automation_id,
        "membership_id": notification.membership_id,
        "resource_ref": resource_ref,
        "actions": [
            {
                "action_id": action.action_id,
                "label": action.label,
                "command": action.command,
                "resource_type": action.resource_type,
                "resource_id": action.resource_id,
                "href": action.href,
            }
            for action in notification.actions
        ],
        "aggregation_key": notification.aggregation_key,
        "occurrence_count": notification.occurrence_count,
        "created_at": notification.created_at.isoformat(),
        "updated_at": notification.updated_at.isoformat(),
        "read_at": _optional_time_json(notification.read_at),
        "acknowledged_at": _optional_time_json(notification.acknowledged_at),
        "dismissed_at": _optional_time_json(notification.dismissed_at),
        "archived_at": _optional_time_json(notification.archived_at),
        "expires_at": _optional_time_json(notification.expires_at),
        "correlation_id": notification.correlation_id,
        "causation_id": notification.causation_id,
        "delivery_metadata": dict(notification.delivery_metadata),
    }


def _decode_notification(encoded: str) -> Notification:
    raw = cast(dict[str, Any], json.loads(encoded))
    recipient = _required_object(raw, "recipient")
    source = _required_object(raw, "source")
    raw_resource = raw.get("resource_ref")
    resource_ref = None
    if isinstance(raw_resource, dict):
        resource_ref = SourceRef(
            resource_type=_required_string(raw_resource, "resource_type"),
            resource_id=_required_string(raw_resource, "resource_id"),
        )
    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid notification actions")
    actions = tuple(_decode_action(cast(dict[str, Any], item)) for item in raw_actions)
    return Notification(
        id=_required_string(raw, "id"),
        category=NotificationCategory(_required_string(raw, "category")),
        severity=NotificationSeverity(_required_string(raw, "severity")),
        title=_required_string(raw, "title"),
        summary=_json_object(raw.get("summary"), "summary"),
        recipient=RecipientRef(
            type=RecipientType(_required_string(recipient, "type")),
            id=_required_string(recipient, "id"),
        ),
        source=SourceRef(
            resource_type=_required_string(source, "resource_type"),
            resource_id=_required_string(source, "resource_id"),
        ),
        state=NotificationState(_required_string(raw, "state")),
        project_id=_optional_string(raw.get("project_id")),
        workspace_id=_optional_string(raw.get("workspace_id")),
        task_id=_optional_string(raw.get("task_id")),
        run_id=_optional_string(raw.get("run_id")),
        approval_id=_optional_string(raw.get("approval_id")),
        verification_id=_optional_string(raw.get("verification_id")),
        node_id=_optional_string(raw.get("node_id")),
        automation_id=_optional_string(raw.get("automation_id")),
        membership_id=_optional_string(raw.get("membership_id")),
        resource_ref=resource_ref,
        actions=actions,
        aggregation_key=_optional_string(raw.get("aggregation_key")),
        occurrence_count=_required_int(raw, "occurrence_count"),
        created_at=_required_time(raw, "created_at"),
        updated_at=_required_time(raw, "updated_at"),
        read_at=_optional_time(raw.get("read_at")),
        acknowledged_at=_optional_time(raw.get("acknowledged_at")),
        dismissed_at=_optional_time(raw.get("dismissed_at")),
        archived_at=_optional_time(raw.get("archived_at")),
        expires_at=_optional_time(raw.get("expires_at")),
        correlation_id=_optional_string(raw.get("correlation_id")),
        causation_id=_optional_string(raw.get("causation_id")),
        delivery_metadata=_json_object(raw.get("delivery_metadata"), "delivery_metadata"),
    )


def _decode_action(raw: dict[str, Any]) -> NotificationAction:
    return NotificationAction(
        action_id=_required_string(raw, "action_id"),
        label=_required_string(raw, "label"),
        command=_optional_string(raw.get("command")),
        resource_type=_optional_string(raw.get("resource_type")),
        resource_id=_optional_string(raw.get("resource_id")),
        href=_optional_string(raw.get("href")),
    )


def _preference_json(preference: NotificationPreference) -> dict[str, JsonValue]:
    return {
        "recipient": {
            "type": preference.recipient.type.value,
            "id": preference.recipient.id,
        },
        "enabled_categories": _json_string_list(
            sorted(item.value for item in preference.enabled_categories)
        ),
        "minimum_severity": preference.minimum_severity.value,
        "project_ids": _json_string_list(sorted(preference.project_ids)),
        "muted": preference.muted,
        "in_app_enabled": preference.in_app_enabled,
        "external_channels": _json_string_list(sorted(preference.external_channels)),
        "aggregate_duplicates": preference.aggregate_duplicates,
    }


def _decode_preference(encoded: str) -> NotificationPreference:
    raw = cast(dict[str, Any], json.loads(encoded))
    recipient = _required_object(raw, "recipient")
    return NotificationPreference(
        recipient=RecipientRef(
            type=RecipientType(_required_string(recipient, "type")),
            id=_required_string(recipient, "id"),
        ),
        enabled_categories=frozenset(
            NotificationCategory(item) for item in _required_string_list(raw, "enabled_categories")
        ),
        minimum_severity=NotificationSeverity(_required_string(raw, "minimum_severity")),
        project_ids=frozenset(_required_string_list(raw, "project_ids")),
        muted=_required_bool(raw, "muted"),
        in_app_enabled=_required_bool(raw, "in_app_enabled"),
        external_channels=frozenset(_required_string_list(raw, "external_channels")),
        aggregate_duplicates=_required_bool(raw, "aggregate_duplicates"),
    )


def _required_object(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _json_object(value: Any, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return cast(dict[str, JsonValue], value)


def _required_string(raw: dict[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid notification string")
    return value


def _required_int(raw: dict[str, Any], name: str) -> int:
    value = raw.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _required_bool(raw: dict[str, Any], name: str) -> bool:
    value = raw.get(name)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return value


def _required_string_list(raw: dict[str, Any], name: str) -> tuple[str, ...]:
    value = raw.get(name)
    if not isinstance(value, list):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
        result.append(item)
    return tuple(result)


def _required_time(raw: dict[str, Any], name: str) -> datetime:
    value = _required_string(raw, name)
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid notification {name}")
    return parsed


def _optional_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid notification timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid notification timestamp")
    return parsed


def _optional_time_json(value: datetime | None) -> JsonValue:
    return None if value is None else value.isoformat()


def _json_string_list(values: list[str]) -> list[JsonValue]:
    result: list[JsonValue] = []
    for value in values:
        result.append(value)
    return result
