from __future__ import annotations

from pathlib import Path

from cli_test_helpers import ControlPlaneRecordingTransport as RecordingTransport
from cli_test_helpers import invoke_cli_json as _invoke
from cli_test_helpers import page_items as _items

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AggregationMode,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageBudget,
    UsageRecord,
    UsageScope,
    accounting_resource_services,
)
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _http(*, accounting: AccountingService | None = None) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        resource_services=None if accounting is None else accounting_resource_services(accounting),
    )
    return ControlPlaneHTTP(control_plane)


def _accounting() -> tuple[AccountingService, str, str]:
    accounting = AccountingService(InMemoryUsageStore())
    project_id = "project_usage_test"
    record = UsageRecord(
        metric_type="storage.file.bytes.current",
        unit="bytes",
        quality=MeasurementQuality.REPORTED,
        source="file-provider",
        quantity=42.0,
        aggregation_mode=AggregationMode.LATEST,
        scope=UsageScope(project_id=project_id),
    )
    accounting.record(record)
    budget = UsageBudget(
        metric_type="storage.file.bytes.current",
        unit="bytes",
        scope_type="project",
        scope_id=project_id,
        limit=100.0,
    )
    accounting.put_budget(budget)
    return accounting, record.id, budget.id


def test_usage_commands_read_same_canonical_accounting_resources_as_web_ui(tmp_path: Path) -> None:
    accounting, record_id, budget_id = _accounting()
    transport = RecordingTransport(_http(accounting=accounting))
    config = tmp_path / "cli.json"

    code, records, error = _invoke(config, transport, "usage", "record", "list")
    assert code == 0 and not error
    assert [item["id"] for item in _items(records)] == [record_id]
    assert _items(records)[0]["aggregation_mode"] == "latest"

    code, record, error = _invoke(config, transport, "usage", "record", "show", record_id)
    assert code == 0 and not error
    assert record["data"]["id"] == record_id

    code, aggregates, error = _invoke(config, transport, "usage", "aggregate", "list")
    assert code == 0 and not error
    aggregate_items = _items(aggregates)
    assert len(aggregate_items) == 1
    aggregate_id = aggregate_items[0]["id"]
    assert aggregate_items[0]["total"] == 42.0
    assert aggregate_items[0]["aggregation_mode"] == "latest"

    code, aggregate, error = _invoke(
        config,
        transport,
        "usage",
        "aggregate",
        "show",
        aggregate_id,
    )
    assert code == 0 and not error
    assert aggregate["data"]["id"] == aggregate_id

    code, budgets, error = _invoke(config, transport, "usage", "budget", "list")
    assert code == 0 and not error
    assert [item["id"] for item in _items(budgets)] == [budget_id]
    assert _items(budgets)[0]["consumed"] == 42.0

    code, budget, error = _invoke(config, transport, "usage", "budget", "show", budget_id)
    assert code == 0 and not error
    assert budget["data"]["id"] == budget_id

    assert transport.calls == [
        ("GET", "/api/v1/usage-records"),
        ("GET", f"/api/v1/usage-records/{record_id}"),
        ("GET", "/api/v1/usage-aggregates"),
        ("GET", f"/api/v1/usage-aggregates/{aggregate_id}"),
        ("GET", "/api/v1/usage-budgets"),
        ("GET", f"/api/v1/usage-budgets/{budget_id}"),
    ]


def test_usage_commands_have_no_backend_fallback_when_accounting_is_absent(tmp_path: Path) -> None:
    transport = RecordingTransport(_http())
    config = tmp_path / "cli.json"

    code, payload, error = _invoke(config, transport, "usage", "record", "list")

    assert code == 3
    assert payload == {}
    assert '"code":"not_found"' in error
    assert '"message":"route not found"' in error
    assert transport.calls == [("GET", "/api/v1/usage-records")]
