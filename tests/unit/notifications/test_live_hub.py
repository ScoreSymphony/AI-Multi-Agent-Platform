"""Notification live-hub behavior migrated from issue #75 coverage."""

from __future__ import annotations

import asyncio
from typing import Any

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import NotificationLiveHub, RecipientRef, RecipientType


def _recipient() -> RecipientRef:
    return RecipientRef(RecipientType.USER, new_id("user"))


def _payload(recipient: RecipientRef, event: str = "notification.created") -> dict[str, Any]:
    return {
        "event": event,
        "notification_id": new_id("notification"),
        "recipient_type": recipient.type.value,
        "recipient_id": recipient.id,
    }


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
