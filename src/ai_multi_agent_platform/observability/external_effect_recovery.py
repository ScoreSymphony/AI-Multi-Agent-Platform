"""Safe #16 telemetry projection for uncertain external-effect recovery."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities import (
    ExternalEffectRecoveryDisposition,
    ExternalEffectRecoveryEvent,
    ExternalEffectRecoveryEventObserver,
    ExternalEffectRecoveryStatus,
)

from .exporters import Telemetry
from .models import (
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)


class ObservabilityExternalEffectRecoveryObserver(ExternalEffectRecoveryEventObserver):
    """Export content-free recovery transitions without provider-native/private values."""

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry

    async def record_external_effect_recovery(
        self,
        event: ExternalEffectRecoveryEvent,
    ) -> None:
        context = TelemetryContext(
            task_id=event.task_id,
            run_id=event.run_id,
            tool_invocation_id=event.invocation_id,
            capability_id=event.capability_id,
            provider_id=event.provider_id,
        )
        attributes = {
            "external_effect_id": event.effect_id,
            "status": event.status.value,
            "disposition": event.disposition.value,
            "reason": event.reason,
        }
        outcome = _outcome(event.status)
        severity = _severity(event)

        self._telemetry.metric(
            "platform.external_effect.recovery_events",
            1.0,
            context=context,
            attributes={
                "event_name": event.event_name,
                "status": event.status.value,
                "disposition": event.disposition.value,
            },
            timestamp=event.occurred_at,
        )
        self._telemetry.log(
            severity=severity,
            component=FailureComponent.CAPABILITY_TOOL,
            event_name=event.event_name,
            context=context,
            outcome=outcome,
            attributes=attributes,
        )
        self._telemetry.timeline(
            event_name=event.event_name,
            component=FailureComponent.CAPABILITY_TOOL,
            context=context,
            timestamp=event.occurred_at,
            outcome=outcome,
            attributes=attributes,
        )


def _outcome(status: ExternalEffectRecoveryStatus) -> TelemetryOutcome:
    if status is ExternalEffectRecoveryStatus.SUCCEEDED:
        return TelemetryOutcome.SUCCEEDED
    if status is ExternalEffectRecoveryStatus.FAILED:
        return TelemetryOutcome.FAILED
    return TelemetryOutcome.UNKNOWN


def _severity(event: ExternalEffectRecoveryEvent) -> TelemetrySeverity:
    if event.status is ExternalEffectRecoveryStatus.FAILED:
        return TelemetrySeverity.ERROR
    if event.disposition in {
        ExternalEffectRecoveryDisposition.UNCERTAIN_MANUAL_REVIEW,
        ExternalEffectRecoveryDisposition.BLOCKED_DEPENDENCY,
        ExternalEffectRecoveryDisposition.BLOCKED_OPERATOR_ACTION,
    }:
        return TelemetrySeverity.WARNING
    return TelemetrySeverity.INFO


__all__ = ["ObservabilityExternalEffectRecoveryObserver"]
