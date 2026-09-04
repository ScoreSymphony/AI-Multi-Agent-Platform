"""Cross-domain evidence projection for canonical evaluation observations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Protocol

from ai_multi_agent_platform.accounting import (
    AccountingService,
    UsageQuery,
    UsageRecord,
    UsageScope,
    aggregate_usage_records,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    InMemoryExporter,
    StructuredLog,
)

from .context import EvaluationExecutionContext
from .contracts import EvaluationCaseExecutor
from .models import EvaluationAttempt, EvaluationCase, EvaluationObservation


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class EvaluationEvidence:
    """Additive evidence projected from platform-owned cross-cutting domains."""

    data: dict[str, JsonValue] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    telemetry_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in self.metrics.items():
            if not name.strip():
                raise ValueError("evaluation evidence metric names must not be blank")
            if not isfinite(value):
                raise ValueError("evaluation evidence metrics must be finite")
        if any(not ref.strip() for ref in self.telemetry_refs):
            raise ValueError("evaluation evidence references must not be blank")


class EvaluationEvidenceProvider(Protocol):
    """Project source-owned evidence for one canonical Task/Run pair."""

    def collect(self, *, task_id: str, run_id: str) -> EvaluationEvidence: ...


class CompositeEvaluationEvidenceProvider:
    """Compose evidence providers without silently overwriting one another."""

    def __init__(self, providers: tuple[EvaluationEvidenceProvider, ...]) -> None:
        self._providers = providers

    def collect(self, *, task_id: str, run_id: str) -> EvaluationEvidence:
        data: dict[str, JsonValue] = {}
        metrics: dict[str, float] = {}
        refs: list[str] = []
        for provider in self._providers:
            evidence = provider.collect(task_id=task_id, run_id=run_id)
            data_collision = set(data).intersection(evidence.data)
            if data_collision:
                names = ", ".join(sorted(data_collision))
                raise ValueError(f"evaluation evidence data keys collide: {names}")
            metric_collision = set(metrics).intersection(evidence.metrics)
            if metric_collision:
                names = ", ".join(sorted(metric_collision))
                raise ValueError(f"evaluation evidence metric keys collide: {names}")
            data.update(evidence.data)
            metrics.update(evidence.metrics)
            refs.extend(evidence.telemetry_refs)
        return EvaluationEvidence(
            data=data,
            metrics=metrics,
            telemetry_refs=_unique(tuple(refs)),
        )


def _accounting_records(
    accounting: AccountingService,
    *,
    task_id: str,
    run_id: str,
) -> tuple[UsageRecord, ...]:
    """Resolve all usage attributable to the exact run plus task-only measurements."""

    by_id: dict[str, UsageRecord] = {}
    for record in accounting.query(UsageQuery(scope=UsageScope(run_id=run_id))):
        if record.scope.task_id is not None and record.scope.task_id != task_id:
            continue
        by_id[record.id] = record
    for record in accounting.query(UsageQuery(scope=UsageScope(task_id=task_id))):
        if record.scope.run_id not in {None, run_id}:
            continue
        by_id[record.id] = record
    return tuple(sorted(by_id.values(), key=lambda item: (item.timestamp, item.id)))


def _usage_record_payload(record: UsageRecord) -> dict[str, JsonValue]:
    return {
        "usage_id": record.id,
        "metric_type": record.metric_type,
        "unit": record.unit,
        "quantity": record.quantity,
        "quality": record.quality.value,
        "source": record.source,
        "aggregation_mode": record.aggregation_mode.value,
        "provider": record.provider,
        "correlation_id": record.correlation_id,
        "causation_id": record.causation_id,
    }


class AccountingEvaluationEvidenceProvider:
    """Project canonical #76 UsageRecord evidence without fabricating missing metrics."""

    def __init__(self, accounting: AccountingService) -> None:
        self._accounting = accounting

    def collect(self, *, task_id: str, run_id: str) -> EvaluationEvidence:
        records = _accounting_records(self._accounting, task_id=task_id, run_id=run_id)
        grouped: dict[tuple[str, str], list[UsageRecord]] = {}
        for record in records:
            grouped.setdefault((record.metric_type, record.unit), []).append(record)

        metrics: dict[str, float] = {}
        aggregates: list[JsonValue] = []
        for metric_type, unit in sorted(grouped):
            group = tuple(grouped[(metric_type, unit)])
            aggregate = aggregate_usage_records(
                group,
                metric_type=metric_type,
                unit=unit,
            )
            metric_name = f"accounting:{metric_type}:{unit}"
            if aggregate.total is not None:
                metrics[metric_name] = aggregate.total
            aggregates.append(
                {
                    "metric_name": metric_name,
                    "metric_type": metric_type,
                    "unit": unit,
                    "total": aggregate.total,
                    "record_count": aggregate.record_count,
                    "unavailable_count": aggregate.unavailable_count,
                    "aggregation_mode": aggregate.aggregation_mode.value,
                    "quality_counts": {
                        quality.value: count
                        for quality, count in aggregate.quality_counts.items()
                    },
                }
            )

        return EvaluationEvidence(
            data={
                "accounting_evidence": {
                    "records": [_usage_record_payload(record) for record in records],
                    "aggregates": aggregates,
                }
            },
            metrics=metrics,
            telemetry_refs=tuple(f"accounting:usage:{record.id}" for record in records),
        )


LogReferenceResolver = Callable[[StructuredLog], str | None]


def _context_matches(
    *,
    task_id: str | None,
    run_id: str | None,
    expected_task_id: str,
    expected_run_id: str,
) -> bool:
    return task_id == expected_task_id and run_id in {None, expected_run_id}


class InMemoryObservabilityEvaluationEvidenceProvider:
    """Reference #16 evidence projection from the local in-memory exporter.

    Structured logs do not currently own canonical IDs. A log reference is emitted
    only when the configured resolver returns a source-owned reference.
    """

    def __init__(
        self,
        exporter: InMemoryExporter,
        *,
        log_reference_resolver: LogReferenceResolver | None = None,
    ) -> None:
        self._exporter = exporter
        self._log_reference_resolver = log_reference_resolver

    def collect(self, *, task_id: str, run_id: str) -> EvaluationEvidence:
        spans = tuple(
            span
            for span in tuple(self._exporter.spans)
            if _context_matches(
                task_id=span.context.task_id,
                run_id=span.context.run_id,
                expected_task_id=task_id,
                expected_run_id=run_id,
            )
        )
        logs = tuple(
            log
            for log in tuple(self._exporter.logs)
            if _context_matches(
                task_id=log.context.task_id,
                run_id=log.context.run_id,
                expected_task_id=task_id,
                expected_run_id=run_id,
            )
        )
        timeline = tuple(
            entry
            for entry in tuple(self._exporter.timeline)
            if _context_matches(
                task_id=entry.context.task_id,
                run_id=entry.context.run_id,
                expected_task_id=task_id,
                expected_run_id=run_id,
            )
        )

        refs: list[str] = []
        for span in spans:
            refs.append(f"observability:trace:{span.trace_id}")
            refs.append(f"observability:span:{span.span_id}")
            if span.context.correlation_id is not None:
                refs.append(f"observability:correlation:{span.context.correlation_id}")
        if self._log_reference_resolver is not None:
            for log in logs:
                reference = self._log_reference_resolver(log)
                if reference is None:
                    continue
                if not reference.strip():
                    raise ValueError("observability log reference resolver returned a blank reference")
                refs.append(f"observability:log:{reference}")

        span_data: list[JsonValue] = [
            {
                "trace_id": span.trace_id,
                "span_id": span.span_id,
                "parent_span_id": span.parent_span_id,
                "name": span.name,
                "outcome": span.outcome.value,
                "duration_seconds": span.duration_seconds,
                "correlation_id": span.context.correlation_id,
            }
            for span in spans
        ]
        log_data: list[JsonValue] = [
            {
                "event_name": log.event_name,
                "severity": log.severity.value,
                "component": log.component.value,
                "outcome": log.outcome.value,
                "timestamp": log.timestamp.isoformat(),
            }
            for log in logs
        ]
        timeline_data: list[JsonValue] = [
            {
                "event_name": entry.event_name,
                "component": entry.component.value,
                "outcome": entry.outcome.value,
                "timestamp": entry.timestamp.isoformat(),
            }
            for entry in timeline
        ]
        return EvaluationEvidence(
            data={
                "observability_evidence": {
                    "spans": span_data,
                    "logs": log_data,
                    "timeline": timeline_data,
                }
            },
            telemetry_refs=_unique(tuple(refs)),
        )


class EvidenceEnrichingCaseExecutor:
    """Decorate any case executor with source-owned cross-domain evidence projection."""

    def __init__(
        self,
        executor: EvaluationCaseExecutor,
        evidence_provider: EvaluationEvidenceProvider,
    ) -> None:
        self._executor = executor
        self._evidence_provider = evidence_provider

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        observation = await self._executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=execution_context,
        )
        if observation.task_id is None or observation.run_id is None:
            raise ValueError(
                "evidence-enriched evaluation execution requires canonical task_id and run_id"
            )
        evidence = self._evidence_provider.collect(
            task_id=observation.task_id,
            run_id=observation.run_id,
        )
        data_collision = set(observation.data).intersection(evidence.data)
        if data_collision:
            names = ", ".join(sorted(data_collision))
            raise ValueError(f"evaluation evidence would overwrite observation data: {names}")
        metric_collision = set(observation.metrics).intersection(evidence.metrics)
        if metric_collision:
            names = ", ".join(sorted(metric_collision))
            raise ValueError(f"evaluation evidence would overwrite observation metrics: {names}")
        return replace(
            observation,
            data={**observation.data, **evidence.data},
            metrics={**observation.metrics, **evidence.metrics},
            telemetry_refs=_unique((*observation.telemetry_refs, *evidence.telemetry_refs)),
        )


__all__ = [
    "AccountingEvaluationEvidenceProvider",
    "CompositeEvaluationEvidenceProvider",
    "EvaluationEvidence",
    "EvaluationEvidenceProvider",
    "EvidenceEnrichingCaseExecutor",
    "InMemoryObservabilityEvaluationEvidenceProvider",
    "LogReferenceResolver",
]
