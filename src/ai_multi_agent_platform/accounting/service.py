"""Accounting ingestion, aggregation and budget evaluation services."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from math import ceil
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability.models import MetricRecord

from .models import (
    AggregationMode,
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
UsageAttributor = Callable[[UsageRecord], UsageRecord]
MAX_TREND_BUCKETS = 500

_RUNTIME_GAUGE_METRICS = frozenset(
    {
        "platform.node.cpu_cores_total",
        "platform.node.cpu_cores_available",
        "platform.node.ram_total_bytes",
        "platform.node.ram_available_bytes",
        "platform.node.storage_total_bytes",
        "platform.node.storage_available_bytes",
        "platform.node.accelerator_memory_total_bytes",
        "platform.node.accelerator_memory_available_total_bytes",
        "platform.worker.active_jobs",
        "platform.worker.concurrency_limit",
    }
)


class AccountingService:
    """Durable #76 owner and structural implementation of observability.MeasurementSink."""

    def __init__(
        self,
        store: UsageStore,
        *,
        threshold_event_sink: ThresholdEventSink | None = None,
        usage_attributor: UsageAttributor | None = None,
    ) -> None:
        self.store = store
        self.threshold_event_sink = threshold_event_sink
        self.usage_attributor = usage_attributor

    def ingest_metric(self, record: MetricRecord) -> None:
        usage = usage_from_metric(record)
        if usage is not None:
            if self.usage_attributor is not None:
                usage = self.usage_attributor(usage)
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
        aggregation_mode: AggregationMode = AggregationMode.ADDITIVE,
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
            aggregation_mode=aggregation_mode,
        )
        self.record(record)
        return record

    def record_external_cost(
        self,
        *,
        amount: float,
        currency: str,
        source: str,
        quality: MeasurementQuality,
        scope: UsageScope | None = None,
        provider: str | None = None,
        confidence: float | None = None,
        provenance: dict[str, JsonValue] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> UsageRecord:
        """Record an explicitly supplied external monetary amount.

        Cost is never inferred from provider usage units or free-form model metadata.
        The ISO currency is also the canonical unit so unlike currencies cannot be
        silently aggregated.
        """

        if quality not in {MeasurementQuality.REPORTED, MeasurementQuality.ESTIMATED}:
            raise ValueError("external cost quality must be reported or estimated")
        canonical_currency = currency.upper()
        record = UsageRecord(
            metric_type="external.cost.amount",
            unit=canonical_currency,
            quality=quality,
            source=source,
            quantity=amount,
            scope=scope or UsageScope(),
            provider=provider,
            correlation_id=correlation_id,
            causation_id=causation_id,
            cost_amount=amount,
            currency=canonical_currency,
            confidence=confidence,
            provenance=dict(provenance or {}),
        )
        self.record(record)
        return record

    def query(self, query: UsageQuery | None = None) -> tuple[UsageRecord, ...]:
        return self.store.query(query or UsageQuery())

    def aggregate(self, query: UsageQuery) -> UsageAggregate:
        records = self.store.query(query)
        if query.metric_type is None or query.unit is None:
            raise ValueError("aggregate query requires metric_type and unit")
        return aggregate_usage_records(
            records,
            metric_type=query.metric_type,
            unit=query.unit,
            start=query.start,
            end=query.end,
        )

    def trend(
        self,
        query: UsageQuery,
        *,
        bucket_seconds: int,
    ) -> tuple[UsageAggregate, ...]:
        """Return bounded time buckets without fabricating samples for empty periods."""

        if query.metric_type is None or query.unit is None:
            raise ValueError("trend query requires metric_type and unit")
        if query.start is None or query.end is None:
            raise ValueError("trend query requires explicit start and end")
        records = self.store.query(
            UsageQuery(
                metric_type=query.metric_type,
                unit=query.unit,
                scope=query.scope,
                quality=query.quality,
            )
        )
        return trend_usage_records(
            records,
            metric_type=query.metric_type,
            unit=query.unit,
            start=query.start,
            end=query.end,
            bucket_seconds=bucket_seconds,
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
        records = tuple(
            record
            for record in self.store.query(query)
            if record.quantity is not None
            and (record.quality is not MeasurementQuality.ESTIMATED or budget.include_estimated)
        )
        consumed = _budget_quantity(records)
        fraction = consumed / budget.limit
        level = _threshold_level(fraction, budget.warning_fraction)
        return BudgetState(
            budget=budget,
            consumed=consumed,
            remaining=max(0.0, budget.limit - consumed),
            fraction=fraction,
            level=level,
            window_start=start,
            window_end=end,
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


def aggregate_usage_records(
    records: tuple[UsageRecord, ...],
    *,
    metric_type: str,
    unit: str,
    start: datetime | None = None,
    end: datetime | None = None,
    default_aggregation_mode: AggregationMode = AggregationMode.ADDITIVE,
) -> UsageAggregate:
    """Aggregate one canonical metric without collapsing distinct point-in-time gauges."""

    modes = {record.aggregation_mode for record in records}
    if len(modes) > 1:
        raise ValueError("one metric/unit query cannot mix aggregation modes")
    mode = next(iter(modes), default_aggregation_mode)
    quality_counts = {quality: 0 for quality in MeasurementQuality}
    for record in records:
        quality_counts[record.quality] += 1

    if mode is AggregationMode.LATEST and records:
        latest_records = _latest_records_by_scope(records)
        if any(record.quantity is None for record in latest_records):
            total = None
        else:
            total = sum(record.quantity for record in latest_records if record.quantity is not None)
    else:
        values = [record.quantity for record in records if record.quantity is not None]
        total = sum(values) if values else None

    return UsageAggregate(
        metric_type=metric_type,
        unit=unit,
        total=total,
        record_count=len(records),
        unavailable_count=quality_counts[MeasurementQuality.UNAVAILABLE],
        quality_counts=quality_counts,
        aggregation_mode=mode,
        start=start,
        end=end,
    )


def trend_usage_records(
    records: tuple[UsageRecord, ...],
    *,
    metric_type: str,
    unit: str,
    start: datetime,
    end: datetime,
    bucket_seconds: int,
) -> tuple[UsageAggregate, ...]:
    """Bucket one metric/unit while preserving additive versus latest semantics."""

    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be greater than zero")
    if end <= start:
        raise ValueError("trend end must be later than start")
    bucket_count = ceil((end - start).total_seconds() / bucket_seconds)
    if bucket_count > MAX_TREND_BUCKETS:
        raise ValueError(f"trend query exceeds {MAX_TREND_BUCKETS} buckets")

    metric_records = tuple(
        sorted(
            (
                record
                for record in records
                if record.metric_type == metric_type and record.unit == unit
            ),
            key=lambda record: (record.timestamp, record.id),
        )
    )
    modes = {record.aggregation_mode for record in metric_records}
    if len(modes) > 1:
        raise ValueError("one metric/unit trend cannot mix aggregation modes")
    mode = next(iter(modes), AggregationMode.ADDITIVE)
    window_records = tuple(record for record in metric_records if start <= record.timestamp <= end)

    buckets: list[UsageAggregate] = []
    record_index = 0
    width = timedelta(seconds=bucket_seconds)
    for bucket_index in range(bucket_count):
        bucket_start = start + timedelta(seconds=bucket_index * bucket_seconds)
        bucket_end = min(end, bucket_start + width)
        is_last = bucket_index == bucket_count - 1
        selected: list[UsageRecord] = []
        while record_index < len(window_records):
            record = window_records[record_index]
            if record.timestamp < bucket_start:
                record_index += 1
                continue
            if record.timestamp < bucket_end or (is_last and record.timestamp <= end):
                selected.append(record)
                record_index += 1
                continue
            break
        buckets.append(
            aggregate_usage_records(
                tuple(selected),
                metric_type=metric_type,
                unit=unit,
                start=bucket_start,
                end=bucket_end,
                default_aggregation_mode=mode,
            )
        )
    return tuple(buckets)


def _latest_records_by_scope(records: tuple[UsageRecord, ...]) -> tuple[UsageRecord, ...]:
    """Keep one latest gauge per exact canonical UsageScope.

    A broad query may contain many Workers, Nodes or other scoped resources. Choosing one
    globally latest row would drop every other resource. Exact scope grouping preserves
    resource identity while still preventing historical snapshots from being summed.
    """

    latest: dict[tuple[tuple[str, str], ...], UsageRecord] = {}
    for record in records:
        key = tuple(sorted(record.scope.fields().items()))
        current = latest.get(key)
        if current is None or (record.timestamp, record.id) > (current.timestamp, current.id):
            latest[key] = record
    return tuple(latest[key] for key in sorted(latest))


def _budget_quantity(records: tuple[UsageRecord, ...]) -> float:
    if not records:
        return 0.0
    modes = {record.aggregation_mode for record in records}
    if len(modes) > 1:
        raise ValueError("budget cannot mix aggregation modes for one metric/unit")
    mode = next(iter(modes))
    if mode is AggregationMode.LATEST:
        latest_records = _latest_records_by_scope(records)
        total = 0.0
        for record in latest_records:
            assert record.quantity is not None
            total += record.quantity
        return total
    total = 0.0
    for record in records:
        assert record.quantity is not None
        total += record.quantity
    return total


def usage_from_metric(metric: MetricRecord) -> UsageRecord | None:
    """Translate only known reliable #16 measurements; unknown metrics are ignored."""

    mapping = _metric_mapping(metric)
    if mapping is None:
        return None
    metric_type, unit, quality = mapping
    aggregation_mode = _aggregation_mode(metric)
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
        quantity=None if quality is MeasurementQuality.UNAVAILABLE else metric.value,
        unit=unit,
        quality=quality,
        aggregation_mode=aggregation_mode,
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

    if metric.name in {
        "platform.node.reported_resource",
        "platform.worker.reported_resource",
    }:
        resource_key = _normalized_metric_label(metric.attributes.get("resource_key"))
        if resource_key is None:
            return None
        subject = "node" if metric.name.startswith("platform.node.") else "worker"
        return (
            f"{subject}.provider_reported.{resource_key}",
            metric.unit,
            MeasurementQuality.REPORTED,
        )

    if metric.name == "platform.node.resource_unavailable":
        resource_metric = metric.attributes.get("resource_metric")
        resource_unit = metric.attributes.get("resource_unit")
        if not isinstance(resource_metric, str) or not isinstance(resource_unit, str):
            return None
        unavailable: dict[str, tuple[str, str, MeasurementQuality]] = {
            "platform.node.cpu_cores_total": (
                "node.cpu.cores.capacity",
                "cores",
                MeasurementQuality.UNAVAILABLE,
            ),
            "platform.node.cpu_cores_available": (
                "node.cpu.cores.available",
                "cores",
                MeasurementQuality.UNAVAILABLE,
            ),
            "platform.node.ram_total_bytes": (
                "node.memory.bytes.capacity",
                "bytes",
                MeasurementQuality.UNAVAILABLE,
            ),
            "platform.node.ram_available_bytes": (
                "node.memory.bytes.available",
                "bytes",
                MeasurementQuality.UNAVAILABLE,
            ),
            "platform.node.storage_total_bytes": (
                "node.storage.bytes.capacity",
                "bytes",
                MeasurementQuality.UNAVAILABLE,
            ),
            "platform.node.storage_available_bytes": (
                "node.storage.bytes.available",
                "bytes",
                MeasurementQuality.UNAVAILABLE,
            ),
            "platform.node.accelerator_memory_total_bytes": (
                "node.accelerator.memory.bytes.capacity",
                "bytes",
                MeasurementQuality.UNAVAILABLE,
            ),
            "platform.node.accelerator_memory_available_total_bytes": (
                "node.accelerator.memory.bytes.available",
                "bytes",
                MeasurementQuality.UNAVAILABLE,
            ),
        }
        mapped = unavailable.get(resource_metric)
        if mapped is None or mapped[1] != resource_unit:
            return None
        return mapped

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
        "platform.worker.dispatch.calls": (
            "worker.dispatch.count",
            "count",
            MeasurementQuality.MEASURED,
        ),
        "platform.worker.dispatch.duration_seconds": (
            "worker.dispatch.duration",
            "seconds",
            MeasurementQuality.MEASURED,
        ),
        "platform.worker.dispatch.failures": (
            "worker.dispatch.failure.count",
            "count",
            MeasurementQuality.MEASURED,
        ),
        "platform.node.cpu_cores_total": (
            "node.cpu.cores.capacity",
            "cores",
            MeasurementQuality.REPORTED,
        ),
        "platform.node.cpu_cores_available": (
            "node.cpu.cores.available",
            "cores",
            MeasurementQuality.REPORTED,
        ),
        "platform.node.ram_total_bytes": (
            "node.memory.bytes.capacity",
            "bytes",
            MeasurementQuality.REPORTED,
        ),
        "platform.node.ram_available_bytes": (
            "node.memory.bytes.available",
            "bytes",
            MeasurementQuality.REPORTED,
        ),
        "platform.node.storage_total_bytes": (
            "node.storage.bytes.capacity",
            "bytes",
            MeasurementQuality.REPORTED,
        ),
        "platform.node.storage_available_bytes": (
            "node.storage.bytes.available",
            "bytes",
            MeasurementQuality.REPORTED,
        ),
        "platform.node.accelerator_memory_total_bytes": (
            "node.accelerator.memory.bytes.capacity",
            "bytes",
            MeasurementQuality.REPORTED,
        ),
        "platform.node.accelerator_memory_available_total_bytes": (
            "node.accelerator.memory.bytes.available",
            "bytes",
            MeasurementQuality.REPORTED,
        ),
        "platform.worker.active_jobs": (
            "worker.jobs.active",
            "count",
            MeasurementQuality.REPORTED,
        ),
        "platform.worker.concurrency_limit": (
            "worker.jobs.capacity",
            "count",
            MeasurementQuality.REPORTED,
        ),
    }
    return exact.get(metric.name)


def _aggregation_mode(metric: MetricRecord) -> AggregationMode:
    if (
        metric.name
        in {
            "platform.node.reported_resource",
            "platform.worker.reported_resource",
            "platform.node.resource_unavailable",
        }
        | _RUNTIME_GAUGE_METRICS
    ):
        return AggregationMode.LATEST
    return AggregationMode.ADDITIVE


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
        case "model_config":
            return UsageScope(model_config_id=value)
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
        "model_config": scope.model_config_id,
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
