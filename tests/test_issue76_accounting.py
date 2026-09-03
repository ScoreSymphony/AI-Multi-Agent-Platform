from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    BudgetAction,
    InMemoryUsageStore,
    MeasurementQuality,
    SQLiteUsageStore,
    ThresholdLevel,
    UsageAggregateResourceService,
    UsageBudget,
    UsageBudgetResourceService,
    UsageQuery,
    UsageRecord,
    UsageRecordResourceService,
    UsageScope,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.observability import (
    AccountingBridgeExporter,
    InMemoryExporter,
    MetricRecord,
    Telemetry,
    TelemetryContext,
)


def test_task_run_executor_accounting_is_idempotent_and_aggregated() -> None:
    service = AccountingService(InMemoryUsageStore())
    context = TelemetryContext(project_id="project-a", task_id="task-a", run_id="run-a")
    timestamp = datetime(2026, 9, 3, tzinfo=UTC)
    metrics = (
        MetricRecord("platform.tasks.created", 1.0, context=context, timestamp=timestamp),
        MetricRecord("platform.runs.created", 1.0, context=context, timestamp=timestamp),
        MetricRecord("platform.run.duration_seconds", 2.5, context=context, unit="seconds"),
        MetricRecord("platform.executor.calls", 1.0, context=context),
        MetricRecord("platform.executor.duration_seconds", 1.25, context=context, unit="seconds"),
        MetricRecord("platform.run.retries", 2.0, context=context),
    )
    for metric in metrics:
        service.ingest_metric(metric)
    service.ingest_metric(metrics[0])

    task_total = service.aggregate(UsageQuery(metric_type="task.count", unit="count"))
    assert task_total.total == 1.0
    assert task_total.record_count == 1
    assert service.aggregate(UsageQuery(metric_type="run.retry.count", unit="count")).total == 2.0
    assert (
        service.aggregate(
            UsageQuery(metric_type="executor.invocation.duration", unit="seconds")
        ).total
        == 1.25
    )


def test_observability_bridge_ingests_without_transferring_accounting_ownership() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    exporter = AccountingBridgeExporter(InMemoryExporter(), accounting)
    telemetry = Telemetry(exporter)

    telemetry.metric(
        "platform.executor.calls",
        1.0,
        context=TelemetryContext(task_id="task-a", run_id="run-a"),
    )

    records = accounting.query(
        UsageQuery(metric_type="executor.invocation.count", unit="count")
    )
    assert len(records) == 1
    assert records[0].source == "observability"


def test_missing_measurement_is_unavailable_not_zero() -> None:
    service = AccountingService(InMemoryUsageStore())
    record = service.record_unavailable(
        metric_type="node.gpu.utilization",
        unit="percent",
        source="worker-report",
        scope=UsageScope(node_id="node-a"),
    )

    assert record.quality is MeasurementQuality.UNAVAILABLE
    assert record.quantity is None
    aggregate = service.aggregate(
        UsageQuery(metric_type="node.gpu.utilization", unit="percent")
    )
    assert aggregate.total is None
    assert aggregate.unavailable_count == 1
    assert aggregate.record_count == 1


def test_model_provider_usage_is_reported_with_provenance() -> None:
    service = AccountingService(InMemoryUsageStore())
    service.ingest_metric(
        MetricRecord(
            "platform.model.usage",
            321.0,
            unit="tokens",
            context=TelemetryContext(
                task_id="task-a",
                model_provider_id="provider-a",
                model_config_id="model-config-a",
            ),
            attributes={"usage_key": "input_tokens", "model_ref": "model-a"},
        )
    )

    records = service.query(UsageQuery(metric_type="model.tokens.input", unit="tokens"))
    assert len(records) == 1
    record = records[0]
    assert record.quality is MeasurementQuality.REPORTED
    assert record.provider == "provider-a"
    assert record.scope.model_provider_id == "provider-a"
    assert record.scope.model_config_id == "model-config-a"
    assert record.provenance["telemetry_metric"] == "platform.model.usage"


def test_model_usage_semantics_do_not_double_count_input_output_and_total() -> None:
    service = AccountingService(InMemoryUsageStore())
    context = TelemetryContext(model_provider_id="provider-a")
    usage = (("input_tokens", 100.0), ("output_tokens", 50.0), ("total_tokens", 150.0))
    for usage_key, value in usage:
        service.ingest_metric(
            MetricRecord(
                "platform.model.usage",
                value,
                unit="tokens",
                context=context,
                attributes={"usage_key": usage_key},
            )
        )

    assert (
        service.aggregate(UsageQuery(metric_type="model.tokens.input", unit="tokens")).total
        == 100.0
    )
    assert (
        service.aggregate(UsageQuery(metric_type="model.tokens.output", unit="tokens")).total
        == 50.0
    )
    assert (
        service.aggregate(UsageQuery(metric_type="model.tokens.total", unit="tokens")).total
        == 150.0
    )


def test_unknown_telemetry_metric_is_not_fabricated_into_usage() -> None:
    service = AccountingService(InMemoryUsageStore())
    service.ingest_metric(MetricRecord("platform.unknown.future_metric", 99.0))
    assert service.query() == ()


def test_budget_threshold_events_are_stateful_and_do_not_storm() -> None:
    events = []
    service = AccountingService(InMemoryUsageStore(), threshold_event_sink=events.append)
    budget = UsageBudget(
        metric_type="storage.bytes",
        unit="bytes",
        scope_type="project",
        scope_id="project-a",
        limit=100.0,
        warning_fraction=0.8,
        action=BudgetAction.NOTIFY,
    )
    service.put_budget(budget)

    def add(quantity: float) -> None:
        service.record(
            UsageRecord(
                metric_type="storage.bytes",
                unit="bytes",
                quality=MeasurementQuality.MEASURED,
                source="storage-provider",
                quantity=quantity,
                scope=UsageScope(project_id="project-a"),
            )
        )

    add(80.0)
    add(5.0)
    add(15.0)
    assert [event.level for event in events] == [ThresholdLevel.WARNING, ThresholdLevel.EXCEEDED]
    state = service.budget_state(budget.id)
    assert state.consumed == 100.0
    assert state.remaining == 0.0


def test_external_cost_preserves_currency_quality_and_provider_provenance() -> None:
    service = AccountingService(InMemoryUsageStore())
    record = UsageRecord(
        metric_type="external.cost",
        unit="currency",
        quality=MeasurementQuality.ESTIMATED,
        source="provider-price-estimator",
        quantity=1.0,
        provider="provider-a",
        cost_amount=0.0123,
        currency="eur",
        confidence=0.7,
        provenance={"price_source": "configured-provider-price"},
    )
    service.record(record)

    stored = service.query(UsageQuery(metric_type="external.cost", unit="currency"))[0]
    assert stored.currency == "EUR"
    assert stored.quality is MeasurementQuality.ESTIMATED
    assert stored.provider == "provider-a"
    assert stored.provenance["price_source"] == "configured-provider-price"


def test_threshold_level_survives_restart_without_duplicate_event(tmp_path) -> None:
    path = tmp_path / "threshold.sqlite3"
    initial_events = []
    first = AccountingService(
        SQLiteUsageStore(path),
        threshold_event_sink=initial_events.append,
    )
    budget = UsageBudget(
        metric_type="task.count",
        unit="count",
        scope_type="project",
        scope_id="project-a",
        limit=1.0,
    )
    first.put_budget(budget)
    first.record(
        UsageRecord(
            metric_type="task.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="test",
            quantity=1.0,
            scope=UsageScope(project_id="project-a"),
        )
    )
    assert [event.level for event in initial_events] == [ThresholdLevel.EXCEEDED]

    restarted_events = []
    restarted = AccountingService(
        SQLiteUsageStore(path),
        threshold_event_sink=restarted_events.append,
    )
    restarted.record(
        UsageRecord(
            metric_type="task.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="test",
            quantity=1.0,
            scope=UsageScope(project_id="project-a"),
        )
    )
    assert restarted_events == []


def test_estimated_usage_is_excluded_from_budget_unless_enabled() -> None:
    service = AccountingService(InMemoryUsageStore())
    budget = UsageBudget(
        metric_type="external.cost",
        unit="provider_units",
        scope_type="project",
        scope_id="project-a",
        limit=10.0,
        include_estimated=False,
    )
    service.put_budget(budget)
    service.record(
        UsageRecord(
            metric_type="external.cost",
            unit="provider_units",
            quality=MeasurementQuality.ESTIMATED,
            source="cost-estimator",
            quantity=9.0,
            scope=UsageScope(project_id="project-a"),
        )
    )
    assert service.budget_state(budget.id).consumed == 0.0


def test_time_window_aggregation() -> None:
    service = AccountingService(InMemoryUsageStore())
    now = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    for timestamp, quantity in ((now - timedelta(hours=2), 2.0), (now, 3.0)):
        service.record(
            UsageRecord(
                metric_type="run.duration",
                unit="seconds",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=quantity,
                timestamp=timestamp,
            )
        )
    aggregate = service.aggregate(
        UsageQuery(
            metric_type="run.duration",
            unit="seconds",
            start=now - timedelta(minutes=30),
            end=now + timedelta(minutes=1),
        )
    )
    assert aggregate.total == 3.0
    assert aggregate.record_count == 1


def test_control_plane_usage_records_are_owner_isolated() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    first = UsageRecord(
        metric_type="task.count",
        unit="count",
        quality=MeasurementQuality.MEASURED,
        source="test",
        quantity=1.0,
        scope=UsageScope(owner_type="user", owner_id="alice"),
    )
    second = UsageRecord(
        metric_type="task.count",
        unit="count",
        quality=MeasurementQuality.MEASURED,
        source="test",
        quantity=1.0,
        scope=UsageScope(owner_type="user", owner_id="bob"),
    )
    accounting.record(first)
    accounting.record(second)
    resource_service = UsageRecordResourceService(accounting)
    context = RequestContext(
        request_id="request-a",
        correlation_id="correlation-a",
        actor=ActorContext(principal_ref="user:alice", owner_type="user", owner_id="alice"),
    )

    resources = asyncio.run(resource_service.list_resources(context, PageQuery()))
    assert [resource["id"] for resource in resources] == [first.id]
    with pytest.raises(ContractError):
        asyncio.run(resource_service.get_resource(context, second.id))


def test_control_plane_aggregates_and_budgets_do_not_cross_owner_boundaries() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    for owner in ("alice", "bob"):
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=1.0 if owner == "alice" else 100.0,
                scope=UsageScope(owner_type="user", owner_id=owner),
            )
        )
        accounting.put_budget(
            UsageBudget(
                metric_type="task.count",
                unit="count",
                scope_type="user",
                scope_id=owner,
                limit=1000.0,
                owner_type="user",
                owner_id=owner,
            )
        )

    context = RequestContext(
        request_id="request-a",
        correlation_id="correlation-a",
        actor=ActorContext(principal_ref="user:alice", owner_type="user", owner_id="alice"),
    )
    aggregates = asyncio.run(
        UsageAggregateResourceService(accounting).list_resources(context, PageQuery())
    )
    assert len(aggregates) == 1
    assert aggregates[0]["total"] == 1.0
    budgets = asyncio.run(
        UsageBudgetResourceService(accounting).list_resources(context, PageQuery())
    )
    assert len(budgets) == 1
    assert budgets[0]["owner_id"] == "alice"


def test_budget_version_history_is_durable_and_monotonic(tmp_path) -> None:
    from dataclasses import replace

    store = SQLiteUsageStore(tmp_path / "budget-history.sqlite3")
    service = AccountingService(store)
    first = UsageBudget(
        metric_type="task.count",
        unit="count",
        scope_type="user",
        scope_id="alice",
        limit=10.0,
        owner_type="user",
        owner_id="alice",
        provenance={"actor": "user:alice"},
    )
    service.put_budget(first)
    second = replace(first, limit=20.0, version=2)
    service.put_budget(second)

    assert [item.version for item in store.list_budget_versions(first.id)] == [1, 2]
    assert store.get_budget(first.id) == second
    with pytest.raises(ValueError):
        service.put_budget(replace(second, limit=30.0, version=4))


def test_sqlite_store_survives_service_restart(tmp_path) -> None:
    path = tmp_path / "usage.sqlite3"
    first = AccountingService(SQLiteUsageStore(path))
    first.record(
        UsageRecord(
            metric_type="task.duration",
            unit="seconds",
            quality=MeasurementQuality.MEASURED,
            source="test",
            quantity=4.5,
            scope=UsageScope(project_id="project-a"),
        )
    )
    budget = UsageBudget(
        metric_type="task.duration",
        unit="seconds",
        scope_type="project",
        scope_id="project-a",
        limit=10.0,
    )
    first.put_budget(budget)

    restarted = AccountingService(SQLiteUsageStore(path))
    assert restarted.aggregate(UsageQuery(metric_type="task.duration", unit="seconds")).total == 4.5
    assert restarted.budget_state(budget.id).consumed == 4.5


def test_store_replacement_preserves_canonical_accounting_semantics(tmp_path) -> None:
    records = (
        UsageRecord(
            metric_type="executor.invocation.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="test",
            quantity=1.0,
        ),
        UsageRecord(
            metric_type="executor.invocation.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="test",
            quantity=2.0,
        ),
    )
    services = (
        AccountingService(InMemoryUsageStore()),
        AccountingService(SQLiteUsageStore(tmp_path / "replacement.sqlite3")),
    )
    totals = []
    for service in services:
        for record in records:
            service.record(record)
        totals.append(
            service.aggregate(
                UsageQuery(metric_type="executor.invocation.count", unit="count")
            ).total
        )
    assert totals == [3.0, 3.0]
