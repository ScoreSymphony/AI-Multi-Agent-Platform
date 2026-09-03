"""Accounting ingestion, aggregation and budget evaluation services."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability.models import MetricRecord

from .models import (
    BudgetState,
    BudgetThresholdEvent,
    MeasurementQuality,
    ThresholdLevel,
    UsageAggregate,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
)
from .store import UsageStore

ThresholdEventSink = Callable[[BudgetThresholdEvent], None]


class AccountingService:
    """Durable #76 owner and structural implementation of observability.MeasurementSink."""

    def __init__(
        self,
        store: UsageStore,
        *,
        threshold_event_sink: ThresholdEventSink | None = None,
    ) -> None:
        self.store = store
        self.threshold_event_sink = threshold_event_sink

    def ingest_metric(self, record: MetricRecord) -> None:
        usage = usage_from_metric(record)
        if usage is not None:
            self.record(usage)

    def record(self, record: UsageRecord) -> None:
        if self.store.append(record):
            self._evaluate_matching_budgets(record)

    def record_unavailable(
        self,
        *,
        metric_type: str,
        unit: str,
        source: str,
        scope: UsageScope | None = None,
        provider: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> UsageRecord:
        record = UsageRecord(
            metric_type=metric_type,
            unit=unit,
            quality=MeasurementQuality.UNAVAILABLE,
            source=source,
            scope=scope or UsageScope(),
            provider=provider,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self.record(record)
        return record

    def query(self, query: UsageQuery | None = None) -> tuple[UsageRecord, ...]:
        return self.store.query(query or UsageQuery())

    def aggregate(self, query: UsageQuery) -> UsageAggregate:
        records = self.store.query(query)
        if query.metric_type is None or query.unit is None:
            raise ValueError("aggregate query requires metric_type and unit")
        values = [record.quantity for record in records if record.quantity is not None]
        quality_counts = {quality: 0 for quality in MeasurementQuality}
        for record in records:
            quality_counts[record.quality] += 1
        return UsageAggregate(
            metric_type=query.metric_type,
            unit=query.unit,
            total=sum(values) if values else None,
            record_count=len(records),
            unavailable_count=quality_counts[MeasurementQuality.UNAVAILABLE],
            quality_counts=quality_counts,
            start=query.start,
            end=query.end,
        )

    def put_budget(self, budget: UsageBudget) -> BudgetState:
        self.store.put_budget(budget)
        state = self.budget_state(budget.id)
        self._sync_budget_threshold(state)
        return state

    def budget_state(self, budget_id: str) -> BudgetState:
        budget = self.store.get_budget(budget_id)
        if budget is None:
            raise KeyError(budget_id)
        end = None
        start = None
        if budget.window_seconds is not None:
            from .models import utc_now

            end = utc_now()
            start = end - timedelta(seconds=budget.window_seconds)
        query = UsageQuery(
            metric_type=budget.metric_type,
            unit=budget.unit,
            scope=_budget_scope(budget),
            start=start,
            end=end,
        )
        records = self.store.query(query)
        consumed = 0.0
        for record in records:
            if record.quantity is None:
                continue
            if record.quality is MeasurementQuality.ESTIMATED and not budget.include_estimated:
                continue
            consumed += record.quantity
        fraction = consumed / budget.limit
        level = _threshold_level(fraction, budget.warning_fraction)
        return BudgetState(
            budget=budget,
            consumed=consumed,
            remaining=max(0.0, budget.limit - consumed),
            fraction=fraction,
            level=level,
        )

    def _evaluate_matching_budgets(self, record: UsageRecord) -> None:
        if record.quantity is None:
            return
        for budget in self.store.list_budgets():
            if budget.metric_type != record.metric_type or budget.unit != record.unit:
                continue
            if record.quality is MeasurementQuality.ESTIMATED and not budget.include_estimated:
                continue
            scope_value = _scope_value(record.scope, budget.scope_type)
            if scope_value != budget.scope_id:
                continue
            self._sync_budget_threshold(self.budget_state(budget.id))

    def _sync_budget_threshold(self, state: BudgetState) -> None:
        budget = state.budget
        previous = self.store.get_threshold_level(budget.id)
        if state.level == previous:
            return
        self.store.set_threshold_level(budget.id, state.level)
        if state.level is not None and self.threshold_event_sink is not None:
            self.threshold_event_sink(
                BudgetThresholdEvent(
                    budget_id=budget.id,
                    level=state.level,
                    consumed=state.consumed,
                    limit=budget.limit,
                    metric_type=budget.metric_type,
                    unit=budget.unit,
                    scope_type=budget.scope_type,
                    scope_id=budget.scope_id,
                    action=budget.action,
                    budget_version=budget.version,
                )
            )


def usage_from_metric(metric: MetricRecord) -> UsageRecord | None:
    """Translate only known reliable #16 measurements; unknown metrics are ignored."""

    mapping = _metric_mapping(metric)
    if mapping is None:
        return None
    metric_type, unit, quality = mapping
    attributes = dict(metric.attributes)
    provider = metric.context.provider_id or metric.context.model_provider_id
    provenance: dict[str, JsonValue] = {
        "telemetry_metric": metric.name,
        "telemetry_unit": metric.unit,
        "attributes": attributes,
    }
    identity = json.dumps(
        {
            "name": metric.name,
            "value": metric.value,
            "unit": metric.unit,
            "timestamp": metric.timestamp.isoformat(),
            "context": metric.context.fields(),
            "attributes": attributes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return UsageRecord(
        id=f"usage_{uuid5(NAMESPACE_URL, identity)}",
        metric_type=metric_type,
        quantity=metric.value,
        unit=unit,
        quality=quality,
        source="observability",
        timestamp=metric.timestamp,
        scope=UsageScope(
            project_id=metric.context.project_id,
            workspace_id=metric.context.workspace_id,
            task_id=metric.context.task_id,
            run_id=metric.context.run_id,
            agent_id=metric.context.agent_id,
            team_id=metric.context.team_id,
            capability_id=metric.context.capability_id,
            model_config_id=metric.context.model_config_id,
            model_provider_id=metric.context.model_provider_id,
            worker_id=metric.context.worker_id,
            node_id=metric.context.node_id,
        ),
        provider=provider,
        correlation_id=metric.context.correlation_id,
        causation_id=metric.context.causation_id,
        provenance=provenance,
    )


def _metric_mapping(
    metric: MetricRecord,
) -> tuple[str, str, MeasurementQuality] | None:
    if metric.name in {"platform.tasks.terminal", "platform.runs.terminal"}:
        outcome = _normalized_metric_label(metric.attributes.get("outcome"))
        if outcome is None:
            return None
        subject = "task" if metric.name == "platform.tasks.terminal" else "run"
        return f"{subject}.outcome.{outcome}.count", "count", MeasurementQuality.MEASURED

    if metric.name == "platform.model.usage":
        usage_key = _normalized_metric_label(metric.attributes.get("usage_key"))
        if usage_key is None:
            return None
        if metric.unit == "tokens":
            canonical = {
                "input_tokens": "model.tokens.input",
                "prompt_tokens": "model.tokens.input",
                "output_tokens": "model.tokens.output",
                "completion_tokens": "model.tokens.output",
                "total_tokens": "model.tokens.total",
                "cached_input_tokens": "model.tokens.cached_input",
                "cached_prompt_tokens": "model.tokens.cached_input",
                "reasoning_tokens": "model.tokens.reasoning",
            }.get(usage_key, f"model.tokens.provider_reported.{usage_key}")
            return canonical, "tokens", MeasurementQuality.REPORTED
        return (
            f"model.provider_usage.{usage_key}",
            metric.unit,
            MeasurementQuality.REPORTED,
        )

    exact: dict[str, tuple[str, str, MeasurementQuality]] = {
        "platform.tasks.created": ("task.count", "count", MeasurementQuality.MEASURED),
        "platform.task.duration_seconds": (
            "task.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.runs.created": ("run.count", "count", MeasurementQuality.MEASURED),
        "platform.run.duration_seconds": (
            "run.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.run.queue_wait_seconds": (
            "run.queue_wait",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.run.retries": ("run.retry.count", "count", MeasurementQuality.MEASURED),
        "platform.executor.calls": (
            "executor.invocation.count",
            "count",
            MeasurementQuality.MEASURED,
        ),
        "platform.executor.duration_seconds": (
            "executor.invocation.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.executor.failures": (
            "executor.failure.count",
            "count",
            MeasurementQuality.MEASURED,
        ),
        "platform.model.calls": ("model.call.count", "count", MeasurementQuality.MEASURED),
        "platform.model.duration_seconds": (
            "model.call.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.tool.calls": (
            "capability.invocation.count",
            "count",
            MeasurementQuality.MEASURED,
        ),
        "platform.tool.duration_seconds": (
            "capability.invocation.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
    }
    return exact.get(metric.name)


def _normalized_metric_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized or len(normalized) > 64:
        return None
    if not all(character.isalnum() or character == "_" for character in normalized):
        return None
    return normalized


def _budget_scope(budget: UsageBudget) -> UsageScope:
    value = budget.scope_id
    match budget.scope_type:
        case "user":
            return UsageScope(owner_type="user", owner_id=value)
        case "organization":
            return UsageScope(organization_id=value)
        case "team":
            return UsageScope(team_id=value)
        case "project":
            return UsageScope(project_id=value)
        case "workspace":
            return UsageScope(workspace_id=value)
        case "task":
            return UsageScope(task_id=value)
        case "run":
            return UsageScope(run_id=value)
        case "agent":
            return UsageScope(agent_id=value)
        case "capability":
            return UsageScope(capability_id=value)
        case "model_provider":
            return UsageScope(model_provider_id=value)
        case "worker":
            return UsageScope(worker_id=value)
        case "node":
            return UsageScope(node_id=value)
        case _:
            raise ValueError(f"unsupported budget scope_type: {budget.scope_type}")


def _scope_value(scope: UsageScope, scope_type: str) -> str | None:
    if scope_type == "user":
        return scope.owner_id if scope.owner_type == "user" else None
    return {
        "organization": (
            scope.organization_id
            or (scope.owner_id if scope.owner_type == "organization" else None)
        ),
        "team": scope.team_id or (scope.owner_id if scope.owner_type == "team" else None),
        "project": scope.project_id,
        "workspace": scope.workspace_id,
        "task": scope.task_id,
        "run": scope.run_id,
        "agent": scope.agent_id,
        "capability": scope.capability_id,
        "model_provider": scope.model_provider_id,
        "worker": scope.worker_id,
        "node": scope.node_id,
    }.get(scope_type)


def _threshold_level(fraction: float, warning_fraction: float) -> ThresholdLevel | None:
    if fraction >= 1.0:
        return ThresholdLevel.EXCEEDED
    if fraction >= warning_fraction:
        return ThresholdLevel.WARNING
    return None
