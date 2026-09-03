"""Runtime-facing Automation service hardening for trusted canonical platform events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent

from .hardened_service import AutomationService as _HardenedAutomationService
from .models import Automation, AutomationState, TriggerDelivery, TriggerType, require_aware


class AutomationService(_HardenedAutomationService):
    """Final #18 service layer for canonical #6 Event ingestion.

    The legacy ``deliver_platform_event`` method remains available for explicit embeddings/tests.
    The autonomous platform runtime uses this method instead so trusted canonical Event ownership,
    project scope and creation-time boundaries cannot be supplied by an untrusted payload.
    """

    async def deliver_canonical_platform_event(
        self,
        event: PlatformEvent,
    ) -> tuple[TriggerDelivery, ...]:
        try:
            fired_at = require_aware(event.occurred_at, "event.occurred_at")
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical platform event occurred_at must be timezone-aware",
            ) from exc

        payload = _canonical_event_payload(event)
        deliveries: list[TriggerDelivery] = []
        for automation in await self.repository.list_automations():
            trigger = automation.trigger
            if automation.state is not AutomationState.ENABLED:
                continue
            if trigger.type is not TriggerType.PLATFORM_EVENT:
                continue
            if trigger.event_type != event.event_type:
                continue
            # A newly configured subscription is not a historical replay request. Starting a
            # runtime against an existing EventRepository therefore cannot backfire old events.
            if fired_at < automation.created_at:
                continue
            if not _canonical_event_visible_to_automation(automation, event):
                continue
            if not _matches(trigger.filters, payload):
                continue
            deliveries.append(
                await self._deliver(
                    automation,
                    trigger_type=TriggerType.PLATFORM_EVENT,
                    source="platform-event",
                    dedupe_key=f"event:{event.event_type}:{event.id}",
                    fired_at=fired_at,
                    payload=payload,
                )
            )
        return tuple(deliveries)


def _canonical_event_visible_to_automation(
    automation: Automation,
    event: PlatformEvent,
) -> bool:
    """Apply a conservative canonical visibility floor before trigger filters.

    An explicitly project-scoped Automation may consume Events from that project because the
    Control Plane separately authorizes the Automation's project scope when it is configured.
    An unscoped Automation is restricted to Events owned by the same canonical owner. Truly
    global/unowned Events are reserved for service-owned Automations.
    """

    if automation.project_id is not None:
        return event.project_id == automation.project_id

    if event.owner_ref is not None:
        return (
            event.owner_ref.type == automation.identity.owner_type
            and event.owner_ref.id == automation.identity.owner_id
        )

    return event.project_id is None and automation.identity.owner_type == "service"


def _canonical_event_payload(event: PlatformEvent) -> dict[str, JsonValue]:
    payload = _json_object(event.payload)
    payload.update(
        {
            "event_id": event.id,
            "event_type": event.event_type,
            "subject_type": event.subject_type,
            "subject_id": event.subject_id,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "trace_id": event.trace_id,
            "project_id": event.project_id,
        }
    )
    if event.owner_ref is not None:
        payload["owner_type"] = event.owner_ref.type
        payload["owner_id"] = event.owner_ref.id
    return payload


def _matches(filters: dict[str, JsonValue], payload: dict[str, JsonValue]) -> bool:
    return all(payload.get(key) == expected for key, expected in filters.items())


def _json_object(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise ContractError(
        ErrorCode.CONTRACT_VIOLATION,
        f"canonical event payload contains non-JSON value: {type(value).__name__}",
    )
