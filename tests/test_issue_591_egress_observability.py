from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import (
    DataClassification,
    EgressAuditEvent,
    EgressAuditEventType,
    EgressCostClass,
    EgressOutcome,
    EgressReasonCode,
    EgressTargetKind,
    EgressTargetPosture,
    digest_egress_payload,
)
from ai_multi_agent_platform.observability import (
    EgressTelemetryAuditSink,
    InMemoryExporter,
    Telemetry,
)


def test_egress_telemetry_projects_only_value_free_evidence() -> None:
    protected = "do-not-log-this-protected-payload"
    exporter = InMemoryExporter()
    sink = EgressTelemetryAuditSink(Telemetry(exporter))
    event = EgressAuditEvent(
        event_type=EgressAuditEventType.DENIED,
        request_id="egress-observability-591",
        target_kind=EgressTargetKind.MODEL_PROVIDER,
        target_id="model-config-591",
        target_posture=EgressTargetPosture.EXTERNAL,
        classification=DataClassification.CONFIDENTIAL,
        outcome=EgressOutcome.DENY,
        reason_code=EgressReasonCode.PAID_EXTERNAL_DENIED,
        payload_digest=digest_egress_payload(protected),
        policy_version="egress-policy/v2",
        correlation_id="corr-egress-observability-591",
        project_id="project-egress-observability-591",
        task_id="task-egress-observability-591",
        run_id="run-egress-observability-591",
        profile_ref="egress-profile-591@7",
        cost_class=EgressCostClass.PAID_EXTERNAL,
        audit_metadata={"profile_trust": "verified", "source_revision": "operator:7"},
    )

    asyncio.run(sink.record(event))

    assert len(exporter.timeline) == 1
    assert len(exporter.logs) == 1
    assert len(exporter.metrics) == 1
    timeline = exporter.timeline[0]
    assert timeline.event_name == "EgressDenied"
    assert timeline.context.project_id == "project-egress-observability-591"
    assert timeline.context.task_id == "task-egress-observability-591"
    assert timeline.context.run_id == "run-egress-observability-591"
    assert timeline.attributes["payload_digest"] == digest_egress_payload(protected)
    assert protected not in repr(timeline.attributes)
    assert protected not in repr(exporter.logs[0].attributes)


def test_egress_evaluated_records_one_decision_metric_without_failure_log() -> None:
    exporter = InMemoryExporter()
    sink = EgressTelemetryAuditSink(Telemetry(exporter))
    event = EgressAuditEvent(
        event_type=EgressAuditEventType.EVALUATED,
        request_id="egress-evaluated-591",
        target_kind=EgressTargetKind.CAPABILITY,
        target_id="capability-egress-591",
        target_posture=EgressTargetPosture.EXTERNAL,
        classification=DataClassification.PUBLIC,
        outcome=EgressOutcome.ALLOW,
        reason_code=EgressReasonCode.ALLOWED,
        payload_digest=digest_egress_payload("public-subject"),
        policy_version="egress-policy/v2",
        correlation_id="corr-egress-evaluated-591",
    )

    asyncio.run(sink.record(event))

    assert len(exporter.timeline) == 1
    assert exporter.logs == []
    assert len(exporter.metrics) == 1
    assert exporter.metrics[0].name == "platform.egress.decisions"
