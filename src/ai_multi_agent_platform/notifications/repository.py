"""Repository contracts and in-memory reference storage for notifications."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol

from .models import Notification, NotificationQuery, NotificationState, RecipientRef


class NotificationRepository(Protocol):
    def save(self, notification: Notification) -> Notification: ...

    def get(self, notification_id: str) -> Notification | None: ...

    def list(self, query: NotificationQuery) -> tuple[Notification, ...]: ...

    def find_active_aggregate(
        self,
        *,
        recipient: RecipientRef,
        aggregation_key: str,
    ) -> Notification | None: ...

    def count_unread(self, recipient: RecipientRef) -> int: ...


class InMemoryNotificationRepository:
    """Deterministic thread-safe baseline repository.

    Production persistence remains replaceable; this implementation exists for the local
    baseline and contract tests.
    """

    def __init__(self) -> None:
        self._items: dict[str, Notification] = {}
        self._lock = RLock()

    def save(self, notification: Notification) -> Notification:
        with self._lock:
            self._items[notification.id] = notification
            return notification

    def get(self, notification_id: str) -> Notification | None:
        with self._lock:
            return self._items.get(notification_id)

    def list(self, query: NotificationQuery) -> tuple[Notification, ...]:
        now = datetime.now(UTC)
        with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.recipient == query.recipient
                and (item.expires_at is None or item.expires_at > now)
                and (query.category is None or item.category is query.category)
                and (query.severity is None or item.severity is query.severity)
                and (query.project_id is None or item.project_id == query.project_id)
                and (not query.unread_only or item.state is NotificationState.UNREAD)
                and (query.include_archived or item.state is not NotificationState.ARCHIVED)
            ]
            items.sort(key=lambda item: (item.updated_at, item.created_at, item.id), reverse=True)
            return tuple(items[query.offset : query.offset + query.limit])

    def find_active_aggregate(
        self,
        *,
        recipient: RecipientRef,
        aggregation_key: str,
    ) -> Notification | None:
        now = datetime.now(UTC)
        with self._lock:
            candidates = [
                item
                for item in self._items.values()
                if item.recipient == recipient
                and item.aggregation_key == aggregation_key
                and item.state not in {NotificationState.DISMISSED, NotificationState.ARCHIVED}
                and (item.expires_at is None or item.expires_at > now)
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda item: (item.updated_at, item.created_at, item.id))

    def count_unread(self, recipient: RecipientRef) -> int:
        now = datetime.now(UTC)
        with self._lock:
            return sum(
                1
                for item in self._items.values()
                if item.recipient == recipient
                and item.state is NotificationState.UNREAD
                and (item.expires_at is None or item.expires_at > now)
            )

    def mark_all_read(self, recipient: RecipientRef, *, at: datetime | None = None) -> tuple[Notification, ...]:
        current = at or datetime.now(UTC)
        updated: list[Notification] = []
        with self._lock:
            for notification_id, item in tuple(self._items.items()):
                if item.recipient != recipient or item.state is not NotificationState.UNREAD:
                    continue
                next_item = replace(
                    item,
                    state=NotificationState.READ,
                    read_at=current,
                    updated_at=current,
                )
                self._items[notification_id] = next_item
                updated.append(next_item)
        return tuple(updated)
