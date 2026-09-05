from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AgentRunUsageAttributor,
    AggregationMode,
    FileStorageAccounting,
    InMemoryUsageStore,
    MeasurementQuality,
    SQLiteUsageStore,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
    WorkspaceSnapshotAccounting,
)
from ai_multi_agent_platform.accounting.service import usage_from_metric
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.notifications import (
    RecipientRef,
    RecipientType,
    budget_threshold_candidate,
)
from ai_multi_agent_platform.observability import MetricRecord, TelemetryContext
from ai_multi_agent_platform.organizations.accounting import (
    DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,
    OrganizationAccountingVisibility,
)
from ai_multi_agent_platform.organizations.models import (
    Membership,
    MembershipStatus,
    Organization,
)
from ai_multi_agent_platform.organizations.repository import InMemoryOrganizationRepository
from ai_multi_agent_platform.organizations.service import OrganizationService
from ai_multi_agent_platform.security.authorization import ActorType
from ai_multi_agent_platform.workspaces.models import (
    Workspace,
    WorkspaceFile,
    WorkspaceSnapshot,
    WorkspaceStatus,
    WorkspaceType,
)


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-171",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref="user:alice",
    )


def _request_context(actor_id: str) -> RequestContext:
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


def test_latest_gauges_keep_one_latest_value_per_canonical_scope() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    start = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
    node_a = new_id("node")
    node_b = new_id("node")
    for record in (
        UsageRecord(
            metric_type="node.memory.bytes.available",
            unit="bytes",
            quality=MeasurementQuality.REPORTED,
            source="node",
            quantity=10.0,
            aggregation_mode=AggregationMode.LATEST,
            scope=UsageScope(node_id=node_a),
            timestamp=start,
        ),
        UsageRecord(
            metric_type="node.memory.bytes.available",
            unit="bytes",
            quality=MeasurementQuality.REPORTED,
            source="node",
            quantity=20.0,
            aggregation_mode=AggregationMode.LATEST,
            scope=UsageScope(node_id=node_b),
            timestamp=start,
        ),
        UsageRecord(
            metric_type="node.memory.bytes.available",
            unit="bytes",
            quality=MeasurementQuality.REPORTED,
            source="node",
            quantity=7.0,
            aggregation_mode=AggregationMode.LATEST,
            scope=UsageScope(node_id=node_a),
            timestamp=start + timedelta(seconds=1),
        ),
    ):
        accounting.record(record)

    aggregate = accounting.aggregate(
        UsageQuery(metric_type="node.memory.bytes.available", unit="bytes")
    )
    assert aggregate.total == 27.0
    assert aggregate.record_count == 3
    assert aggregate.aggregation_mode is AggregationMode.LATEST


def test_node_worker_runtime_metrics_are_explicit_and_reservations_are_not_consumption() -> None:
    node_id = new_id("node")
    worker_id = new_id("worker")
    node = usage_from_metric(
        MetricRecord(
            "platform.node.cpu_cores_available",
            6.0,
            unit="cores",
            context=TelemetryContext(node_id=node_id),
        )
    )
    worker = usage_from_metric(
        MetricRecord(
            "platform.worker.active_jobs",
            2.0,
            context=TelemetryContext(node_id=node_id, worker_id=worker_id),
        )
    )
    reservation = usage_from_metric(
        MetricRecord(
            "platform.scheduler.reserved_cpu_cores",
            4.0,
            unit="cores",
            context=TelemetryContext(node_id=node_id, worker_id=worker_id),
        )
    )
    placement_only_accelerator_gauge = usage_from_metric(
        MetricRecord(
            "platform.node.accelerator_memory_available_bytes",
            1024.0,
            unit="bytes",
            context=TelemetryContext(node_id=node_id),
            attributes={"aggregation": "max_single_accelerator"},
        )
    )

    assert node is not None
    assert node.metric_type == "node.cpu.cores.available"
    assert node.scope.node_id == node_id
    assert node.quality is MeasurementQuality.REPORTED
    assert node.aggregation_mode is AggregationMode.LATEST
    assert worker is not None
    assert worker.metric_type == "worker.jobs.active"
    assert worker.scope.worker_id == worker_id
    assert worker.aggregation_mode is AggregationMode.LATEST
    assert reservation is None
    assert placement_only_accelerator_gauge is None


def test_executed_agent_revision_is_provenance_only_and_never_guessed() -> None:
    run_id = new_id("run")
    agent_id = new_id("agent")
    team_id = new_id("agent_team")
    reader = SimpleNamespace(
        list_agent_runs=lambda requested=None: (
            (
                SimpleNamespace(
                    agent_run_id=new_id("agent_run"),
                    agent=SimpleNamespace(agent_id=agent_id, revision=7),
                    team=SimpleNamespace(team_id=team_id, revision=3),
                    orchestrator_adapter_id="orchestrator:test",
                ),
            )
            if requested in {None, run_id}
            else ()
        ),
    )
    accounting = AccountingService(
        InMemoryUsageStore(),
        usage_attributor=AgentRunUsageAttributor(reader),
    )
    accounting.ingest_metric(
        MetricRecord(
            "platform.model.calls",
            1.0,
            context=TelemetryContext(
                run_id=run_id,
                agent_id=agent_id,
                team_id=team_id,
                worker_id=new_id("worker"),
            ),
        )
    )
    record = accounting.query()[0]
    assert record.scope.agent_id == agent_id
    assert record.scope.team_id == team_id
    assert record.provenance["agent_revision"] == 7
    assert record.provenance["team_revision"] == 3
    assert record.provenance["orchestrator_adapter_id"] == "orchestrator:test"

    unattributed = AgentRunUsageAttributor(reader)(
        replace(record, scope=replace(record.scope, agent_id=None), provenance={})
    )
    assert unattributed.provenance == {}
    assert unattributed.scope.agent_id is None


def test_workspace_snapshot_uses_logical_bytes_without_duplicate_physical_storage(tmp_path) -> None:
    project_id = new_id("project")
    context = _data_context(project_id)
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    file_record = asyncio.run(files.create_file(b"abc", context))
    workspace = Workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="user", id="alice"),
        workspace_type=WorkspaceType.PERSISTENT_PROJECT,
    )
    snapshot = WorkspaceSnapshot(
        workspace_id=workspace.id,
        revision=1,
        files=(
            WorkspaceFile(
                relative_path="src/a.txt",
                file_id=file_record.file_id,
                sha256=file_record.sha256,
            ),
            WorkspaceFile(
                relative_path="copy/a.txt",
                file_id=file_record.file_id,
                sha256=file_record.sha256,
            ),
        ),
        content_checksum="0" * 64,
    )
    accounting = AccountingService(InMemoryUsageStore())
    logical = WorkspaceSnapshotAccounting(accounting, files)
    references, logical_bytes = asyncio.run(logical.reconcile(workspace, snapshot, context))

    assert references.quantity == 2.0
    assert logical_bytes.quantity == 6.0
    assert logical_bytes.scope.workspace_id == workspace.id
    assert logical_bytes.provenance["unique_file_count"] == 1
    assert logical_bytes.provenance["physical_storage_counted_here"] is False

    physical = FileStorageAccounting(accounting, files)
    project_storage = asyncio.run(physical.reconcile(context))
    assert project_storage.quantity == 3.0
    with pytest.raises(ValueError, match="cannot be attributed to a Workspace"):
        asyncio.run(
            physical.reconcile(
                context,
                scope=UsageScope(project_id=project_id, workspace_id=workspace.id),
            )
        )

    deleted = replace(workspace, status=WorkspaceStatus.DELETED)
    retired_references, retired_bytes = logical.retire(deleted)
    assert retired_references.quantity == 0.0
    assert retired_bytes.quantity == 0.0


def test_membership_policy_grants_aggregate_scope_but_suspension_revokes_future_visibility() -> (
    None
):
    async def scenario() -> None:
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await repository.save_organization(
            Organization(name="Example", owner_actor_id="owner")
        )
        member = Membership(
            actor_id="alice",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
            policy_refs=(DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,),
        )
        await repository.save_membership(member)
        visibility = OrganizationAccountingVisibility(service)
        context = _request_context("alice")
        assert await visibility.can_aggregate_organization(context, organization.id)

        await repository.save_membership(
            replace(
                member,
                status=MembershipStatus.SUSPENDED,
                suspended_at=datetime(2026, 9, 5, 20, 0, tzinfo=UTC),
            )
        )
        assert not await visibility.can_aggregate_organization(context, organization.id)
        owner_context = _request_context("owner")
        assert await visibility.can_aggregate_organization(owner_context, organization.id)

    asyncio.run(scenario())


def test_threshold_generation_survives_restart_and_allows_later_recross(tmp_path) -> None:
    path = tmp_path / "usage.sqlite3"
    store = SQLiteUsageStore(path)
    events = []
    accounting = AccountingService(store, threshold_event_sink=events.append)
    project_id = new_id("project")
    budget = UsageBudget(
        metric_type="storage.file.bytes.current",
        unit="bytes",
        scope_type="project",
        scope_id=project_id,
        limit=10.0,
        owner_type="user",
        owner_id="alice",
    )
    accounting.put_budget(budget)
    start = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)

    def record(quantity: float, offset: int) -> None:
        accounting.record(
            UsageRecord(
                metric_type="storage.file.bytes.current",
                unit="bytes",
                quality=MeasurementQuality.REPORTED,
                source="test",
                quantity=quantity,
                aggregation_mode=AggregationMode.LATEST,
                scope=UsageScope(project_id=project_id),
                timestamp=start + timedelta(seconds=offset),
            )
        )

    record(10.0, 0)
    assert store.get_threshold_generation(budget.id) == 1
    first = budget_threshold_candidate(
        events[-1],
        recipient=RecipientRef(RecipientType.USER, "alice"),
        threshold_generation=store.get_threshold_generation(budget.id),
    )

    restarted_store = SQLiteUsageStore(path)
    assert restarted_store.get_threshold_generation(budget.id) == 1
    assert restarted_store.get_threshold_level(budget.id) is not None

    record(0.0, 1)
    assert store.get_threshold_level(budget.id) is None
    assert store.get_threshold_generation(budget.id) == 1
    record(10.0, 2)
    assert store.get_threshold_generation(budget.id) == 2
    second = budget_threshold_candidate(
        events[-1],
        recipient=RecipientRef(RecipientType.USER, "alice"),
        threshold_generation=store.get_threshold_generation(budget.id),
    )
    assert first.aggregation_key != second.aggregation_key

    restarted_again = SQLiteUsageStore(path)
    recovered = budget_threshold_candidate(
        events[-1],
        recipient=RecipientRef(RecipientType.USER, "alice"),
        threshold_generation=restarted_again.get_threshold_generation(budget.id),
    )
    assert recovered.aggregation_key == second.aggregation_key
