"""Completed source-domain integrations for canonical Notifications (#75 hardening)."""

from __future__ import annotations

from typing import Any, cast

from ai_multi_agent_platform.automation import AutomationEventSink
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.notifications import (
    NotificationAction,
    NotificationCandidate,
    NotificationCategory,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
)

from .notifications_runtime_composition import ControlPlane as _BaseControlPlane
from .notifications_runtime_composition import ControlPlaneHTTP, build_openapi

_AUTOMATION_FAILURE_OUTCOMES = frozenset({"failed", "rejected"})


class ControlPlane(_BaseControlPlane):
    """Notification runtime plus direct integration with completed canonical source domains."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        provided_sink = cast(AutomationEventSink | None, kwargs.get("automation_event_sink"))
        holder: list[ControlPlane] = []

        async def notification_automation_sink(event: dict[str, JsonValue]) -> None:
            if provided_sink is not None:
                await provided_sink(event)
            if holder:
                await holder[0]._project_automation_event(event)

        if kwargs.get("automation_service") is None:
            kwargs["automation_event_sink"] = notification_automation_sink
        super().__init__(*args, **kwargs)
        holder.append(self)

    async def _project_automation_event(self, event: dict[str, JsonValue]) -> None:
        """Project #18 failures without allowing attention failure to fail Automation."""

        try:
            if event.get("type") != "automation.delivery":
                return
            outcome = event.get("outcome")
            if not isinstance(outcome, str) or outcome not in _AUTOMATION_FAILURE_OUTCOMES:
                return
            automation_id = event.get("automation_id")
            delivery_id = event.get("trigger_delivery_id")
            if not isinstance(automation_id, str) or not isinstance(delivery_id, str):
                return
            automation = await self.automation_service.get_automation(automation_id)
            try:
                recipient_type = RecipientType(automation.identity.owner_type)
                recipient = RecipientRef(recipient_type, automation.identity.owner_id)
            except ValueError:
                return
            generated_task_id = event.get("generated_task_id")
            task_id = generated_task_id if isinstance(generated_task_id, str) else None
            error_code = event.get("error_code")
            safe_error = error_code if isinstance(error_code, str) else None
            await self.notification_service.create_once(
                NotificationCandidate(
                    category=NotificationCategory.AUTOMATION,
                    severity=NotificationSeverity.ERROR,
                    title="Automation failed",
                    summary={
                        "automation_id": automation.id,
                        "trigger_delivery_id": delivery_id,
                        "outcome": outcome,
                        "error_code": safe_error,
                    },
                    recipient=recipient,
                    source=SourceRef("automation", automation.id),
                    project_id=automation.project_id,
                    workspace_id=automation.workspace_id,
                    task_id=task_id,
                    automation_id=automation.id,
                    resource_ref=SourceRef("automation", automation.id),
                    actions=(
                        NotificationAction(
                            action_id="open-automation",
                            label="Open automation",
                            resource_type="automation",
                            resource_id=automation.id,
                            href=f"/automations/{automation.id}",
                        ),
                    ),
                    aggregation_key=(
                        f"automation:{automation.id}:delivery:{delivery_id}:{outcome}"
                    ),
                )
            )
        except Exception:
            # #18 is already authoritative and committed when its event sink runs. Attention
            # projection is best-effort and must never falsify Automation lifecycle failure.
            return


__all__ = ["ControlPlane", "ControlPlaneHTTP", "build_openapi"]
