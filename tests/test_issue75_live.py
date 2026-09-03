from __future__ import annotations

import asyncio
from typing import Any

from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    RequestContext,
    build_openapi,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    NotificationCandidate,
    NotificationCategory,
    NotificationLiveHub,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _recipient() -> RecipientRef:
    return RecipientRef(RecipientType.USER, new_id("user"))


def _payload(recipient: RecipientRef, event: str = "notification.created") -> dict[str, Any]:
    return {
        "event": event,
        "notification_id": new_id("notification"),
        "recipient_type": recipient.type.value,
        "recipient_id": recipient.id,
    }


def _control_plane() -> ControlPlane:
    repository = InMemoryKernelRepository()
    return ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        events=repository,
        authorization=FakeAuthorizationProvider(allowed=True),
    )


def test_live_hub_is_recipient_scoped_and_supports_cursor_replay() -> None:
    async def scenario() -> None:
        hub = NotificationLiveHub()
        recipient = _recipient()
        other = _recipient()
        stream = hub.subscribe(recipient)
        pending = asyncio.create_task(anext(stream))

        await hub.publish(_payload(other))
        await asyncio.sleep(0)
        assert not pending.done()

        await hub.publish(_payload(recipient))
        first = await asyncio.wait_for(pending, timeout=1)
        assert first.recipient == recipient

        await hub.publish(_payload(recipient, "notification.read"))
        replay = hub.subscribe(recipient, after_event_id=first.id)
        second = await asyncio.wait_for(anext(replay), timeout=1)
        assert second.event == "notification.read"

        await stream.aclose()
        await replay.aclose()

    asyncio.run(scenario())


def test_control_plane_notification_subscription_uses_actor_inbox() -> None:
    async def scenario() -> None:
        control_plane = _control_plane()
        recipient = _recipient()
        context = RequestContext(
            request_id="issue-75-live",
            correlation_id="issue-75-live",
            actor=ActorContext(
                principal_ref=recipient.id,
                owner_type="user",
                owner_id=recipient.id,
            ),
        )
        stream = await control_plane.subscribe_notifications(context)
        pending = asyncio.create_task(anext(stream))
        task_id = new_id("task")
        created = await control_plane.notification_service.create(
            NotificationCandidate(
                category=NotificationCategory.TASK,
                severity=NotificationSeverity.ERROR,
                title="Task failed",
                summary={"status": "failed"},
                recipient=recipient,
                source=SourceRef("task", task_id),
                task_id=task_id,
                aggregation_key=f"task:{task_id}:failed",
            )
        )
        assert created is not None
        event = await asyncio.wait_for(pending, timeout=1)
        assert event["event"] == "notification.created"
        assert event["recipient"]["id"] == recipient.id
        assert event["payload"]["notification_id"] == created.id
        await stream.aclose()

    asyncio.run(scenario())


def test_notification_sse_route_streams_control_plane_events() -> None:
    async def scenario() -> None:
        control_plane = _control_plane()
        http = ControlPlaneHTTP(control_plane)
        app = ControlPlaneASGI(http)
        recipient = _recipient()
        sent: list[dict[str, Any]] = []
        body_received = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)
            body = message.get("body")
            if isinstance(body, bytes) and b"notification.event" in body:
                body_received.set()

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/notifications/stream",
            "headers": [
                (b"x-principal-ref", recipient.id.encode()),
                (b"x-owner-type", b"user"),
                (b"x-owner-id", recipient.id.encode()),
            ],
            "query_string": b"",
        }
        running = asyncio.create_task(app(scope, receive, send))
        for _ in range(20):
            if any(message.get("type") == "http.response.start" for message in sent):
                break
            await asyncio.sleep(0)

        task_id = new_id("task")
        await control_plane.notification_service.create(
            NotificationCandidate(
                category=NotificationCategory.TASK,
                severity=NotificationSeverity.INFO,
                title="Task finished",
                summary={"status": "succeeded"},
                recipient=recipient,
                source=SourceRef("task", task_id),
                task_id=task_id,
            )
        )
        await asyncio.wait_for(body_received.wait(), timeout=1)
        body = b"".join(
            message.get("body", b"")
            for message in sent
            if message.get("type") == "http.response.body"
        )
        assert b"event: notification.event" in body
        assert recipient.id.encode() in body
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())


def test_notification_openapi_advertises_live_stream() -> None:
    specification = build_openapi()
    path = specification["paths"]["/api/v1/notifications/stream"]
    assert path["get"]["responses"]["200"]["content"] == {"text/event-stream": {}}
    assert specification["x-notification-live-updates"]["transport"] == "sse"
