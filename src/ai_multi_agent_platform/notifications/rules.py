"""Canonical event-to-notification rule hooks."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import PlatformEvent

from .models import (
    NotificationCandidate,
    NotificationCategory,
    NotificationSeverity,
    SourceRef,
)
from .recipients import RecipientResolver


class NotificationRule(Protocol):
    async def evaluate(self, event: PlatformEvent) -> tuple[NotificationCandidate, ...]: ...


class TaskTerminalNotificationRule:
    """Project canonical Task terminal events into user-attention notifications."""

    _SUPPORTED = frozenset({"task.succeeded", "task.failed"})

    def __init__(self, recipients: RecipientResolver) -> None:
        self._recipients = recipients

    async def evaluate(self, event: PlatformEvent) -> tuple[NotificationCandidate, ...]:
        if event.event_type not in self._SUPPORTED or event.subject_type != "task":
            return ()

        resolved = await self._recipients.resolve(event)
        if not resolved:
            return ()

        failed = event.event_type == "task.failed"
        title = "Task failed" if failed else "Task completed"
        severity = NotificationSeverity.ERROR if failed else NotificationSeverity.INFO
        status = "failed" if failed else "succeeded"
        return tuple(
            NotificationCandidate(
                category=NotificationCategory.TASK,
                severity=severity,
                title=title,
                summary={
                    "status": status,
                    "task_id": event.subject_id,
                },
                recipient=recipient,
                source=SourceRef(resource_type="task", resource_id=event.subject_id),
                project_id=event.project_id,
                task_id=event.subject_id,
                aggregation_key=f"task:{event.subject_id}:{status}",
                correlation_id=event.correlation_id,
                causation_id=event.id,
            )
            for recipient in resolved
        )
