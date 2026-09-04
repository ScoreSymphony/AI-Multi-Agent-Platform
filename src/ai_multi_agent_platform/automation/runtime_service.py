"""Runtime-facing Automation service hardening for trusted canonical platform events."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent

from .hardened_service import AutomationService as _HardenedAutomationService
from .models import (
    Automation,
    AutomationState,
    DeliveryStatus,
    TriggerDelivery,
    TriggerType,
    require_aware,
    validate_invalidation_reason_code,
)
from .workspace_event_scope import WorkspaceEventScopeResolver

_AUTO_INVALIDATING_DELIVERY_ERRORS = frozenset(
    {
        ErrorCode.INVALID_CONFIGURATION.value,
        ErrorCode.UNSUPPORTED_CAPABILITY.value,
    }
)
_REVALIDATION_TRANSIENT_ERRORS = frozenset(
    {
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.UNAVAILABLE,
        ErrorCode.TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.RESOURCE_EXHAUSTED,
        ErrorCode.TRANSIENT_FAILURE,
        ErrorCode.BACKEND_ERROR,
    }
)


class AutomationService(_HardenedAutomationService):
    """Final Automation service layer for canonical events, retries and INVALID lifecycle."""

    _workspace_event_scope_resolver: WorkspaceEventScopeResolver | None = None

    def configure_workspace_event_scope_resolver(
        self,
        resolver: WorkspaceEventScopeResolver | None,
    ) -> None:
        """Bind the canonical workspace resolver before event ingestion starts."""

        self._workspace_event_scope_resolver = resolver

    async def set_state(
        self,
        automation_id: str,
        state: AutomationState,
        *,
        now: datetime | None = None,
    ) -> Automation:
        current = await self.repository.get_automation(automation_id)
        if state is AutomationState.INVALID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "use explicit automation invalidation semantics to enter invalid state",
            )
        if current.state is AutomationState.INVALID:
            raise ContractError(
                ErrorCode.CONFLICT,
                "invalid automation requires explicit revalidation before lifecycle changes",
            )
        updated = await super().set_state(automation_id, state, now=now)
        if state is not AutomationState.ENABLED:
            await self._emit_retry_suppressed_for_state(updated)
        return updated

    async def invalidate_automation(
        self,
        automation_id: str,
        *,
        reason_code: str,
        now: datetime | None = None,
    ) -> Automation:
        """Enter canonical INVALID using categorical metadata only.

        ``reason_code`` is deliberately a restricted machine category, never free-form provider
        error text. INVALID preserves the current schedule position and the prior lifecycle state.
        """

        try:
            reason = validate_invalidation_reason_code(reason_code)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "invalid automation invalidation reason code",
            ) from exc
        current = await self.repository.get_automation(automation_id)
        occurred = require_aware(now or self._clock(), "now").astimezone(UTC)
        invalidated = current.invalidate(reason, occurred)
        persisted = await self.repository.save_automation(invalidated)
        await self._emit_configuration(
            persisted,
            action="invalidated",
            changed_fields=(
                "state",
                "invalidation_reason_code",
                "invalidated_at",
                "state_before_invalid",
            ),
            previous_state=current.state,
        )
        await self._emit_lifecycle_event(
            persisted,
            action="invalidated",
            previous_state=current.state,
            reason_code=reason,
            occurred_at=occurred,
        )
        await self._emit_retry_suppressed_for_state(persisted)
        return persisted

    async def revalidate_automation(
        self,
        automation_id: str,
        *,
        now: datetime | None = None,
    ) -> Automation:
        """Revalidate and restore the lifecycle state that preceded INVALID.

        The default validator confirms canonical in-process structure. Deployments that own
        durable external references may override ``_validate_configuration_for_revalidation``
        without changing the canonical lifecycle contract.
        """

        current = await self.repository.get_automation(automation_id)
        if current.state is not AutomationState.INVALID:
            return current
        occurred = require_aware(now or self._clock(), "now").astimezone(UTC)
        try:
            await self._validate_configuration_for_revalidation(current)
        except ContractError as exc:
            if exc.retryable or exc.code in _REVALIDATION_TRANSIENT_ERRORS:
                await self._emit_lifecycle_event(
                    current,
                    action="revalidation_deferred",
                    previous_state=current.state_before_invalid,
                    reason_code=current.invalidation_reason_code,
                    occurred_at=occurred,
                )
                raise ContractError(
                    exc.code,
                    "automation revalidation could not be completed due to a transient failure",
                    retryable=True,
                ) from exc
            reason = f"revalidation_{exc.code.value}"
            retained = current.invalidate(reason, occurred)
            retained = await self.repository.save_automation(retained)
            await self._emit_lifecycle_event(
                retained,
                action="revalidation_failed",
                previous_state=retained.state_before_invalid,
                reason_code=reason,
                occurred_at=occurred,
            )
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automation configuration remains invalid",
            ) from exc
        except (TypeError, ValueError) as exc:
            reason = "revalidation_invalid_configuration"
            retained = current.invalidate(reason, occurred)
            retained = await self.repository.save_automation(retained)
            await self._emit_lifecycle_event(
                retained,
                action="revalidation_failed",
                previous_state=retained.state_before_invalid,
                reason_code=reason,
                occurred_at=occurred,
            )
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automation configuration remains invalid",
            ) from exc

        recovered = current.revalidated(occurred)
        recovered = await self.repository.save_automation(recovered)
        await self._emit_configuration(
            recovered,
            action="revalidated",
            changed_fields=(
                "state",
                "invalidation_reason_code",
                "invalidated_at",
                "state_before_invalid",
            ),
            previous_state=AutomationState.INVALID,
        )
        await self._emit_lifecycle_event(
            recovered,
            action="revalidated",
            previous_state=AutomationState.INVALID,
            reason_code=None,
            occurred_at=occurred,
        )
        return recovered

    async def _validate_configuration_for_revalidation(self, automation: Automation) -> None:
        """Replaceable validation seam for durable external configuration references.

        Canonical dataclass construction and repository decoding already validate the local
        Automation/Trigger/TaskTemplate structure. The default reference path therefore treats an
        explicit authorized revalidation as confirmation that an externally fixed configuration
        may return to service. Integrations can override this seam to resolve secrets, providers or
        referenced resources and raise a provider-neutral ContractError on failure.
        """

        del automation

    async def deliver_webhook(
        self,
        automation_id: str,
        *,
        event_id: str,
        payload: dict[str, JsonValue],
        source: str,
        verified: bool,
        fired_at: datetime | None = None,
    ) -> TriggerDelivery:
        automation = await self.repository.get_automation(automation_id)
        if automation.state is AutomationState.INVALID:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "automation is invalid and cannot accept webhook deliveries",
            )
        return await super().deliver_webhook(
            automation_id,
            event_id=event_id,
            payload=payload,
            source=source,
            verified=verified,
            fired_at=fired_at,
        )

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
            visible, rejection_reason = await _canonical_event_visibility(
                automation,
                event,
                self._workspace_event_scope_resolver,
            )
            if not visible:
                await self._emit_event_visibility_rejection(
                    automation,
                    reason_code=rejection_reason or "scope_not_visible",
                )
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

    async def _process(self, automation: Automation, delivery: TriggerDelivery) -> TriggerDelivery:
        processed = await super()._process(automation, delivery)
        if (
            processed.status is DeliveryStatus.FAILED
            and processed.error_code in _AUTO_INVALIDATING_DELIVERY_ERRORS
        ):
            occurred = processed.last_failed_at or require_aware(self._clock(), "now")
            await self.invalidate_automation(
                automation.id,
                reason_code=f"delivery_{processed.error_code}",
                now=occurred,
            )
        return processed

    async def _emit_lifecycle_event(
        self,
        automation: Automation,
        *,
        action: str,
        previous_state: AutomationState | None,
        reason_code: str | None,
        occurred_at: datetime,
    ) -> None:
        if self._event_sink is None:
            return
        await self._event_sink(
            {
                "type": "automation.lifecycle",
                "automation_id": automation.id,
                "automation_revision": automation.revision,
                "action": action,
                "state": automation.state.value,
                "previous_state": None if previous_state is None else previous_state.value,
                "invalidation_reason_code": reason_code,
                "invalidated_at": (
                    None
                    if automation.invalidated_at is None
                    else automation.invalidated_at.isoformat()
                ),
                "occurred_at": require_aware(occurred_at, "occurred_at").isoformat(),
            }
        )

    async def _emit_event_visibility_rejection(
        self,
        automation: Automation,
        *,
        reason_code: str,
    ) -> None:
        """Audit scope rejection without including inaccessible Event/resource identifiers."""

        if self._event_sink is None:
            return
        await self._event_sink(
            {
                "type": "automation.event_visibility",
                "automation_id": automation.id,
                "automation_revision": automation.revision,
                "outcome": "rejected",
                "reason_code": reason_code,
                "project_scoped": automation.project_id is not None,
                "workspace_scoped": automation.workspace_id is not None,
            }
        )

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


async def _canonical_event_visibility(
    automation: Automation,
    event: PlatformEvent,
    workspace_resolver: WorkspaceEventScopeResolver | None,
) -> tuple[bool, str | None]:
    """Apply project/workspace/owner visibility before any Automation trigger filters."""

    if automation.project_id is not None and event.project_id != automation.project_id:
        return False, "project_scope_mismatch"

    if automation.workspace_id is not None:
        if workspace_resolver is None:
            return False, "workspace_scope_unproven"
        try:
            event_workspace_id = await workspace_resolver.resolve_workspace_id(event)
        except Exception:
            # Visibility resolution is security-sensitive: any resolver/backend failure must
            # reject rather than accidentally broadening access.
            return False, "workspace_scope_resolution_failed"
        if event_workspace_id is None:
            return False, "workspace_scope_unproven"
        if event_workspace_id != automation.workspace_id:
            return False, "workspace_scope_mismatch"
        if automation.project_id is not None:
            return True, None

    if automation.project_id is not None:
        return True, None

    if event.owner_ref is not None:
        visible = (
            event.owner_ref.type == automation.identity.owner_type
            and event.owner_ref.id == automation.identity.owner_id
        )
        return (True, None) if visible else (False, "owner_scope_mismatch")

    visible = event.project_id is None and automation.identity.owner_type == "service"
    return (True, None) if visible else (False, "owner_scope_unproven")


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
