"""Request-context authorization hardening for canonical Notifications (#75)."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.notifications import (
    Notification,
    NotificationCategory,
    NotificationQuery,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
)

from .extensions import ResourceService
from .models import PageQuery, RequestContext
from .notifications_composition import (
    NOTIFICATION_COLLECTION,
    NOTIFICATION_PREFERENCE_COLLECTION,
    _delivery_attempt_resource,
    _notification_resource,
    _optional_enum,
    _recipient_from_context,
)
from .notifications_runtime_composition import _preference_resource
from .notifications_source_composition import ControlPlane as _BaseControlPlane
from .notifications_source_composition import ControlPlaneHTTP, build_openapi


class _AuthorizedNotificationResources(ResourceService):
    def __init__(self, control_plane: ControlPlane) -> None:
        self._control_plane = control_plane

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
        items = await self._control_plane.notification_service.list(
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
            if not await self._control_plane.notification_source_visible(context, item):
                continue
            attempts = await self._control_plane.notification_service.delivery_attempts(
                item.id,
                recipient=recipient,
            )
            resources.append(_notification_resource(item, attempts=attempts))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        recipient = _recipient_from_context(context)
        notification = await self._control_plane._visible_notification_or_not_found(
            context,
            resource_id,
            recipient,
        )
        attempts = await self._control_plane.notification_service.delivery_attempts(
            resource_id,
            recipient=recipient,
        )
        return _notification_resource(notification, attempts=attempts)


class _AuthorizedPreferenceResources(ResourceService):
    def __init__(self, control_plane: ControlPlane) -> None:
        self._control_plane = control_plane

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        recipient = _recipient_from_context(context)
        return (
            _preference_resource(
                self._control_plane.notification_service.get_preference(recipient),
                unread_count=await self._control_plane.notification_unread_count(context),
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
            self._control_plane.notification_service.get_preference(recipient),
            unread_count=await self._control_plane.notification_unread_count(context),
        )


class ControlPlane(_BaseControlPlane):
    """Notification stack that rechecks canonical source authorization per request.

    The persisted notification is historical attention state. Authorization is deliberately
    evaluated with the current authenticated ``RequestContext`` so principal identity, actor type,
    project scope and credential ceilings are not replaced by a stored recipient identifier.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.register_resource_service(NOTIFICATION_COLLECTION, _AuthorizedNotificationResources(self))
        self.register_resource_service(
            NOTIFICATION_PREFERENCE_COLLECTION,
            _AuthorizedPreferenceResources(self),
        )

    async def notification_source_visible(
        self,
        context: RequestContext,
        notification: Notification,
    ) -> bool:
        """Return current #15 visibility for the notification's canonical source resource."""

        source = notification.resource_ref or notification.source
        return await self._allowed(
            context,
            f"{source.resource_type}:read",
            source.resource_id,
            project_id=notification.project_id,
        )

    async def notification_unread_count(self, context: RequestContext) -> int:
        """Count only unread attention whose source is currently visible to this request actor."""

        recipient = _recipient_from_context(context)
        items = await self.notification_service.list(
            NotificationQuery(recipient=recipient, unread_only=True)
        )
        count = 0
        for item in items:
            if await self.notification_source_visible(context, item):
                count += 1
        return count

    async def _visible_notification_or_not_found(
        self,
        context: RequestContext,
        notification_id: str,
        recipient: RecipientRef,
    ) -> Notification:
        notification = await self.notification_service.get(notification_id, recipient=recipient)
        if not await self.notification_source_visible(context, notification):
            raise ContractError(ErrorCode.NOT_FOUND, "notification not found")
        return notification

    async def _mark_read(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        recipient = _recipient_from_context(context)
        await self._visible_notification_or_not_found(context, resource_ref, recipient)
        notification = await self.notification_service.mark_read(resource_ref, recipient=recipient)
        attempts = await self.notification_service.delivery_attempts(
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
        unread = await self.notification_service.list(
            NotificationQuery(recipient=recipient, unread_only=True)
        )
        updated = 0
        for notification in unread:
            if not await self.notification_source_visible(context, notification):
                continue
            await self.notification_service.mark_read(notification.id, recipient=recipient)
            updated += 1
        return {
            "id": recipient.id,
            "type": "notification-inbox-update",
            "updated_count": updated,
            "unread_count": await self.notification_unread_count(context),
        }

    async def _acknowledge(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        recipient = _recipient_from_context(context)
        await self._visible_notification_or_not_found(context, resource_ref, recipient)
        notification = await self.notification_service.acknowledge(
            resource_ref,
            recipient=recipient,
        )
        return _notification_resource(
            notification,
            attempts=await self.notification_service.delivery_attempts(
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
        await self._visible_notification_or_not_found(context, resource_ref, recipient)
        notification = await self.notification_service.dismiss(resource_ref, recipient=recipient)
        return _notification_resource(
            notification,
            attempts=await self.notification_service.delivery_attempts(
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
        await self._visible_notification_or_not_found(context, resource_ref, recipient)
        notification = await self.notification_service.archive(resource_ref, recipient=recipient)
        return _notification_resource(
            notification,
            attempts=await self.notification_service.delivery_attempts(
                notification.id,
                recipient=recipient,
            ),
        )

    async def _retry_delivery(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        recipient = _recipient_from_context(context)
        await self._visible_notification_or_not_found(context, resource_ref, recipient)
        channel_id = payload.get("channel_id")
        if not isinstance(channel_id, str) or not channel_id.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "notification.delivery.retry requires a non-blank channel_id",
            )
        attempt = await self.notification_service.retry_delivery(
            resource_ref,
            recipient=recipient,
            channel_id=channel_id,
        )
        return _delivery_attempt_resource(attempt)

    async def _update_runtime_preference(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        result = await super()._update_runtime_preference(context, resource_ref, payload)
        result["unread_count"] = await self.notification_unread_count(context)
        return result


__all__ = ["ControlPlane", "ControlPlaneHTTP", "build_openapi"]
