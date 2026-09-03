"""Canonical Control Plane composition for notifications and user attention (#75)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.notifications import (
    DeliveryAttempt,
    EventOwnerRecipientResolver,
    InMemoryNotificationPreferenceRepository,
    InMemoryNotificationRepository,
    Notification,
    NotificationCategory,
    NotificationDeliveryCoordinator,
    NotificationEventSink,
    NotificationPreference,
    NotificationPreferenceRepository,
    NotificationQuery,
    NotificationRepository,
    NotificationService,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
    RecipientType,
    TaskTerminalNotificationRule,
)

from .extensions import ResourceService
from .models import PageQuery, RequestContext
from .terminal_composition import ControlPlane as _BaseControlPlane
from .terminal_composition import ControlPlaneASGI, ControlPlaneHTTP, build_openapi

NOTIFICATION_COLLECTION = "notifications"
NOTIFICATION_PREFERENCE_COLLECTION = "notification-preferences"
NOTIFICATION_COMMANDS = (
    "notification.mark-read",
    "notification.mark-all-read",
    "notification.acknowledge",
    "notification.dismiss",
    "notification.archive",
    "notification.preference.update",
    "notification.delivery.retry",
)


class _NotificationResources(ResourceService):
    def __init__(self, service: NotificationService) -> None:
        self._service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        recipient = _recipient_from_context(context)
        filters = query.filters or {}
        category = _optional_enum(filters.get("category"), NotificationCategory, "category")
        severity = _optional_enum(filters.get("severity"), NotificationSeverity, "severity")
        project_id = filters.get("project_id")
        state = _optional_enum(filters.get("state"), NotificationState, "state")
        items = await self._service.list(
            NotificationQuery(
                recipient=recipient,
                category=category,
                severity=severity,
                project_id=project_id,
                unread_only=state is NotificationState.UNREAD,
                include_archived=state is NotificationState.ARCHIVED,
            )
        )
        if state is not None:
            items = tuple(item for item in items if item.state is state)
        resources: list[dict[str, JsonValue]] = []
        for item in items:
            attempts = await self._service.delivery_attempts(item.id, recipient=recipient)
            resources.append(_notification_resource(item, attempts=attempts))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        recipient = _recipient_from_context(context)
        notification = await self._service.get(resource_id, recipient=recipient)
        attempts = await self._service.delivery_attempts(resource_id, recipient=recipient)
        return _notification_resource(notification, attempts=attempts)


class _PreferenceResources(ResourceService):
    def __init__(self, service: NotificationService) -> None:
        self._service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        recipient = _recipient_from_context(context)
        preference = self._service.get_preference(recipient)
        return (
            _preference_resource(
                preference,
                unread_count=await self._service.unread_count(recipient),
            ),
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        recipient = _recipient_from_context(context)
        if resource_id != recipient.id:
            raise ContractError(ErrorCode.NOT_FOUND, "notification preference not found")
        return _preference_resource(
            self._service.get_preference(recipient),
            unread_count=await self._service.unread_count(recipient),
        )


class ControlPlane(_BaseControlPlane):
    """Current platform Control Plane plus the canonical #75 notification subsystem."""

    def __init__(
        self,
        *args: Any,
        notification_repository: NotificationRepository | None = None,
        notification_preference_repository: NotificationPreferenceRepository | None = None,
        notification_delivery: NotificationDeliveryCoordinator | None = None,
        notification_service: NotificationService | None = None,
        notification_event_sink: NotificationEventSink | None = None,
        **kwargs: Any,
    ) -> None:
        supplied_resources = kwargs.get("resource_services")
        if isinstance(supplied_resources, Mapping):
            conflicts = sorted(
                set(supplied_resources).intersection(
                    {NOTIFICATION_COLLECTION, NOTIFICATION_PREFERENCE_COLLECTION}
                )
            )
            if conflicts:
                raise ValueError(
                    f"resource_services conflict with canonical notification routes: {conflicts!r}"
                )
        supplied_commands = kwargs.get("command_handlers")
        if isinstance(supplied_commands, Mapping):
            conflicts = sorted(set(supplied_commands).intersection(NOTIFICATION_COMMANDS))
            if conflicts:
                raise ValueError(
                    f"command_handlers conflict with canonical notification commands: {conflicts!r}"
                )

        super().__init__(*args, **kwargs)
        if notification_service is None:
            repository = notification_repository or InMemoryNotificationRepository()
            preferences = (
                notification_preference_repository
                or InMemoryNotificationPreferenceRepository()
            )
            notification_service = NotificationService(
                repository=repository,
                preferences=preferences,
                rules=(TaskTerminalNotificationRule(EventOwnerRecipientResolver()),),
                delivery=notification_delivery,
                event_sink=notification_event_sink,
            )
        self._notification_service = notification_service
        self.register_resource_service(
            NOTIFICATION_COLLECTION,
            _NotificationResources(self._notification_service),
        )
        self.register_resource_service(
            NOTIFICATION_PREFERENCE_COLLECTION,
            _PreferenceResources(self._notification_service),
        )
        self.register_command("notification.mark-read", self._mark_read)
        self.register_command("notification.mark-all-read", self._mark_all_read)
        self.register_command("notification.acknowledge", self._acknowledge)
        self.register_command("notification.dismiss", self._dismiss)
        self.register_command("notification.archive", self._archive)
        self.register_command("notification.preference.update", self._update_preference)
        self.register_command("notification.delivery.retry", self._retry_delivery)

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    async def _mark_read(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        recipient = _recipient_from_context(context)
        notification = await self._notification_service.mark_read(resource_ref, recipient=recipient)
        attempts = await self._notification_service.delivery_attempts(
            notification.id,
            recipient=recipient,
        )
        return _notification_resource(notification, attempts=attempts)

    async def _mark_all_read(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        if resource_ref != NOTIFICATION_COLLECTION:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "notification.mark-all-read resource_ref must be 'notifications'",
            )
        recipient = _recipient_from_context(context)
        updated = await self._notification_service.mark_all_read(recipient)
        return {
            "id": recipient.id,
            "type": "notification-inbox-update",
            "updated_count": len(updated),
            "unread_count": await self._notification_service.unread_count(recipient),
        }

    async def _acknowledge(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        recipient = _recipient_from_context(context)
        notification = await self._notification_service.acknowledge(
            resource_ref,
            recipient=recipient,
        )
        return _notification_resource(
            notification,
            attempts=await self._notification_service.delivery_attempts(
                notification.id,
                recipient=recipient,
            ),
        )

    async def _dismiss(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        recipient = _recipient_from_context(context)
        notification = await self._notification_service.dismiss(resource_ref, recipient=recipient)
        return _notification_resource(
            notification,
            attempts=await self._notification_service.delivery_attempts(
                notification.id,
                recipient=recipient,
            ),
        )

    async def _archive(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        recipient = _recipient_from_context(context)
        notification = await self._notification_service.archive(resource_ref, recipient=recipient)
        return _notification_resource(
            notification,
            attempts=await self._notification_service.delivery_attempts(
                notification.id,
                recipient=recipient,
            ),
        )

    async def _update_preference(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        recipient = _recipient_from_context(context)
        if resource_ref != recipient.id:
            raise ContractError(ErrorCode.NOT_FOUND, "notification preference not found")
        current = self._notification_service.get_preference(recipient)
        preference = NotificationPreference(
            recipient=recipient,
            enabled_categories=_category_set(payload, current.enabled_categories),
            minimum_severity=_severity(payload, current.minimum_severity),
            project_ids=_string_set(payload, "project_ids", current.project_ids),
            muted=_boolean(payload, "muted", current.muted),
            in_app_enabled=_boolean(payload, "in_app_enabled", current.in_app_enabled),
            external_channels=_string_set(
                payload,
                "external_channels",
                current.external_channels,
            ),
            aggregate_duplicates=_boolean(
                payload,
                "aggregate_duplicates",
                current.aggregate_duplicates,
            ),
        )
        saved = self._notification_service.set_preference(preference)
        return _preference_resource(
            saved,
            unread_count=await self._notification_service.unread_count(recipient),
        )

    async def _retry_delivery(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        recipient = _recipient_from_context(context)
        channel_id = payload.get("channel_id")
        if not isinstance(channel_id, str) or not channel_id.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "notification.delivery.retry requires a non-blank channel_id",
            )
        attempt = await self._notification_service.retry_delivery(
            resource_ref,
            recipient=recipient,
            channel_id=channel_id,
        )
        return _delivery_attempt_resource(attempt)


def _recipient_from_context(context: RequestContext) -> RecipientRef:
    owner_type = context.actor.owner_type
    owner_id = context.actor.owner_id
    if owner_type is None or owner_id is None:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "notification inbox requires authenticated canonical owner context",
        )
    try:
        recipient_type = RecipientType(owner_type)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "authenticated actor does not have a user/team/organization notification inbox",
        ) from exc
    try:
        return RecipientRef(type=recipient_type, id=owner_id)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "notification inbox owner is not a canonical identity",
        ) from exc


def _notification_resource(
    notification: Notification,
    *,
    attempts: tuple[DeliveryAttempt, ...] = (),
) -> dict[str, JsonValue]:
    actions: list[JsonValue] = [
        {
            "action_id": action.action_id,
            "label": action.label,
            "command": action.command,
            "resource_type": action.resource_type,
            "resource_id": action.resource_id,
            "href": action.href,
        }
        for action in notification.actions
    ]
    resource_ref: JsonValue = None
    if notification.resource_ref is not None:
        resource_ref = {
            "resource_type": notification.resource_ref.resource_type,
            "resource_id": notification.resource_ref.resource_id,
        }
    delivery_attempts: list[JsonValue] = [
        _delivery_attempt_resource(attempt) for attempt in attempts
    ]
    return {
        "id": notification.id,
        "type": "notification",
        "category": notification.category.value,
        "severity": notification.severity.value,
        "title": notification.title,
        "summary": dict(notification.summary),
        "state": notification.state.value,
        "recipient": {
            "type": notification.recipient.type.value,
            "id": notification.recipient.id,
        },
        "source": {
            "resource_type": notification.source.resource_type,
            "resource_id": notification.source.resource_id,
        },
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
        "actions": actions,
        "aggregation_key": notification.aggregation_key,
        "occurrence_count": notification.occurrence_count,
        "created_at": notification.created_at.isoformat(),
        "updated_at": notification.updated_at.isoformat(),
        "read_at": None if notification.read_at is None else notification.read_at.isoformat(),
        "acknowledged_at": (
            None
            if notification.acknowledged_at is None
            else notification.acknowledged_at.isoformat()
        ),
        "dismissed_at": (
            None if notification.dismissed_at is None else notification.dismissed_at.isoformat()
        ),
        "archived_at": (
            None if notification.archived_at is None else notification.archived_at.isoformat()
        ),
        "expires_at": (
            None if notification.expires_at is None else notification.expires_at.isoformat()
        ),
        "correlation_id": notification.correlation_id,
        "causation_id": notification.causation_id,
        "delivery": {
            "metadata": dict(notification.delivery_metadata),
            "attempts": delivery_attempts,
        },
    }


def _delivery_attempt_resource(attempt: DeliveryAttempt) -> dict[str, JsonValue]:
    return {
        "id": attempt.id,
        "type": "notification-delivery-attempt",
        "notification_id": attempt.notification_id,
        "recipient": {
            "type": attempt.recipient.type.value,
            "id": attempt.recipient.id,
        },
        "channel": attempt.channel,
        "status": attempt.status.value,
        "attempt": attempt.attempt,
        "attempted_at": attempt.attempted_at.isoformat(),
        "provider_reference": attempt.provider_reference,
        "retry_after_seconds": attempt.retry_after_seconds,
        "metadata": dict(attempt.metadata),
    }


def _preference_resource(
    preference: NotificationPreference,
    *,
    unread_count: int,
) -> dict[str, JsonValue]:
    return {
        "id": preference.recipient.id,
        "type": "notification-preference",
        "recipient": {
            "type": preference.recipient.type.value,
            "id": preference.recipient.id,
        },
        "enabled_categories": sorted(item.value for item in preference.enabled_categories),
        "minimum_severity": preference.minimum_severity.value,
        "project_ids": sorted(preference.project_ids),
        "muted": preference.muted,
        "in_app_enabled": preference.in_app_enabled,
        "external_channels": sorted(preference.external_channels),
        "aggregate_duplicates": preference.aggregate_duplicates,
        "unread_count": unread_count,
    }


def _optional_enum(value: str | None, enum_type: Any, name: str) -> Any:
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"unsupported {name}: {value}") from exc


def _category_set(
    payload: dict[str, JsonValue],
    default: frozenset[NotificationCategory],
) -> frozenset[NotificationCategory]:
    raw = payload.get("enabled_categories")
    if raw is None:
        return default
    if not isinstance(raw, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "enabled_categories must be an array")
    try:
        return frozenset(
            NotificationCategory(item) for item in _string_items(raw, "enabled_categories")
        )
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, "unknown notification category") from exc


def _severity(
    payload: dict[str, JsonValue],
    default: NotificationSeverity,
) -> NotificationSeverity:
    raw = payload.get("minimum_severity")
    if raw is None:
        return default
    if not isinstance(raw, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, "minimum_severity must be a string")
    try:
        return NotificationSeverity(raw)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, "unknown notification severity") from exc


def _string_set(
    payload: dict[str, JsonValue],
    name: str,
    default: frozenset[str],
) -> frozenset[str]:
    raw = payload.get(name)
    if raw is None:
        return default
    if not isinstance(raw, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an array")
    return frozenset(_string_items(raw, name))


def _string_items(raw: list[JsonValue], name: str) -> tuple[str, ...]:
    items: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{name} must contain non-blank strings",
            )
        items.append(item)
    return tuple(items)


def _boolean(payload: dict[str, JsonValue], name: str, default: bool) -> bool:
    raw = payload.get(name)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean")
    return raw
