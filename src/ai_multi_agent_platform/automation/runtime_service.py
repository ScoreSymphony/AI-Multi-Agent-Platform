"""Runtime-facing Automation service hardening for trusted canonical platform events."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent

from .hardened_service import AutomationService as _HardenedAutomationService
from .models import Automation, AutomationState, TriggerDelivery, TriggerType, require_aware


class AutomationService(_HardenedAutomationService):
    """Final Automation service layer for canonical Event ingestion and durable retry hooks."""

    async def set_state(
        self,
        automation_id: str,
        state: AutomationState,
        *,
        now: datetime | None = None,
    ) -> Automation:
        updated = await super().set_state(automation_id, state, now=now)
        if state is not AutomationState.ENABLED:
            await self._emit_retry_suppressed_for_state(updated)
        return updated

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

    async def _emit(self, automation: Automation, delivery: TriggerDelivery, outcome: str) -> None:
        """Emit canonical delivery audit including #241 retry state without secret material."""

        if self._event_sink is None:
            return
        is_schedule = delivery.trigger_type in {TriggerType.ONE_TIME, TriggerType.RECURRING}
        await self._event_sink(
            {
                "type": "automation.delivery",
                "automation_id": automation.id,
                "automation_revision": automation.revision,
                "trigger_delivery_id": delivery.id,
                "generated_task_id": delivery.generated_task_id,
                "trigger_type": delivery.trigger_type.value,
                "source": delivery.source,
                "fired_at": delivery.fired_at.isoformat(),
                "received_at": delivery.received_at.isoformat(),
                "processing_duration_ms": delivery.processing_duration_ms,
                "dedupe_key": delivery.dedupe_key,
                "dedupe_outcome": outcome,
                "outcome": outcome,
                "attempt": delivery.attempt,
                "error_code": delivery.error_code,
                "retryable": delivery.retryable,
                "last_failed_at": (
                    None if delivery.last_failed_at is None else delivery.last_failed_at.isoformat()
                ),
                "next_retry_at": (
                    None if delivery.next_retry_at is None else delivery.next_retry_at.isoformat()
                ),
                "retry_exhausted_at": (
                    None
                    if delivery.retry_exhausted_at is None
                    else delivery.retry_exhausted_at.isoformat()
                ),
                "automation_state": automation.state.value,
                "schedule_timezone": automation.trigger.timezone if is_schedule else None,
                "schedule_at": (
                    automation.trigger.at.isoformat()
                    if is_schedule and automation.trigger.at is not None
                    else None
                ),
                "schedule_interval_seconds": (
                    automation.trigger.interval_seconds if is_schedule else None
                ),
                "schedule_missed_policy": (
                    automation.trigger.missed_schedule_policy.value if is_schedule else None
                ),
                "schedule_next_evaluation_at": (
                    automation.next_evaluation_at.isoformat()
                    if is_schedule and automation.next_evaluation_at is not None
                    else None
                ),
            }
        )


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
