"""Runtime-complete, restart-safe Notification Control Plane composition (#75 hardening)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.notifications import (
    InMemoryNotificationRuntimeState,
    NotificationDeliveryCoordinator,
    NotificationRuntime,
    NotificationRuntimeState,
    NotificationRuntimeTick,
    SqliteDeliveryAttemptRepository,
    SqliteNotificationPreferenceRepository,
    SqliteNotificationRepository,
    SqliteNotificationRuntimeState,
)

from .notifications_composition import ControlPlane as _BaseControlPlane
from .notifications_composition import ControlPlaneHTTP, build_openapi

NOTIFICATION_STATE_ENV = "AI_MULTI_AGENT_PLATFORM_NOTIFICATION_STATE"


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
            raise ValueError("Notification runtime composition requires the canonical EventRepository")

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


__all__ = [
    "NOTIFICATION_STATE_ENV",
    "ControlPlane",
    "ControlPlaneHTTP",
    "build_openapi",
]
