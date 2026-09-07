from __future__ import annotations

import asyncio

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
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _http(accounting: AccountingService) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        resource_services=accounting_resource_services(accounting),
    )
    return ControlPlaneHTTP(control_plane)


def _headers() -> dict[str, str]:
    return {
        "X-Request-Id": "request-usage",
        "X-Correlation-Id": "correlation-usage",
        "X-Principal-Ref": "user:alice",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }


def test_accounting_collections_are_real_versioned_control_plane_resources() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    accounting.record(
        UsageRecord(
            metric_type="storage.file.bytes.current",
            unit="bytes",
            quality=MeasurementQuality.REPORTED,
            source="file-provider",
            quantity=42.0,
            aggregation_mode=AggregationMode.LATEST,
            scope=UsageScope(owner_type="user", owner_id="alice"),
        )
    )
    accounting.put_budget(
        UsageBudget(
            metric_type="storage.file.bytes.current",
            unit="bytes",
            scope_type="user",
            scope_id="alice",
            limit=100.0,
            owner_type="user",
            owner_id="alice",
        )
    )
    http = _http(accounting)

    async def scenario() -> None:
        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1", headers=_headers()))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        assert isinstance(resources, list)
        for collection in ("usage-records", "usage-aggregates", "usage-budgets"):
            assert collection in resources

        records = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/usage-records", headers=_headers())
        )
        assert records.status == 200
        assert isinstance(records.body, dict)
        record_items = records.body["items"]
        assert isinstance(record_items, list)
        assert len(record_items) == 1
        assert isinstance(record_items[0], dict)
        assert record_items[0]["aggregation_mode"] == "latest"

        aggregates = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/usage-aggregates", headers=_headers())
        )
        assert aggregates.status == 200
        assert isinstance(aggregates.body, dict)
        aggregate_items = aggregates.body["items"]
        assert isinstance(aggregate_items, list)
        assert len(aggregate_items) == 1
        assert isinstance(aggregate_items[0], dict)
        assert aggregate_items[0]["total"] == 42.0
        assert aggregate_items[0]["aggregation_mode"] == "latest"
        assert aggregate_items[0]["trend_bucket_seconds"] == 3600
        trend = aggregate_items[0]["trend"]
        assert isinstance(trend, list)
        assert any(isinstance(point, dict) and point["value"] == 42.0 for point in trend)

        budgets = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/usage-budgets", headers=_headers())
        )
        assert budgets.status == 200
        assert isinstance(budgets.body, dict)
        budget_items = budgets.body["items"]
        assert isinstance(budget_items, list)
        assert len(budget_items) == 1
        assert isinstance(budget_items[0], dict)
        assert budget_items[0]["consumed"] == 42.0

        openapi = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/openapi.json", headers=_headers())
        )
        assert openapi.status == 200
        assert isinstance(openapi.body, dict)
        paths = openapi.body["paths"]
        assert isinstance(paths, dict)
        assert "/api/v1/usage-records" in paths
        assert "/api/v1/usage-aggregates" in paths
        assert "/api/v1/usage-budgets" in paths

    asyncio.run(scenario())
