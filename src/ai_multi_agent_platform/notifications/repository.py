"""Repository contracts and in-memory reference storage for notifications."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .models import Notification, NotificationQuery, NotificationState, RecipientRef


class NotificationRepository(ABC):
    @abstractmethod
    async def save(self, notification: Notification) -> Notification: ...

    @abstractmethod
    async def get(self, notification_id: str) -> Notification: ...

    @abstractmethod
    async def list(self, query: NotificationQuery) -> tuple[Notification, ...]: ...

    @abstractmethod
    async def list_all(self) -> tuple[Notification, ...]:
        """Enumerate canonical rows for internal rebuildable derived projections."""
        ...

    @abstractmethod
    async def find_active_aggregate(
        self,
        *,
        recipient: RecipientRef,
        aggregation_key: str,
    ) -> Notification | None: ...

    @abstractmethod
    async def count_unread(self, recipient: RecipientRef) -> int: ...

    @abstractmethod
    async def mark_all_read(
        self,
        recipient: RecipientRef,
        *,
        at: datetime | None = None,
    ) -> tuple[Notification, ...]: ...


class InMemoryNotificationRepository(NotificationRepository):
    """Deterministic reference repository for local operation and contract tests."""

    def __init__(self) -> None:
        self._items: dict[str, Notification] = {}

    async def save(self, notification: Notification) -> Notification:
        self._items[notification.id] = notification
        return notification

    async def get(self, notification_id: str) -> Notification:
        validate_id(notification_id, "notification")
        try:
            return self._items[notification_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"notification not found: {notification_id}",
            ) from exc

    async def list(self, query: NotificationQuery) -> tuple[Notification, ...]:
        now = datetime.now(UTC)
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
        if query.limit is None:
            return tuple(items[query.offset :])
        return tuple(items[query.offset : query.offset + query.limit])

    async def list_all(self) -> tuple[Notification, ...]:
        items = list(self._items.values())
        items.sort(key=lambda item: (item.updated_at, item.created_at, item.id), reverse=True)
        return tuple(items)

    async def find_active_aggregate(
        self,
        *,
        recipient: RecipientRef,
        aggregation_key: str,
    ) -> Notification | None:
        if not aggregation_key.strip():
            raise ValueError("aggregation_key must not be blank")
        now = datetime.now(UTC)
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

    async def count_unread(self, recipient: RecipientRef) -> int:
        now = datetime.now(UTC)
        return sum(
            1
            for item in self._items.values()
            if item.recipient == recipient
            and item.state is NotificationState.UNREAD
            and (item.expires_at is None or item.expires_at > now)
        )

    async def mark_all_read(
        self,
        recipient: RecipientRef,
        *,
        at: datetime | None = None,
    ) -> tuple[Notification, ...]:
        current = at or datetime.now(UTC)
        if current.utcoffset() is None:
            raise ValueError("at must be timezone-aware")
        updated: list[Notification] = []
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