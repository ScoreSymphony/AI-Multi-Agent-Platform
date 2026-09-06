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
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.contracts import AdapterMetadata, ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.control_plane.extensions import InMemoryResourceService
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    DistributedTelemetry,
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.resource_reporting import (
    RESOURCE_REPORTING_FIELDS,
    resource_reporting_metadata,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import NotificationQuery, NotificationState, RecipientRef, RecipientType
from ai_multi_agent_platform.observability import AccountingBridgeExporter, InMemoryExporter, Telemetry
from ai_multi_agent_platform.organizations.accounting import DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF
from ai_multi_agent_platform.organizations.models import Membership, Organization
from ai_multi_agent_platform.organizations.repository import InMemoryOrganizationRepository
from ai_multi_agent_platform.organizations.service import OrganizationService
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

BASE = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)


def _kernel() -> tuple[PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    return (
        PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        repository,
    )


def _context(actor_id: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{actor_id}",
        correlation_id=f"correlation-{actor_id}",
        actor=ActorContext(
            principal_ref=actor_id,
            owner_type="user",
            owner_id=actor_id,
            actor_type="human",
        ),
    )


def test_single_node_composes_same_accounting_service_into_control_plane(tmp_path) -> None:
    accounting = AccountingService(InMemoryUsageStore())
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
        accounting_service=accounting,
    )

    assert deployment.accounting_service is accounting
    assert deployment.control_plane.accounting_service is accounting
    assert {"usage-records", "usage-aggregates", "usage-budgets"}.issubset(
        deployment.control_plane.registered_collections
    )
    # #75 installs its threshold observer on this exact #76 authority.
    assert accounting.threshold_event_sink is not None


def test_canonical_accounting_collections_cannot_be_replaced_after_composition() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    kernel, events = _kernel()
    control_plane = ControlPlane(kernel=kernel, events=events, accounting_service=accounting)

    for collection in ("usage-records", "usage-aggregates", "usage-budgets"):
        with pytest.raises(ValueError, match="canonical accounting route"):
            control_plane.register_resource_service(collection, InMemoryResourceService())


def test_real_distributed_missing_resources_do_not_become_zero_usage() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    telemetry = Telemetry(AccountingBridgeExporter(InMemoryExporter(), accounting))
    runtime = DistributedRuntime(
        DistributedRegistry(heartbeat_timeout=timedelta(seconds=30)),
        telemetry=DistributedTelemetry(telemetry),
    )
    node_id = new_id("node")
    worker_id = new_id("worker")
    node = NodeRecord(
        node_id=node_id,
        display_name="unknown-resource-node",
        resources=ResourceSnapshot(),
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )
    worker = WorkerRecord(
        worker_id=worker_id,
        node_id=node_id,
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )
    runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=BASE)
    runtime.heartbeat(
        Heartbeat(
            node_id=node_id,
            observed_at=BASE + timedelta(seconds=1),
            sequence=1,
            resources=ResourceSnapshot(),
            workers=(worker,),
        )
    )

    assert not any(record.metric_type.startswith("node.") for record in accounting.query())


def test_real_distributed_explicit_unavailable_and_reliable_zero_survive_to_accounting() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    telemetry = Telemetry(AccountingBridgeExporter(InMemoryExporter(), accounting))
    distributed = DistributedTelemetry(telemetry)
    unavailable = tuple(sorted(RESOURCE_REPORTING_FIELDS - {"cpu_cores_total"}))
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="explicit-resource-state",
        resources=ResourceSnapshot(),
        adapter_metadata=(
            resource_reporting_metadata(
                reported_fields=("cpu_cores_total",),
                unavailable_fields=unavailable,
            ),
        ),
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )

    distributed.heartbeat(node, (), observed_at=BASE)
    records = {record.metric_type: record for record in accounting.query()}

    assert records["node.cpu.cores.capacity"].quantity == 0.0
    assert records["node.cpu.cores.capacity"].quality is MeasurementQuality.REPORTED
    expected_unavailable = {
        "node.cpu.cores.available",
        "node.memory.bytes.capacity",
        "node.memory.bytes.available",
        "node.storage.bytes.capacity",
        "node.storage.bytes.available",
        "node.accelerator.memory.bytes.capacity",
        "node.accelerator.memory.bytes.available",
    }
    assert expected_unavailable.issubset(records)
    for metric_type in expected_unavailable:
        record = records[metric_type]
        assert record.quantity is None
        assert record.quality is MeasurementQuality.UNAVAILABLE
        assert record.scope.node_id == node.node_id


def test_archived_threshold_attention_is_not_resurrected_after_restart(tmp_path) -> None:
    async def scenario() -> None:
        accounting_path = tmp_path / "accounting.sqlite3"
        notification_path = tmp_path / "notifications.sqlite3"
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        project_id = new_id("project")
        accounting = AccountingService(SQLiteUsageStore(accounting_path))
        budget = UsageBudget(
            metric_type="storage.bytes",
            unit="bytes",
            scope_type="project",
            scope_id=project_id,
            limit=100.0,
            warning_fraction=0.8,
            action=BudgetAction.NOTIFY,
            owner_type=recipient.type.value,
            owner_id=recipient.id,
        )
        accounting.put_budget(budget)
        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            accounting_service=accounting,
            notification_state_path=notification_path,
        )
        accounting.record(
            UsageRecord(
                metric_type="storage.bytes",
                unit="bytes",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=80.0,
                scope=UsageScope(project_id=project_id),
            )
        )
        assert accounting.store.get_threshold_level(budget.id) is ThresholdLevel.WARNING
        first_tick = await control_plane.run_notification_runtime_once()
        active = await control_plane.notification_service.list(NotificationQuery(recipient=recipient))
        assert first_tick.reminder_notifications == 1
        assert len(active) == 1
        aggregation_key = active[0].aggregation_key
        await control_plane.notification_service.archive(active[0].id, recipient=recipient)

        restarted_accounting = AccountingService(SQLiteUsageStore(accounting_path))
        restarted_kernel, restarted_events = _kernel()
        restarted = ControlPlane(
            kernel=restarted_kernel,
            events=restarted_events,
            accounting_service=restarted_accounting,
            notification_state_path=notification_path,
        )
        restart_tick = await restarted.run_notification_runtime_once()
        history = await restarted.notification_service.list(
            NotificationQuery(recipient=recipient, include_archived=True)
        )

        assert restart_tick.reminder_notifications == 0
        assert len(history) == 1
        assert history[0].state is NotificationState.ARCHIVED
        assert history[0].aggregation_key == aggregation_key
        assert history[0].summary["threshold_generation"] == 1

    asyncio.run(scenario())


def test_real_authorization_gate_remains_stricter_than_organization_accounting_membership() -> None:
    async def scenario() -> None:
        organization_repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(organization_repository)
        organization = await organization_repository.save_organization(
            Organization(name="Accounting org", owner_actor_id="owner")
        )
        for actor_id in ("alice", "mallory"):
            await organization_repository.save_membership(
                Membership(
                    actor_id=actor_id,
                    actor_type=ActorType.HUMAN,
                    organization_id=organization.id,
                    policy_refs=(DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,),
                )
            )

        accounting = AccountingService(InMemoryUsageStore())
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=5.0,
                scope=UsageScope(
                    organization_id=organization.id,
                    owner_type="user",
                    owner_id="worker-owner",
                ),
            )
        )
        authorization = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="alice",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.VIEW}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
                LocalPrincipalPolicy(
                    principal_ref="mallory",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.READ}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            authorization=ControlPlaneAuthorizationBridge(AuthorizationGate(authorization)),
            organization_service=organizations,
            accounting_service=accounting,
        )

        allowed = await control_plane.list_extension_resources(
            _context("alice"), "usage-aggregates", PageQuery()
        )
        items = allowed["items"]
        assert isinstance(items, list)
        organization_items = [
            item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("scope"), dict)
            and item["scope"].get("organization_id") == organization.id
        ]
        assert len(organization_items) == 1
        assert organization_items[0]["total"] == 5.0

        # Mallory has the same #87 membership/accounting grant as Alice, but #15 lacks VIEW.
        # Membership can narrow visible accounting; it must never widen an authorization denial.
        with pytest.raises(ContractError) as denied:
            await control_plane.list_extension_resources(
                _context("mallory"), "usage-aggregates", PageQuery()
            )
        assert denied.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_distributed_provider_and_transport_replacement_preserves_node_worker_attribution() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    node_id = new_id("node")
    worker_id = new_id("worker")
    resources = ResourceSnapshot(
        cpu_cores_total=8.0,
        cpu_cores_available=4.0,
        ram_total_bytes=32_000,
        ram_available_bytes=16_000,
        storage_total_bytes=1_000_000,
        storage_available_bytes=500_000,
    )

    def emit(provider_name: str, transport_name: str, sequence: int) -> None:
        exporter = InMemoryExporter()
        telemetry = Telemetry(AccountingBridgeExporter(exporter, accounting))
        runtime = DistributedRuntime(
            DistributedRegistry(heartbeat_timeout=timedelta(seconds=30)),
            telemetry=DistributedTelemetry(telemetry),
        )
        adapter_metadata = (
            AdapterMetadata(namespace="provider", values={"name": provider_name}),
            AdapterMetadata(namespace="transport", values={"name": transport_name}),
        )
        node = NodeRecord(
            node_id=node_id,
            display_name="stable-node",
            resources=resources,
            adapter_metadata=adapter_metadata,
            registered_at=BASE,
            last_heartbeat_at=BASE,
        )
        worker = WorkerRecord(
            worker_id=worker_id,
            node_id=node_id,
            concurrency_limit=4,
            active_jobs=sequence,
            adapter_metadata=adapter_metadata,
            registered_at=BASE,
            last_heartbeat_at=BASE,
        )
        runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=BASE)
        runtime.heartbeat(
            Heartbeat(
                node_id=node_id,
                observed_at=BASE + timedelta(seconds=sequence),
                sequence=sequence,
                resources=resources,
                workers=(worker,),
            )
        )

    emit("provider-a", "transport-a", 1)
    emit("provider-b", "transport-b", 2)

    node_records = accounting.query(
        UsageQuery(metric_type="node.cpu.cores.capacity", unit="cores")
    )
    worker_records = accounting.query(
        UsageQuery(metric_type="worker.jobs.active", unit="count")
    )
    assert len(node_records) == 2
    assert len(worker_records) == 2
    assert {record.scope.node_id for record in node_records} == {node_id}
    assert {record.scope.node_id for record in worker_records} == {node_id}
    assert {record.scope.worker_id for record in worker_records} == {worker_id}
    assert accounting.aggregate(
        UsageQuery(metric_type="node.cpu.cores.capacity", unit="cores")
    ).total == 8.0
    assert accounting.aggregate(
        UsageQuery(metric_type="worker.jobs.active", unit="count")
    ).total == 2.0
