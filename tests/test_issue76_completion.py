from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AggregationMode,
    BudgetWindowMode,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageAggregateResourceService,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageRecordResourceService,
    UsageScope,
    accounting_resource_services,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationProvider,
    HealthStatus,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.observability import MetricRecord, TelemetryContext
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class DenyAuthorizationProvider(AuthorizationProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="deny-accounting-test",
            provider_type="authorization",
            health=HealthStatus.HEALTHY,
        )

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        del request
        return AuthorizationDecision(False, reason="accounting access denied by #15")


def _context(owner_type: str, owner_id: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{owner_id}",
        correlation_id=f"correlation-{owner_id}",
        actor=ActorContext(
            principal_ref=f"{owner_type}:{owner_id}",
            owner_type=owner_type,
            owner_id=owner_id,
        ),
    )


def _http(
    accounting: AccountingService,
    authorization: AuthorizationProvider | None = None,
) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlaneHTTP(
        ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=authorization,
            resource_services=accounting_resource_services(accounting),
        )
    )


def test_worker_and_node_reported_resources_are_latest_provider_neutral_gauges() -> None:
    service = AccountingService(InMemoryUsageStore())
    first_context = TelemetryContext(
        worker_id="worker-a", node_id="node-a", provider_id="provider-v1"
    )
    replacement_context = TelemetryContext(
        worker_id="worker-a", node_id="node-a", provider_id="provider-v2"
    )
    for context, value in ((first_context, 4.0), (replacement_context, 7.0)):
        service.ingest_metric(
            MetricRecord(
                "platform.worker.reported_resource",
                value,
                unit="reported_units",
                context=context,
                attributes={"resource_key": "gpu-load"},
            )
        )
    service.ingest_metric(
        MetricRecord(
            "platform.node.reported_resource",
            1024.0,
            unit="reported_units",
            context=TelemetryContext(node_id="node-a", provider_id="provider-v2"),
            attributes={"resource_key": "memory-bytes-native"},
        )
    )

    worker = service.aggregate(
        UsageQuery(
            metric_type="worker.provider_reported.gpu_load",
            unit="reported_units",
            scope=UsageScope(worker_id="worker-a"),
        )
    )
    assert worker.aggregation_mode is AggregationMode.LATEST
    assert worker.total == 7.0
    records = service.query(
        UsageQuery(
            metric_type="worker.provider_reported.gpu_load",
            unit="reported_units",
            scope=UsageScope(worker_id="worker-a"),
        )
    )
    assert {record.provider for record in records} == {"provider-v1", "provider-v2"}
    assert all(record.scope.worker_id == "worker-a" for record in records)
    assert all(record.quality is MeasurementQuality.REPORTED for record in records)

    node = service.aggregate(
        UsageQuery(
            metric_type="node.provider_reported.memory_bytes_native",
            unit="reported_units",
            scope=UsageScope(node_id="node-a"),
        )
    )
    assert node.aggregation_mode is AggregationMode.LATEST
    assert node.total == 1024.0


def test_worker_dispatch_usage_is_additive_and_attributed() -> None:
    service = AccountingService(InMemoryUsageStore())
    context = TelemetryContext(
        project_id="project-a",
        task_id="task-a",
        run_id="run-a",
        worker_id="worker-a",
        node_id="node-a",
        provider_id="worker-provider",
    )
    service.ingest_metric(MetricRecord("platform.worker.dispatch.calls", 1.0, context=context))
    service.ingest_metric(
        MetricRecord(
            "platform.worker.dispatch.duration_seconds",
            2.5,
            unit="seconds",
            context=context,
        )
    )
    assert (
        service.aggregate(
            UsageQuery(
                metric_type="worker.dispatch.count",
                unit="count",
                scope=UsageScope(worker_id="worker-a"),
            )
        ).total
        == 1.0
    )
    duration = service.query(
        UsageQuery(metric_type="worker.dispatch.duration", unit="seconds")
    )[0]
    assert duration.scope.worker_id == "worker-a"
    assert duration.scope.node_id == "node-a"


def test_external_cost_requires_explicit_currency_and_quality() -> None:
    service = AccountingService(InMemoryUsageStore())
    record = service.record_external_cost(
        amount=0.125,
        currency="eur",
        source="configured-price-estimator",
        quality=MeasurementQuality.ESTIMATED,
        scope=UsageScope(project_id="project-a"),
        provider="provider-a",
        confidence=0.8,
        provenance={"price_source": "operator-configured"},
    )
    assert record.metric_type == "external.cost.amount"
    assert record.unit == "EUR"
    assert record.currency == "EUR"
    assert record.cost_amount == 0.125
    assert record.quality is MeasurementQuality.ESTIMATED

    excluded = UsageBudget(
        metric_type="external.cost.amount",
        unit="EUR",
        scope_type="project",
        scope_id="project-a",
        limit=1.0,
    )
    included = UsageBudget(
        metric_type="external.cost.amount",
        unit="EUR",
        scope_type="project",
        scope_id="project-a",
        limit=1.0,
        include_estimated=True,
    )
    service.put_budget(excluded)
    service.put_budget(included)
    assert service.budget_state(excluded.id).consumed == 0.0
    assert service.budget_state(included.id).consumed == 0.125


def test_model_configuration_is_a_first_class_budget_scope() -> None:
    service = AccountingService(InMemoryUsageStore())
    budget = UsageBudget(
        metric_type="model.tokens.total",
        unit="tokens",
        scope_type="model_config",
        scope_id="model-config-a",
        limit=100.0,
    )
    service.put_budget(budget)
    service.record(
        UsageRecord(
            metric_type="model.tokens.total",
            unit="tokens",
            quality=MeasurementQuality.REPORTED,
            source="model-provider",
            quantity=40.0,
            scope=UsageScope(model_config_id="model-config-a"),
        )
    )
    assert service.budget_state(budget.id).consumed == 40.0


def test_budget_window_contract_distinguishes_lifetime_and_rolling() -> None:
    service = AccountingService(InMemoryUsageStore())
    now = datetime.now(UTC)
    rolling = UsageBudget(
        metric_type="task.count",
        unit="count",
        scope_type="project",
        scope_id="project-a",
        limit=10.0,
        window_seconds=5,
    )
    lifetime = UsageBudget(
        metric_type="task.count",
        unit="count",
        scope_type="project",
        scope_id="project-a",
        limit=10.0,
    )
    service.put_budget(rolling)
    service.put_budget(lifetime)
    for timestamp in (now - timedelta(seconds=20), now):
        service.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=1.0,
                timestamp=timestamp,
                scope=UsageScope(project_id="project-a"),
            )
        )
    rolling_state = service.budget_state(rolling.id)
    lifetime_state = service.budget_state(lifetime.id)
    assert rolling.window_mode is BudgetWindowMode.ROLLING
    assert rolling_state.consumed == 1.0
    assert rolling_state.window_start is not None
    assert rolling_state.window_end is not None
    assert lifetime.window_mode is BudgetWindowMode.LIFETIME
    assert lifetime_state.consumed == 2.0
    assert lifetime_state.window_start is None
    assert lifetime_state.window_end is None


def test_latest_control_plane_aggregates_remain_separate_per_worker_scope() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    for worker_id, value in (("worker-a", 2.0), ("worker-b", 8.0)):
        accounting.record(
            UsageRecord(
                metric_type="worker.provider_reported.load",
                unit="reported_units",
                quality=MeasurementQuality.REPORTED,
                source="worker-provider",
                quantity=value,
                aggregation_mode=AggregationMode.LATEST,
                scope=UsageScope(
                    worker_id=worker_id, owner_type="user", owner_id="alice"
                ),
            )
        )
    resources = asyncio.run(
        UsageAggregateResourceService(accounting).list_resources(
            _context("user", "alice"), PageQuery()
        )
    )
    assert len(resources) == 2
    by_worker = {
        resource["scope"]["worker_id"]: resource["total"]
        for resource in resources
        if isinstance(resource["scope"], dict)
    }
    assert by_worker == {"worker-a": 2.0, "worker-b": 8.0}


def test_organization_usage_isolation_is_exact() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    for org_id, value in (("org-a", 1.0), ("org-b", 50.0)):
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=value,
                scope=UsageScope(
                    organization_id=org_id,
                    owner_type="organization",
                    owner_id=org_id,
                ),
            )
        )
    records = asyncio.run(
        UsageRecordResourceService(accounting).list_resources(
            _context("organization", "org-a"), PageQuery()
        )
    )
    assert len(records) == 1
    assert records[0]["quantity"] == 1.0


def test_control_plane_accounting_routes_follow_authorization_gate() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    accounting.record(
        UsageRecord(
            metric_type="task.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="test",
            quantity=1.0,
            scope=UsageScope(owner_type="user", owner_id="alice"),
        )
    )
    http = _http(accounting, DenyAuthorizationProvider())
    response = asyncio.run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/usage-records",
                headers={
                    "X-Request-Id": "request-denied",
                    "X-Correlation-Id": "correlation-denied",
                    "X-Principal-Ref": "user:alice",
                    "X-Owner-Type": "user",
                    "X-Owner-Id": "alice",
                },
            )
        )
    )
    assert response.status == 403
