"""Canonical event projection hook for the notification subsystem."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from ai_multi_agent_platform.contracts import (
    EventProvider,
    HealthStatus,
    OperationControl,
    PlatformEvent,
    ProviderDescriptor,
)

from .service import NotificationService

ProjectionFailureSink = Callable[[PlatformEvent, Exception], Awaitable[None]]


class NotificationProjectingEventProvider(EventProvider):
    """Decorate canonical event transport with non-authoritative attention projection.

    Canonical lifecycle persistence has already succeeded before the kernel mirrors an event
    to this provider. Notification projection failures are therefore reported separately and
    never turn the owning lifecycle operation into a false failure. Replaying the same event
    remains safe because notification aggregation keys are deterministic.
    """

    def __init__(
        self,
        inner: EventProvider,
        notifications: NotificationService,
        *,
        projection_failure_sink: ProjectionFailureSink | None = None,
    ) -> None:
        self._inner = inner
        self._notifications = notifications
        self._projection_failure_sink = projection_failure_sink

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def health(self) -> HealthStatus:
        return await self._inner.health()

    async def publish(self, event: PlatformEvent) -> None:
        await self._inner.publish(event)
        try:
            await self._notifications.project_event(event)
        except Exception as exc:
            if self._projection_failure_sink is not None:
                await self._projection_failure_sink(event, exc)

    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> tuple[PlatformEvent, ...]:
        return await self._inner.read(
            correlation_id,
            after_event_id=after_event_id,
            control=control,
        )

    def subscribe(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> AsyncIterator[PlatformEvent]:
        return self._inner.subscribe(
            correlation_id,
            after_event_id=after_event_id,
            control=control,
        )

    async def replay(self, events: tuple[PlatformEvent, ...]) -> int:
        """Reconcile previously persisted canonical events after projection failure/restart."""

        projected = 0
        for event in events:
            projected += len(await self._notifications.project_event(event))
        return projected
