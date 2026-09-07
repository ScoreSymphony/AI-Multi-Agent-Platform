from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AggregationMode,
    FileStorageAccounting,
    InMemoryUsageStore,
    MeasurementQuality,
    SQLiteUsageStore,
    ThresholdLevel,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import new_id


def _context(project_id: str) -> DataAccessContext:
    operation = OperationContext(
        correlation_id="storage-accounting",
        owner_type="user",
        owner_id="alice",
        project_id=project_id,
    )
    return DataAccessContext(operation=operation, actor_ref="user:alice")


def test_current_file_storage_uses_latest_snapshot_not_sum(tmp_path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    accounting = AccountingService(InMemoryUsageStore())
    storage = FileStorageAccounting(accounting, provider)
    first_file = asyncio.run(provider.create_file(b"a" * 10, context))
    asyncio.run(provider.create_file(b"b" * 20, context))
    first_time = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    first = asyncio.run(storage.reconcile(context, observed_at=first_time))
    assert first.quantity == 30.0
    assert first.aggregation_mode is AggregationMode.LATEST
    assert first.quality is MeasurementQuality.REPORTED

    asyncio.run(provider.delete_file(first_file.file_id, context))
    second = asyncio.run(storage.reconcile(context, observed_at=first_time + timedelta(minutes=1)))
    assert second.quantity == 20.0
    aggregate = accounting.aggregate(
        UsageQuery(
            metric_type="storage.file.bytes.current",
            unit="bytes",
            scope=UsageScope(project_id=project_id),
        )
    )
    assert aggregate.total == 20.0
    assert aggregate.record_count == 2
    assert aggregate.aggregation_mode is AggregationMode.LATEST


def test_latest_unavailable_measurement_is_not_fabricated_as_zero() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    start = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    accounting.record(
        UsageRecord(
            metric_type="node.memory.bytes.current",
            unit="bytes",
            quality=MeasurementQuality.REPORTED,
            source="node",
            quantity=128.0,
            aggregation_mode=AggregationMode.LATEST,
            timestamp=start,
        )
    )
    accounting.record(
        UsageRecord(
            metric_type="node.memory.bytes.current",
            unit="bytes",
            quality=MeasurementQuality.UNAVAILABLE,
            source="node",
            quantity=None,
            aggregation_mode=AggregationMode.LATEST,
            timestamp=start + timedelta(seconds=1),
        )
    )
    aggregate = accounting.aggregate(
        UsageQuery(metric_type="node.memory.bytes.current", unit="bytes")
    )
    assert aggregate.total is None
    assert aggregate.unavailable_count == 1


def test_storage_budget_uses_current_value_and_can_recover_below_threshold(tmp_path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    events = []
    accounting = AccountingService(InMemoryUsageStore(), threshold_event_sink=events.append)
    budget = UsageBudget(
        metric_type="storage.file.bytes.current",
        unit="bytes",
        scope_type="project",
        scope_id=project_id,
        limit=30.0,
    )
    accounting.put_budget(budget)
    storage = FileStorageAccounting(accounting, provider)
    first = asyncio.run(provider.create_file(b"a" * 10, context))
    asyncio.run(provider.create_file(b"b" * 20, context))
    observed = datetime(2026, 9, 3, 2, 0, tzinfo=UTC)
    asyncio.run(storage.reconcile(context, observed_at=observed))
    assert [event.level for event in events] == [ThresholdLevel.EXCEEDED]

    asyncio.run(provider.delete_file(first.file_id, context))
    asyncio.run(storage.reconcile(context, observed_at=observed + timedelta(minutes=1)))
    state = accounting.budget_state(budget.id)
    assert state.consumed == 20.0
    assert state.level is None


def test_storage_reconciliation_does_not_infer_usage_owner_from_measuring_actor(tmp_path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    accounting = AccountingService(InMemoryUsageStore())
    asyncio.run(provider.create_file(b"abc", context))
    usage = asyncio.run(FileStorageAccounting(accounting, provider).reconcile(context))
    assert usage.scope.project_id == project_id
    assert usage.scope.owner_type is None
    assert usage.scope.owner_id is None
    assert usage.provenance["ready_file_count"] == 1


def test_storage_scope_can_be_explicitly_owner_attributed(tmp_path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    accounting = AccountingService(InMemoryUsageStore())
    usage = asyncio.run(
        FileStorageAccounting(accounting, provider).reconcile(
            context,
            scope=UsageScope(project_id=project_id, owner_type="user", owner_id="alice"),
        )
    )
    assert usage.quantity == 0.0
    assert usage.scope.owner_id == "alice"


def test_storage_scope_cannot_claim_another_project(tmp_path) -> None:
    project_id = new_id("project")
    provider = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    storage = FileStorageAccounting(AccountingService(InMemoryUsageStore()), provider)
    with pytest.raises(ValueError):
        asyncio.run(
            storage.reconcile(
                _context(project_id),
                scope=UsageScope(project_id=new_id("project")),
            )
        )


def test_sqlite_round_trip_preserves_latest_aggregation_mode(tmp_path) -> None:
    path = tmp_path / "usage.sqlite3"
    service = AccountingService(SQLiteUsageStore(path))
    record = UsageRecord(
        metric_type="storage.file.bytes.current",
        unit="bytes",
        quality=MeasurementQuality.REPORTED,
        source="file-provider",
        quantity=12.0,
        aggregation_mode=AggregationMode.LATEST,
    )
    service.record(record)
    restarted = AccountingService(SQLiteUsageStore(path))
    stored = restarted.query(UsageQuery(metric_type="storage.file.bytes.current", unit="bytes"))[0]
    assert stored.aggregation_mode is AggregationMode.LATEST
    assert (
        restarted.aggregate(
            UsageQuery(metric_type="storage.file.bytes.current", unit="bytes")
        ).total
        == 12.0
    )


def test_one_metric_unit_cannot_mix_additive_and_latest_semantics() -> None:
    service = AccountingService(InMemoryUsageStore())
    for mode in (AggregationMode.ADDITIVE, AggregationMode.LATEST):
        service.record(
            UsageRecord(
                metric_type="test.mixed",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=1.0,
                aggregation_mode=mode,
            )
        )
    with pytest.raises(ValueError, match="mix aggregation modes"):
        service.aggregate(UsageQuery(metric_type="test.mixed", unit="count"))
