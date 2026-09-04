"""Runtime-complete, restart-safe Notification Control Plane composition (#75 hardening)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.notifications import (
    InMemoryNotificationRuntimeState,
    Notification,
    NotificationCategory,
    NotificationDeliveryCoordinator,
    NotificationPreference,
    NotificationRuntime,
    NotificationRuntimeState,
    NotificationRuntimeTick,
    NotificationSeverity,
    SqliteDeliveryAttemptRepository,
    SqliteNotificationPreferenceRepository,
    SqliteNotificationRepository,
    SqliteNotificationRuntimeState,
)
from ai_multi_agent_platform.notifications.task_management import task_attention_state_candidates

from .extensions import ResourceService
from .models import PageQuery, RequestContext
from .notifications_composition import (
    NOTIFICATION_PREFERENCE_COLLECTION,
    ControlPlaneHTTP,
    _recipient_from_context,
    build_openapi,
)
from .notifications_composition import (
    ControlPlane as _BaseControlPlane,
)

NOTIFICATION_STATE_ENV = "AI_MULTI_AGENT_PLATFORM_NOTIFICATION_STATE"
_MAX_REMINDER_SCAN_WINDOW = timedelta(days=365)


class _RuntimePreferenceResources(ResourceService):
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
                unread_count=await self._control_plane.notification_service.unread_count(recipient),
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
            unread_count=await self._control_plane.notification_service.unread_count(recipient),
        )


class ControlPlane(_BaseControlPlane):
    """Canonical Notifications with durable persistence and autonomous projection/reminders.

    ``notification_state_path`` activates the restart-safe reference implementation. When it is
    omitted, low-level embeddings remain explicitly ephemeral. Production-shaped deployments may
    set ``AI_MULTI_AGENT_PLATFORM_NOTIFICATION_STATE`` instead of passing the path directly.
    """

    def __init__(
        self,
        *args: Any,
        notification_state_path: str | Path | None = None,
        notification_runtime_state: NotificationRuntimeState | None = None,
        notification_runtime_poll_seconds: float = 1.0,
        **kwargs: Any,
    ) -> None:
        configured_path = notification_state_path
        if configured_path is None:
            configured_path = os.environ.get(NOTIFICATION_STATE_ENV)
        state_path = None if configured_path is None else Path(configured_path)

        events = cast(EventRepository | None, kwargs.get("events"))
        if events is None:
            raise ValueError(
                "Notification runtime composition requires the canonical EventRepository"
            )

        custom_service = kwargs.get("notification_service") is not None
        if state_path is not None and not custom_service:
            kwargs.setdefault("notification_repository", SqliteNotificationRepository(state_path))
            kwargs.setdefault(
                "notification_preference_repository",
                SqliteNotificationPreferenceRepository(state_path),
            )
            if kwargs.get("notification_delivery") is None:
                kwargs["notification_delivery"] = NotificationDeliveryCoordinator(
                    attempts=SqliteDeliveryAttemptRepository(state_path)
                )

        if notification_runtime_state is None:
            notification_runtime_state = (
                SqliteNotificationRuntimeState(state_path)
                if state_path is not None
                else InMemoryNotificationRuntimeState()
            )

        super().__init__(*args, **kwargs)
        self.register_resource_service(
            NOTIFICATION_PREFERENCE_COLLECTION,
            _RuntimePreferenceResources(self),
        )
        self.register_command("notification.preference.update", self._update_runtime_preference)
        self._notification_runtime = NotificationRuntime(
            events=events,
            notifications=self.notification_service,
            state=notification_runtime_state,
            reminder_evaluator=self.evaluate_task_attention_reminders,
            poll_interval_seconds=notification_runtime_poll_seconds,
        )

    @property
    def notification_runtime(self) -> NotificationRuntime:
        return self._notification_runtime

    @property
    def notification_runtime_state(self) -> NotificationRuntimeState:
        return self._notification_runtime.state

    async def start_notification_runtime(self) -> None:
        await self._notification_runtime.start()

    async def stop_notification_runtime(self) -> None:
        await self._notification_runtime.stop()

    async def run_notification_runtime_once(self) -> NotificationRuntimeTick:
        return await self._notification_runtime.run_once()

    async def evaluate_task_attention_reminders(
        self,
        *,
        now: datetime | None = None,
        approaching_window: timedelta | None = None,
    ) -> tuple[Notification, ...]:
        """Project #88 attention with per-recipient reminder lead time and enable policy."""

        current = now or datetime.now(UTC)
        if current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if approaching_window is not None and approaching_window <= timedelta(0):
            raise ValueError("approaching_window must be positive")

        scan_window = approaching_window or _MAX_REMINDER_SCAN_WINDOW
        active: list[Notification] = []
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            view = await self._task_management.view(task)
            for candidate in task_attention_state_candidates(
                view,
                task,
                now=current,
                approaching_window=scan_window,
            ):
                if (
                    approaching_window is None
                    and candidate.category is NotificationCategory.DEADLINE
                    and candidate.summary.get("phase") == "approaching"
                ):
                    due_at = view.metadata.due_at
                    if due_at is None:
                        continue
                    preference = self.notification_service.get_preference(candidate.recipient)
                    remaining = due_at - current.astimezone(UTC)
                    if remaining > timedelta(seconds=preference.deadline_reminder_lead_seconds):
                        continue
                notification = await self.notification_service.create_once(candidate, now=current)
                if notification is not None:
                    active.append(notification)
        return tuple(active)

    async def _update_runtime_preference(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        recipient = _recipient_from_context(context)
        if resource_ref != recipient.id:
            raise ContractError(ErrorCode.NOT_FOUND, "notification preference not found")
        current = self.notification_service.get_preference(recipient)
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
            deadline_reminders_enabled=_boolean(
                payload,
                "deadline_reminders_enabled",
                current.deadline_reminders_enabled,
            ),
            deadline_reminder_lead_seconds=_integer(
                payload,
                "deadline_reminder_lead_seconds",
                current.deadline_reminder_lead_seconds,
            ),
            overdue_reminders_enabled=_boolean(
                payload,
                "overdue_reminders_enabled",
                current.overdue_reminders_enabled,
            ),
            quiet_hours_start=_nullable_string(
                payload,
                "quiet_hours_start",
                current.quiet_hours_start,
            ),
            quiet_hours_end=_nullable_string(
                payload,
                "quiet_hours_end",
                current.quiet_hours_end,
            ),
            quiet_hours_timezone=_nullable_string(
                payload,
                "quiet_hours_timezone",
                current.quiet_hours_timezone,
            ),
        )
        saved = self.notification_service.set_preference(preference)
        return _preference_resource(
            saved,
            unread_count=await self.notification_service.unread_count(recipient),
        )


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
        "unread_count": unread_count,
    }


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
        return frozenset(NotificationCategory(item) for item in _strings(raw, "enabled_categories"))
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
    return frozenset(_strings(raw, name))


def _strings(raw: list[JsonValue], name: str) -> tuple[str, ...]:
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{name} must contain non-blank strings",
            )
        values.append(item)
    return tuple(values)


def _boolean(payload: dict[str, JsonValue], name: str, default: bool) -> bool:
    raw = payload.get(name)
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean")
    return raw


def _integer(payload: dict[str, JsonValue], name: str, default: int) -> int:
    raw = payload.get(name)
    if raw is None:
        return default
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an integer")
    return raw


def _nullable_string(
    payload: dict[str, JsonValue],
    name: str,
    default: str | None,
) -> str | None:
    if name not in payload:
        return default
    raw = payload[name]
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be null or a non-blank string")
    return raw


def _json_strings(values: list[str]) -> list[JsonValue]:
    return list(values)


__all__ = [
    "NOTIFICATION_STATE_ENV",
    "ControlPlane",
    "ControlPlaneHTTP",
    "build_openapi",
]
