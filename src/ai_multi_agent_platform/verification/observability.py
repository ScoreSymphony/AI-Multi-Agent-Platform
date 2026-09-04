"""#16 timeline projection for canonical Verification audit history (#86)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    FailureClassification,
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TimelineEntry,
)

from .audit import VerificationAuditEvent, VerificationAuditEventType
from .models import VerificationOutcome
from .service import VerificationService


class VerificationTimelineReader:
    """Derived #16 TimelineReader backed only by canonical Verification audit facts.

    This projection is intentionally read-only. Completion policy never reads telemetry;
    it continues to use VerificationService and CompletionAuthority state directly.
    """

    def __init__(self, verification: VerificationService) -> None:
        self._verification = verification

    def query_timeline(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]:
        entries = (
            _timeline_entry(event)
            for event in self._verification.audit_history(
                task_id=task_id,
                verification_id=None,
            )
            if (run_id is None or event.run_id == run_id)
            and (correlation_id is None or event.correlation_id == correlation_id)
        )
        return tuple(sorted(entries, key=lambda entry: entry.timestamp))


def _timeline_entry(event: VerificationAuditEvent) -> TimelineEntry:
    outcome, failure = _outcome(event)
    return TimelineEntry(
        event_name=event.event_type.value,
        component=FailureComponent.VERIFICATION,
        context=TelemetryContext(
            project_id=event.project_id,
            task_id=event.task_id,
            run_id=event.run_id,
            agent_id=None if event.verifier is None else event.verifier.agent_id,
            model_config_id=(None if event.verifier is None else event.verifier.model_config_id),
            model_provider_id=(None if event.verifier is None else event.verifier.provider_id),
            verification_id=event.verification_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            provider_id=None if event.verifier is None else event.verifier.provider_id,
        ),
        timestamp=event.occurred_at,
        outcome=outcome,
        failure=failure,
        attributes=_attributes(event),
    )


def _outcome(
    event: VerificationAuditEvent,
) -> tuple[TelemetryOutcome, FailureClassification | None]:
    if event.event_type is VerificationAuditEventType.REQUEST_EXPIRED:
        return (
            TelemetryOutcome.TIMED_OUT,
            FailureClassification(
                component=FailureComponent.VERIFICATION,
                code="verification_request_expired",
                retryable=False,
            ),
        )
    if event.event_type is not VerificationAuditEventType.RESULT_RECORDED:
        return TelemetryOutcome.UNKNOWN, None
    if event.outcome is VerificationOutcome.PASS:
        return TelemetryOutcome.SUCCEEDED, None
    if event.outcome is VerificationOutcome.INCONCLUSIVE:
        return TelemetryOutcome.UNKNOWN, None
    if event.outcome is VerificationOutcome.NEEDS_CHANGES:
        code = "verification_changes_required"
    else:
        code = "verification_failed"
    return (
        TelemetryOutcome.FAILED,
        FailureClassification(
            component=FailureComponent.VERIFICATION,
            code=code,
            retryable=False,
        ),
    )


def _attributes(event: VerificationAuditEvent) -> dict[str, JsonValue]:
    attributes: dict[str, JsonValue] = {
        "audit_event_id": event.event_id,
        "repair_attempt": event.repair_attempt,
        "evidence_artifact_ids": list(event.evidence_artifact_ids),
        "checks_executed": list(event.checks_executed),
    }
    for key, value in (
        ("policy_id", event.policy_id),
        ("policy_version", event.policy_version),
        ("stage_id", event.stage_id),
        (
            "requested_verifier_kind",
            None if event.requested_verifier_kind is None else event.requested_verifier_kind.value,
        ),
        ("outcome", None if event.outcome is None else event.outcome.value),
        ("verifier_ref", None if event.verifier is None else event.verifier.verifier_ref),
        ("verifier_kind", None if event.verifier is None else event.verifier.kind.value),
    ):
        if value is not None:
            attributes[key] = value
    if event.subject is not None:
        attributes["subject"] = {
            "type": event.subject.subject_type,
            "id": event.subject.subject_id,
            "revision": event.subject.revision,
            "digest": event.subject.digest,
        }
    if event.metadata:
        attributes["metadata"] = dict(event.metadata)
    return attributes
