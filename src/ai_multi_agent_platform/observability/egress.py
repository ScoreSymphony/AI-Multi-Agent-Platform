"""Observability bridge for value-free canonical egress audit events."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    EgressAuditEvent,
    EgressAuditEventType,
    EgressAuditSink,
    EgressTargetKind,
    JsonValue,
)

from .exporters import Telemetry
from .models import (
    FailureClassification,
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)


class EgressTelemetryAuditSink(EgressAuditSink):
    """Project #591 audit evidence into #16 without ever receiving outbound payload values."""

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry

    async def record(self, event: EgressAuditEvent) -> None:
        component = _component(event.target_kind)
        context = TelemetryContext(
            project_id=event.project_id,
            task_id=event.task_id,
            run_id=event.run_id,
            capability_id=event.capability_id,
            approval_id=event.approval_ref,
            correlation_id=event.correlation_id,
        )
        attributes: dict[str, JsonValue] = {
            "egress_request_id": event.request_id,
            "target_kind": event.target_kind.value,
            "target_id": event.target_id,
            "target_posture": event.target_posture.value,
            "classification": (
                None if event.classification is None else event.classification.value
            ),
            "outcome": event.outcome.value,
            "reason_code": event.reason_code.value,
            "payload_digest": event.payload_digest,
            "policy_version": event.policy_version,
            "profile_ref": event.profile_ref,
            "cost_class": None if event.cost_class is None else event.cost_class.value,
            "approval_ref": event.approval_ref,
            **dict(event.audit_metadata),
        }

        if event.event_type is EgressAuditEventType.ALLOWED:
            outcome = TelemetryOutcome.SUCCEEDED
            severity = TelemetrySeverity.INFO
            failure = None
        elif event.event_type is EgressAuditEventType.DENIED:
            outcome = TelemetryOutcome.FAILED
            severity = TelemetrySeverity.WARNING
            failure = FailureClassification(
                component=component,
                code=f"egress_{event.reason_code.value}",
                retryable=False,
            )
        else:
            outcome = TelemetryOutcome.UNKNOWN
            severity = TelemetrySeverity.INFO
            failure = None

        self.telemetry.timeline(
            event_name=event.event_type.value,
            component=component,
            context=context,
            outcome=outcome,
            failure=failure,
            attributes=attributes,
        )
        if event.event_type is EgressAuditEventType.EVALUATED:
            self.telemetry.metric(
                "platform.egress.decisions",
                1.0,
                context=context,
                attributes={
                    "target_kind": event.target_kind.value,
                    "outcome": event.outcome.value,
                    "reason_code": event.reason_code.value,
                },
            )
        elif event.event_type is EgressAuditEventType.DENIED:
            self.telemetry.metric(
                "platform.egress.denied",
                1.0,
                context=context,
                attributes={
                    "target_kind": event.target_kind.value,
                    "reason_code": event.reason_code.value,
                },
            )
            self.telemetry.log(
                severity=severity,
                component=component,
                event_name=event.event_type.value,
                context=context,
                outcome=outcome,
                failure=failure,
                attributes=attributes,
            )


def _component(kind: EgressTargetKind) -> FailureComponent:
    if kind is EgressTargetKind.MODEL_PROVIDER:
        return FailureComponent.MODEL_PROVIDER_ROUTER
    if kind is EgressTargetKind.CAPABILITY:
        return FailureComponent.CAPABILITY_TOOL
    if kind is EgressTargetKind.CONNECTOR:
        return FailureComponent.CONNECTOR_BROWSER
    if kind is EgressTargetKind.WORKER:
        return FailureComponent.SCHEDULER_WORKER_NODE
    if kind in {
        EgressTargetKind.CONTEXT_EXPORT,
        EgressTargetKind.FILE_EXPORT,
        EgressTargetKind.ARTIFACT_EXPORT,
    }:
        return FailureComponent.PERSISTENCE_STORAGE
    return FailureComponent.INFRASTRUCTURE_UNKNOWN


__all__ = ["EgressTelemetryAuditSink"]
