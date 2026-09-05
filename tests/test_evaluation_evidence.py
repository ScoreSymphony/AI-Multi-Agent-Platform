from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AggregationMode,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.evaluation import (
    AccountingEvaluationEvidenceProvider,
    CompositeEvaluationEvidenceProvider,
    ConfigurationSnapshot,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationEvidence,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRun,
    EvidenceEnrichingCaseExecutor,
    InMemoryObservabilityEvaluationEvidenceProvider,
    SqliteEvaluationRepository,
)
from ai_multi_agent_platform.observability import (
    FailureComponent,
    InMemoryExporter,
    SpanRecord,
    StructuredLog,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
    TimelineEntry,
)


class StaticCaseExecutor:
    def __init__(self, observation: EvaluationObservation) -> None:
        self.observation = observation

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case
        if attempt.attempt_id != execution_context.attempt_id:
            raise ValueError("wrong execution context")
        return self.observation


class StaticEvidenceProvider:
    def __init__(self, evidence: EvaluationEvidence) -> None:
        self.evidence = evidence

    def collect(self, *, task_id: str, run_id: str) -> EvaluationEvidence:
        assert task_id == "task_target"
        assert run_id == "run_target"
        return self.evidence


def _record(
    *,
    metric_type: str,
    unit: str,
    quality: MeasurementQuality,
    task_id: str,
    run_id: str | None,
    quantity: float | None,
    timestamp: datetime,
    aggregation_mode: AggregationMode = AggregationMode.ADDITIVE,
) -> UsageRecord:
    return UsageRecord(
        metric_type=metric_type,
        unit=unit,
        quality=quality,
        source="test",
        quantity=quantity,
        scope=UsageScope(task_id=task_id, run_id=run_id),
        timestamp=timestamp,
        aggregation_mode=aggregation_mode,
    )


def test_accounting_evidence_preserves_usage_identity_quality_and_missing_metrics() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    now = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    target_a = _record(
        metric_type="run.duration",
        unit="seconds",
        quality=MeasurementQuality.MEASURED,
        task_id="task_target",
        run_id="run_target",
        quantity=1.5,
        timestamp=now,
    )
    target_b = _record(
        metric_type="run.duration",
        unit="seconds",
        quality=MeasurementQuality.REPORTED,
        task_id="task_target",
        run_id="run_target",
        quantity=2.0,
        timestamp=now + timedelta(seconds=1),
    )
    unavailable = _record(
        metric_type="worker.gpu.memory",
        unit="bytes",
        quality=MeasurementQuality.UNAVAILABLE,
        task_id="task_target",
        run_id="run_target",
        quantity=None,
        timestamp=now + timedelta(seconds=2),
    )
    other_run = _record(
        metric_type="run.duration",
        unit="seconds",
        quality=MeasurementQuality.MEASURED,
        task_id="task_target",
        run_id="run_other",
        quantity=99.0,
        timestamp=now + timedelta(seconds=3),
    )
    for record in (target_a, target_b, unavailable, other_run):
        accounting.record(record)

    evidence = AccountingEvaluationEvidenceProvider(accounting).collect(
        task_id="task_target",
        run_id="run_target",
    )

    assert evidence.metrics["accounting:run.duration:seconds"] == pytest.approx(3.5)
    assert "accounting:worker.gpu.memory:bytes" not in evidence.metrics
    assert evidence.telemetry_refs == (
        f"accounting:usage:{target_a.id}",
        f"accounting:usage:{target_b.id}",
        f"accounting:usage:{unavailable.id}",
    )
    payload = evidence.data["accounting_evidence"]
    assert isinstance(payload, dict)
    records = payload["records"]
    assert isinstance(records, list)
    assert [item["quality"] for item in records if isinstance(item, dict)] == [
        "measured",
        "reported",
        "unavailable",
    ]
    assert other_run.id not in str(payload)


def test_accounting_evidence_respects_latest_gauge_semantics() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    now = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    first = _record(
        metric_type="node.memory.current",
        unit="bytes",
        quality=MeasurementQuality.MEASURED,
        task_id="task_target",
        run_id="run_target",
        quantity=100.0,
        timestamp=now,
        aggregation_mode=AggregationMode.LATEST,
    )
    latest = _record(
        metric_type="node.memory.current",
        unit="bytes",
        quality=MeasurementQuality.MEASURED,
        task_id="task_target",
        run_id="run_target",
        quantity=140.0,
        timestamp=now + timedelta(seconds=1),
        aggregation_mode=AggregationMode.LATEST,
    )
    accounting.record(first)
    accounting.record(latest)

    evidence = AccountingEvaluationEvidenceProvider(accounting).collect(
        task_id="task_target",
        run_id="run_target",
    )

    assert evidence.metrics["accounting:node.memory.current:bytes"] == 140.0
    payload = evidence.data["accounting_evidence"]
    assert isinstance(payload, dict)
    aggregates = payload["aggregates"]
    assert isinstance(aggregates, list)
    first_aggregate = aggregates[0]
    assert isinstance(first_aggregate, dict)
    assert first_aggregate["aggregation_mode"] == "latest"


def test_observability_evidence_uses_source_trace_ids_and_optional_log_refs() -> None:
    exporter = InMemoryExporter()
    context = TelemetryContext(
        task_id="task_target",
        run_id="run_target",
        correlation_id="correlation_target",
    )
    other_context = TelemetryContext(task_id="task_target", run_id="run_other")
    started = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    exporter.emit_span(
        SpanRecord(
            name="executor.run",
            trace_id="trace_target",
            span_id="span_target",
            context=context,
            started_at=started,
            finished_at=started + timedelta(seconds=1),
            duration_seconds=1.0,
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={"secret": "must-not-be-projected"},
        )
    )
    exporter.emit_log(
        StructuredLog(
            severity=TelemetrySeverity.INFO,
            component=FailureComponent.EXECUTION,
            event_name="executor.completed",
            context=context,
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={"payload": "must-not-be-projected"},
            timestamp=started + timedelta(seconds=1),
        )
    )
    exporter.emit_timeline(
        TimelineEntry(
            event_name="run.completed",
            component=FailureComponent.EXECUTION,
            context=context,
            timestamp=started + timedelta(seconds=1),
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={"payload": "must-not-be-projected"},
        )
    )
    exporter.emit_span(
        SpanRecord(
            name="other",
            trace_id="trace_other",
            span_id="span_other",
            context=other_context,
            started_at=started,
            finished_at=started,
            duration_seconds=0.0,
        )
    )

    provider = InMemoryObservabilityEvaluationEvidenceProvider(
        exporter,
        log_reference_resolver=lambda log: f"local-log:{log.event_name}",
    )
    evidence = provider.collect(task_id="task_target", run_id="run_target")

    assert evidence.telemetry_refs == (
        "observability:trace:trace_target",
        "observability:span:span_target",
        "observability:correlation:correlation_target",
        "observability:log:local-log:executor.completed",
    )
    assert "trace_other" not in str(evidence.data)
    assert "must-not-be-projected" not in str(evidence.data)


def test_observability_evidence_does_not_invent_log_identity() -> None:
    exporter = InMemoryExporter()
    exporter.emit_log(
        StructuredLog(
            severity=TelemetrySeverity.INFO,
            component=FailureComponent.EXECUTION,
            event_name="executor.completed",
            context=TelemetryContext(task_id="task_target", run_id="run_target"),
        )
    )

    evidence = InMemoryObservabilityEvaluationEvidenceProvider(exporter).collect(
        task_id="task_target",
        run_id="run_target",
    )

    assert not any(ref.startswith("observability:log:") for ref in evidence.telemetry_refs)
    payload = evidence.data["observability_evidence"]
    assert isinstance(payload, dict)
    logs = payload["logs"]
    assert isinstance(logs, list)
    first_log = logs[0]
    assert isinstance(first_log, dict)
    assert first_log["event_name"] == "executor.completed"


def test_composite_and_executor_enrichment_reject_silent_collisions() -> None:
    provider = CompositeEvaluationEvidenceProvider(
        (
            StaticEvidenceProvider(EvaluationEvidence(data={"accounting_evidence": {}})),
            StaticEvidenceProvider(EvaluationEvidence(data={"accounting_evidence": {}})),
        )
    )
    with pytest.raises(ValueError, match="data keys collide"):
        provider.collect(task_id="task_target", run_id="run_target")

    attempt = EvaluationAttempt(
        evaluation_run_id="evaluation_run_test",
        case_id="case",
        case_version="1",
        repetition_index=0,
    )
    wrapped = EvidenceEnrichingCaseExecutor(
        StaticCaseExecutor(
            EvaluationObservation(
                task_id="task_target",
                run_id="run_target",
                metrics={"existing": 1.0},
            )
        ),
        StaticEvidenceProvider(EvaluationEvidence(metrics={"existing": 2.0})),
    )
    with pytest.raises(ValueError, match="overwrite observation metrics"):
        asyncio.run(
            wrapped.execute_case(
                case=EvaluationCase(case_id="case", name="Case", version="1"),
                attempt=attempt,
                execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
            )
        )


def test_enriched_evidence_flows_into_result_and_survives_sqlite_restart(
    tmp_path: Path,
) -> None:
    attempt = EvaluationAttempt(
        evaluation_run_id="evaluation_run_test",
        case_id="case",
        case_version="1",
        repetition_index=0,
    )
    evidence = EvaluationEvidence(
        data={"accounting_evidence": {"records": []}},
        metrics={"accounting:run.duration:seconds": 2.5},
        telemetry_refs=(
            "accounting:usage:usage_00000000-0000-0000-0000-000000000001",
            "observability:trace:trace_target",
            "observability:span:span_target",
            "observability:log:local-log:1",
        ),
    )
    wrapped = EvidenceEnrichingCaseExecutor(
        StaticCaseExecutor(
            EvaluationObservation(
                data={"base": True},
                task_id="task_target",
                run_id="run_target",
                telemetry_refs=("existing:telemetry",),
            )
        ),
        StaticEvidenceProvider(evidence),
    )
    case = EvaluationCase(case_id="case", name="Case", version="1")
    observation = asyncio.run(
        wrapped.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )
    )

    assert observation.metrics["accounting:run.duration:seconds"] == 2.5
    assert observation.telemetry_refs == (
        "existing:telemetry",
        *evidence.telemetry_refs,
    )

    result = DeterministicAssertionEvaluator().evaluate(
        evaluation_run_id=attempt.evaluation_run_id,
        case=case,
        observation=observation,
    )
    assert result.telemetry_refs == observation.telemetry_refs

    repository = SqliteEvaluationRepository(tmp_path / "evaluation.sqlite3")
    repository.save_run(
        EvaluationRun(
            run_id=attempt.evaluation_run_id,
            suite_id="suite",
            suite_version="1",
            snapshot=ConfigurationSnapshot(platform_version="0.0.1", platform_commit="test"),
        )
    )
    repository.save_result(result)

    reopened = SqliteEvaluationRepository(tmp_path / "evaluation.sqlite3")
    assert reopened.list_results(attempt.evaluation_run_id)[0].telemetry_refs == (
        observation.telemetry_refs
    )
