"""Canonical notification service and user-attention lifecycle."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    Notification,
    NotificationCandidate,
    NotificationPreference,
    NotificationQuery,
    NotificationState,
    RecipientRef,
)
from .preferences import NotificationPreferenceRepository, preference_allows
from .repository import NotificationRepository
from .rules import NotificationRule

NotificationEventSink = Callable[[dict[str, JsonValue]], Awaitable[None]]


class NotificationService:
    """Own notification projection state without owning the underlying source lifecycle."""

    def __init__(
        self,
        *,
        repository: NotificationRepository,
        preferences: NotificationPreferenceRepository,
        rules: Sequence[NotificationRule] = (),
        event_sink: NotificationEventSink | None = None,
    ) -> None:
        self._repository = repository
        self._preferences = preferences
        self._rules = tuple(rules)
        self._event_sink = event_sink

    async def project_event(self, event: PlatformEvent) -> tuple[Notification, ...]:
        created: list[Notification] = []
        for rule in self._rules:
            for candidate in await rule.evaluate(event):
                projected = await self.create(candidate)
                if projected is not None:
                    created.append(projected)
        return tuple(created)

    async def create(
        self,
        candidate: NotificationCandidate,
        *,
        now: datetime | None = None,
    ) -> Notification | None:
        current = _aware(now or datetime.now(UTC), "now")
        preference = self._preferences.get(candidate.recipient)
        if not preference_allows(preference, candidate):
            await self._emit(
                "notification.filtered",
                recipient=candidate.recipient,
                source_type=candidate.source.resource_type,
                source_id=candidate.source.resource_id,
                category=candidate.category.value,
            )
            return None

        if candidate.aggregation_key and preference.aggregate_duplicates:
            existing = await self._repository.find_active_aggregate(
                recipient=candidate.recipient,
                aggregation_key=candidate.aggregation_key,
            )
            if existing is not None:
                aggregated = replace(
                    existing,
                    title=candidate.title,
                    summary=candidate.summary,
                    severity=candidate.severity,
                    state=NotificationState.UNREAD,
                    occurrence_count=existing.occurrence_count + 1,
                    updated_at=current,
                    read_at=None,
                    correlation_id=candidate.correlation_id or existing.correlation_id,
                    causation_id=candidate.causation_id or existing.causation_id,
                )
                persisted = await self._repository.save(aggregated)
                await self._emit(
                    "notification.aggregated",
                    notification=persisted,
                    occurrence_count=persisted.occurrence_count,
                )
                return persisted

        notification = Notification(
            category=candidate.category,
            severity=candidate.severity,
            title=candidate.title,
            summary=candidate.summary,
            recipient=candidate.recipient,
            source=candidate.source,
            project_id=candidate.project_id,
            workspace_id=candidate.workspace_id,
            task_id=candidate.task_id,
            run_id=candidate.run_id,
            approval_id=candidate.approval_id,
            verification_id=candidate.verification_id,
            node_id=candidate.node_id,
            automation_id=candidate.automation_id,
            membership_id=candidate.membership_id,
            resource_ref=candidate.resource_ref,
            actions=candidate.actions,
            aggregation_key=candidate.aggregation_key,
            created_at=current,
            updated_at=current,
            expires_at=candidate.expires_at,
            correlation_id=candidate.correlation_id,
            causation_id=candidate.causation_id,
            delivery_metadata=candidate.delivery_metadata,
        )
        persisted = await self._repository.save(notification)
        await self._emit("notification.created", notification=persisted)
        return persisted

    async def get(self, notification_id: str, *, recipient: RecipientRef) -> Notification:
        notification = await self._repository.get(notification_id)
        self._require_recipient(notification, recipient)
        return notification

    async def list(self, query: NotificationQuery) -> tuple[Notification, ...]:
        return await self._repository.list(query)

    async def unread_count(self, recipient: RecipientRef) -> int:
        return await self._repository.count_unread(recipient)

    def get_preference(self, recipient: RecipientRef) -> NotificationPreference:
        return self._preferences.get(recipient)

    def set_preference(self, preference: NotificationPreference) -> NotificationPreference:
        return self._preferences.save(preference)

    async def mark_read(
        self,
        notification_id: str,
        *,
        recipient: RecipientRef,
        at: datetime | None = None,
    ) -> Notification:
        current = _aware(at or datetime.now(UTC), "at")
        notification = await self.get(notification_id, recipient=recipient)
        if notification.state is NotificationState.READ:
            return notification
        if notification.state in {NotificationState.DISMISSED, NotificationState.ARCHIVED}:
            return notification
        updated = replace(
            notification,
            state=NotificationState.READ,
            read_at=notification.read_at or current,
            updated_at=current,
        )
        persisted = await self._repository.save(updated)
        await self._emit("notification.read", notification=persisted)
        return persisted

    async def mark_all_read(
        self,
        recipient: RecipientRef,
        *,
        at: datetime | None = None,
    ) -> tuple[Notification, ...]:
        current = _aware(at or datetime.now(UTC), "at")
        updated = await self._repository.mark_all_read(recipient, at=current)
        if updated:
            await self._emit(
                "notification.mark_all_read",
                recipient=recipient,
                count=len(updated),
            )
        return updated

    async def acknowledge(
        self,
        notification_id: str,
        *,
        recipient: RecipientRef,
        at: datetime | None = None,
    ) -> Notification:
        current = _aware(at or datetime.now(UTC), "at")
        notification = await self.get(notification_id, recipient=recipient)
        if notification.state is NotificationState.ACKNOWLEDGED:
            return notification
        if notification.state in {NotificationState.DISMISSED, NotificationState.ARCHIVED}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"notification cannot be acknowledged from {notification.state.value}",
            )
        updated = replace(
            notification,
            state=NotificationState.ACKNOWLEDGED,
            read_at=notification.read_at or current,
            acknowledged_at=current,
            updated_at=current,
        )
        persisted = await self._repository.save(updated)
        await self._emit("notification.acknowledged", notification=persisted)
        return persisted

    async def dismiss(
        self,
        notification_id: str,
        *,
        recipient: RecipientRef,
        at: datetime | None = None,
    ) -> Notification:
        current = _aware(at or datetime.now(UTC), "at")
        notification = await self.get(notification_id, recipient=recipient)
        if notification.state is NotificationState.DISMISSED:
            return notification
        if notification.state is NotificationState.ARCHIVED:
            raise ContractError(ErrorCode.CONFLICT, "archived notification cannot be dismissed")
        updated = replace(
            notification,
            state=NotificationState.DISMISSED,
            read_at=notification.read_at or current,
            dismissed_at=current,
            updated_at=current,
        )
        persisted = await self._repository.save(updated)
        await self._emit("notification.dismissed", notification=persisted)
        return persisted

    async def archive(
        self,
        notification_id: str,
        *,
        recipient: RecipientRef,
        at: datetime | None = None,
    ) -> Notification:
        current = _aware(at or datetime.now(UTC), "at")
        notification = await self.get(notification_id, recipient=recipient)
        if notification.state is NotificationState.ARCHIVED:
            return notification
        updated = replace(
            notification,
            state=NotificationState.ARCHIVED,
            read_at=notification.read_at or current,
            archived_at=current,
            updated_at=current,
        )
        persisted = await self._repository.save(updated)
        await self._emit("notification.archived", notification=persisted)
        return persisted

    @staticmethod
    def _require_recipient(notification: Notification, recipient: RecipientRef) -> None:
        if notification.recipient != recipient:
            # Deliberately return not-found semantics so notification existence is not leaked.
            raise ContractError(ErrorCode.NOT_FOUND, "notification not found")

    async def _emit(
        self,
        event: str,
        *,
        notification: Notification | None = None,
        recipient: RecipientRef | None = None,
        **metadata: JsonValue,
    ) -> None:
        if self._event_sink is None:
            return
        target = recipient or (notification.recipient if notification is not None else None)
        payload: dict[str, JsonValue] = {
            "event": event,
            "notification_id": None if notification is None else notification.id,
            "recipient_type": None if target is None else target.type.value,
            "recipient_id": None if target is None else target.id,
            "source_type": None if notification is None else notification.source.resource_type,
            "source_id": None if notification is None else notification.source.resource_id,
            "correlation_id": None if notification is None else notification.correlation_id,
            **metadata,
        }
        await self._event_sink(payload)


def _aware(value: datetime, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value
