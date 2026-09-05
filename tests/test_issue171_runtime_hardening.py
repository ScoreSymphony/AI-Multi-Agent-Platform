from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AgentRunUsageAttributor,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageQuery,
    UsageRecord,
    UsageScope,
    WorkspaceSnapshotAccounting,
)
from ai_multi_agent_platform.agents import (
    AgentDefinition,
    AgentExecutionSpec,
    AgentInstructions,
    AgentProfile,
    AgentRevision,
    AgentRevisionRef,
    AgentRuntime,
    AgentService,
    AgentTeamDefinition,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevision,
    InMemoryAgentRepository,
    InstructionSource,
    OrchestratorMapping,
)
from ai_multi_agent_platform.contracts import ExecutionHandle, OperationContext, WorkerDescriptor
from ai_multi_agent_platform.contracts.types import ExecutionRequest
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
    AcceleratorResource,
    DistributedRegistry,
    DistributedRuntime,
    DistributedTelemetry,
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.messaging import (
    InProcessMessageTransport,
    MessageKind,
    Subscription,
    TransportEnvelope,
)
from ai_multi_agent_platform.observability import (
    AccountingBridgeExporter,
    InMemoryExporter,
    MetricRecord,
    ObservedWorkerProvider,
    Telemetry,
    TelemetryContext,
    TraceHierarchy,
    extract_trace_carrier,
    inject_trace_carrier,
    observe_agent_run,
)
from ai_multi_agent_platform.organizations.accounting import (
    DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,
    organization_accounting_resource_services,
)
from ai_multi_agent_platform.organizations.models import (
    Membership,
    MembershipStatus,
    Organization,
    Team,
)
from ai_multi_agent_platform.organizations.repository import InMemoryOrganizationRepository
from ai_multi_agent_platform.organizations.service import OrganizationService
from ai_multi_agent_platform.security.authorization import ActorType
from ai_multi_agent_platform.testing import FakeWorkerProvider
from ai_multi_agent_platform.workspaces.models import (
    Workspace,
    WorkspaceFile,
    WorkspaceSnapshot,
    WorkspaceStatus,
    WorkspaceType,
)

BASE = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


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


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-171-hardening",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref="alice",
    )


def _agent_fixture() -> tuple[
    InMemoryAgentRepository,
    AgentRuntime,
    AgentDefinition,
    AgentRevision,
    AgentTeamDefinition,
    AgentTeamRevision,
]:
    repository = InMemoryAgentRepository()
    owner = OwnerRef(type="user", id="alice")
    agent_id = new_id("agent")
    profile = AgentProfile(
        name="Accounting executor",
        role="execute canonical work",
        instructions=AgentInstructions(
            role=InstructionSource(content="Execute the task."),
        ),
    )
    revision = AgentRevision(
        agent_id=agent_id,
        revision=1,
        profile=profile,
        owner_ref=owner,
    )
    definition = AgentDefinition(
        agent_id=agent_id,
        owner_ref=owner,
        current_revision=1,
    )
    repository.create_agent(definition, revision)

    team_id = new_id("team")
    team_revision = AgentTeamRevision(
        team_id=team_id,
        revision=1,
        owner_ref=owner,
        profile=AgentTeamProfile(
            name="Accounting team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(agent_id=agent_id, revision=1),
                    role="executor",
                ),
            ),
        ),
    )
    team_definition = AgentTeamDefinition(
        team_id=team_id,
        owner_ref=owner,
        current_revision=1,
    )
    repository.create_team(team_definition, team_revision)
    return (
        repository,
        AgentRuntime(AgentService(repository)),
        definition,
        revision,
        team_definition,
        team_revision,
    )


class _ReplacementMapper:
    adapter_id = "replacement-orchestrator"

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping:
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=f"replacement:{spec.agent_revision.agent_id}:{spec.run_id}",
            metadata={},
        )


class _TransportingWorkerProvider(FakeWorkerProvider):
    def __init__(
        self,
        *,
        workers: tuple[WorkerDescriptor, ...],
        hierarchy: TraceHierarchy,
        transport: InProcessMessageTransport,
        topic: str,
    ) -> None:
        super().__init__(workers=workers)
        self._hierarchy = hierarchy
        self._transport = transport
        self._topic = topic

    async def dispatch(
        self,
        worker_id: str,
        request: ExecutionRequest,
    ) -> ExecutionHandle:
        envelope = inject_trace_carrier(
            TransportEnvelope(
                message_type="worker.dispatch",
                kind=MessageKind.COMMAND,
                payload_schema_version="1.0",
                source_component="issue-171-hardening",
                correlation_id=request.context.correlation_id,
                payload={"worker_id": worker_id},
            ),
            self._hierarchy.current_carrier(),
        )
        await self._transport.publish(self._topic, envelope)
        return await super().dispatch(worker_id, request)


def test_real_distributed_heartbeat_feeds_canonical_node_worker_accounting() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    telemetry = Telemetry(AccountingBridgeExporter(InMemoryExporter(), accounting))
    runtime = DistributedRuntime(
        DistributedRegistry(heartbeat_timeout=timedelta(seconds=30)),
        telemetry=DistributedTelemetry(telemetry),
    )
    node_id = new_id("node")
    worker_id = new_id("worker")
    resources = ResourceSnapshot(
        cpu_cores_total=8.0,
        cpu_cores_available=5.0,
        ram_total_bytes=32_000,
        ram_available_bytes=20_000,
        storage_total_bytes=1_000_000,
        storage_available_bytes=750_000,
        accelerators=(
            AcceleratorResource(
                accelerator_id="gpu-0",
                memory_total_bytes=16_000,
                memory_available_bytes=12_000,
            ),
        ),
    )
    node = NodeRecord(
        node_id=node_id,
        display_name="real-node",
        resources=resources,
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )
    worker = WorkerRecord(
        worker_id=worker_id,
        node_id=node_id,
        concurrency_limit=4,
        active_jobs=2,
        registered_at=BASE,
        last_heartbeat_at=BASE,
    )
    runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=BASE)
    runtime.heartbeat(
        Heartbeat(
            node_id=node_id,
            observed_at=BASE + timedelta(seconds=1),
            sequence=1,
            resources=resources,
            workers=(worker,),
        )
    )

    by_metric = {record.metric_type: record for record in accounting.query()}
    expected = {
        "node.cpu.cores.capacity": 8.0,
        "node.cpu.cores.available": 5.0,
        "node.memory.bytes.capacity": 32_000.0,
        "node.memory.bytes.available": 20_000.0,
        "node.storage.bytes.capacity": 1_000_000.0,
        "node.storage.bytes.available": 750_000.0,
        "node.accelerator.memory.bytes.capacity": 16_000.0,
        "node.accelerator.memory.bytes.available": 12_000.0,
        "worker.jobs.active": 2.0,
        "worker.jobs.capacity": 4.0,
    }
    for metric_type, quantity in expected.items():
        assert by_metric[metric_type].quantity == quantity
        assert by_metric[metric_type].quality is MeasurementQuality.REPORTED
    assert by_metric["node.cpu.cores.capacity"].scope.node_id == node_id
    assert by_metric["worker.jobs.active"].scope.worker_id == worker_id
    assert by_metric["worker.jobs.active"].scope.node_id == node_id
    assert not any(
        record.metric_type.startswith("scheduler.reserved")
        for record in accounting.query()
    )


def test_real_agent_team_run_remains_explainable_after_revision_and_mapper_change() -> None:
    async def scenario() -> None:
        repository, runtime, definition, revision, team_definition, team_revision = (
            _agent_fixture()
        )
        task_id = new_id("task")
        first_run_id = new_id("run")
        first_runs = await runtime.start_team(
            task_id=task_id,
            run_id=first_run_id,
            team_id=team_definition.team_id,
        )
        executed = first_runs[0]

        accounting = AccountingService(
            InMemoryUsageStore(),
            usage_attributor=AgentRunUsageAttributor(repository),
        )
        requested_but_not_executed = new_id("agent")
        accounting.ingest_metric(
            MetricRecord(
                "platform.model.calls",
                1.0,
                context=TelemetryContext(
                    task_id=task_id,
                    run_id=first_run_id,
                    agent_id=executed.agent.agent_id,
                    team_id=executed.team.team_id if executed.team is not None else None,
                ),
                attributes={"requested_agent_id": requested_but_not_executed},
            )
        )
        historical = accounting.query()[0]
        assert historical.scope.agent_id == revision.agent_id
        assert historical.scope.agent_id != requested_but_not_executed
        assert historical.provenance["agent_revision"] == 1
        assert historical.provenance["team_revision"] == 1
        assert (
            historical.provenance["orchestrator_adapter_id"]
            == "reference-orchestrator"
        )

        second_revision = AgentRevision(
            agent_id=revision.agent_id,
            revision=2,
            profile=replace(revision.profile, description="revision two"),
            owner_ref=revision.owner_ref,
        )
        repository.update_agent(
            replace(
                definition,
                current_revision=2,
                updated_at=definition.updated_at + timedelta(seconds=1),
            ),
            second_revision,
        )
        second_team_revision = AgentTeamRevision(
            team_id=team_revision.team_id,
            revision=2,
            owner_ref=team_revision.owner_ref,
            profile=replace(
                team_revision.profile,
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(agent_id=revision.agent_id, revision=2),
                        role="executor",
                    ),
                ),
            ),
        )
        repository.update_team(
            replace(
                team_definition,
                current_revision=2,
                updated_at=team_definition.updated_at + timedelta(seconds=1),
            ),
            second_team_revision,
        )

        second_run_id = new_id("run")
        second_runs = await runtime.start_team(
            task_id=task_id,
            run_id=second_run_id,
            team_id=team_definition.team_id,
            revision=2,
            mapper=_ReplacementMapper(),
        )
        second = second_runs[0]
        accounting.ingest_metric(
            MetricRecord(
                "platform.model.calls",
                1.0,
                context=TelemetryContext(
                    task_id=task_id,
                    run_id=second_run_id,
                    agent_id=second.agent.agent_id,
                    team_id=second.team.team_id if second.team is not None else None,
                ),
            )
        )

        records = accounting.query(
            UsageQuery(metric_type="model.call.count", unit="count")
        )
        assert len(records) == 2
        assert records[0].scope.agent_id == revision.agent_id
        assert records[1].scope.agent_id == revision.agent_id
        assert records[0].scope.team_id == team_revision.team_id
        assert records[1].scope.team_id == team_revision.team_id
        assert records[0].provenance["agent_revision"] == 1
        assert records[0].provenance["team_revision"] == 1
        assert records[1].provenance["agent_revision"] == 2
        assert records[1].provenance["team_revision"] == 2
        assert (
            records[1].provenance["orchestrator_adapter_id"]
            == "replacement-orchestrator"
        )

    asyncio.run(scenario())


def test_remote_worker_trace_preserves_executed_agent_team_into_accounting() -> None:
    async def scenario() -> None:
        repository, agent_runtime, _, _, team_definition, team_revision = _agent_fixture()
        task_id = new_id("task")
        run_id = new_id("run")
        agent_id = team_revision.profile.members[0].agent.agent_id
        executed = await agent_runtime.start_agent(
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            team_revision=team_revision,
        )
        accounting = AccountingService(
            InMemoryUsageStore(),
            usage_attributor=AgentRunUsageAttributor(repository),
        )
        telemetry = Telemetry(AccountingBridgeExporter(InMemoryExporter(), accounting))
        hierarchy = TraceHierarchy(telemetry)
        transport = InProcessMessageTransport()
        topic = "issue-171-accounting-worker"
        node_id = new_id("node")
        worker_id = new_id("worker")
        worker = ObservedWorkerProvider(
            _TransportingWorkerProvider(
                workers=(WorkerDescriptor(worker_id=worker_id, node_id=node_id),),
                hierarchy=hierarchy,
                transport=transport,
                topic=topic,
            ),
            telemetry,
            hierarchy=hierarchy,
        )
        operation = OperationContext(
            correlation_id=task_id,
            causation_id=executed.agent_run_id,
        )

        async def execute() -> None:
            await worker.dispatch(
                worker_id,
                ExecutionRequest(
                    run_id=run_id,
                    subject_type="task",
                    subject_id=task_id,
                    context=operation,
                ),
            )

        await observe_agent_run(
            hierarchy,
            agent_id=executed.agent.agent_id,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                agent_id=executed.agent.agent_id,
                team_id=team_definition.team_id,
                correlation_id=task_id,
            ),
            operation=execute,
        )

        subscription = transport.subscribe(
            Subscription(topic=topic, consumer_id="issue-171")
        )
        delivery = await anext(subscription)
        carrier = extract_trace_carrier(delivery.envelope)
        await transport.ack(delivery)
        await subscription.aclose()
        assert carrier.agent_id == executed.agent.agent_id
        assert carrier.team_id == team_definition.team_id
        assert carrier.worker_id == worker_id

        dispatch = accounting.query(
            UsageQuery(metric_type="worker.dispatch.count", unit="count")
        )
        assert len(dispatch) == 1
        record = dispatch[0]
        assert record.scope.run_id == run_id
        assert record.scope.agent_id == executed.agent.agent_id
        assert record.scope.team_id == team_definition.team_id
        assert record.scope.worker_id == worker_id
        assert record.provenance["agent_revision"] == 1
        assert record.provenance["team_revision"] == 1

    asyncio.run(scenario())


def test_team_grant_does_not_expand_to_organization_or_other_team() -> None:
    async def scenario() -> None:
        repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(repository)
        org_a = await repository.save_organization(
            Organization(name="A", owner_actor_id="owner-a")
        )
        org_b = await repository.save_organization(
            Organization(name="B", owner_actor_id="owner-b")
        )
        team_a = await repository.save_team(
            Team(organization_id=org_a.id, name="A team")
        )
        other_team = await repository.save_team(
            Team(organization_id=org_a.id, name="Other team")
        )
        membership = await repository.save_membership(
            Membership(
                actor_id="alice",
                actor_type=ActorType.HUMAN,
                organization_id=org_a.id,
                team_id=team_a.id,
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
                quantity=3.0,
                scope=UsageScope(
                    organization_id=org_a.id,
                    team_id=team_a.id,
                    task_id=new_id("task"),
                    run_id=new_id("run"),
                    agent_id=new_id("agent"),
                    owner_type="user",
                    owner_id="bob",
                ),
                provenance={"membership_at_record_time": "historical"},
            )
        )
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=40.0,
                scope=UsageScope(
                    organization_id=org_a.id,
                    team_id=other_team.id,
                    owner_type="user",
                    owner_id="carol",
                ),
            )
        )
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=500.0,
                scope=UsageScope(
                    organization_id=org_b.id,
                    owner_type="user",
                    owner_id="mallory",
                ),
            )
        )

        services = organization_accounting_resource_services(accounting, organizations)
        context = _request_context("alice")
        aggregates = await services["usage-aggregates"].list_resources(
            context,
            PageQuery(),
        )
        team_aggregates = [
            item
            for item in aggregates
            if isinstance(item.get("scope"), dict)
            and item["scope"].get("team_id") == team_a.id
        ]
        assert len(team_aggregates) == 1
        assert team_aggregates[0]["total"] == 3.0
        assert team_aggregates[0]["scope"].get("task_id") is None
        assert team_aggregates[0]["scope"].get("run_id") is None
        assert team_aggregates[0]["scope"].get("agent_id") is None
        assert not any(
            isinstance(item.get("scope"), dict)
            and item["scope"].get("owner_type") == "organization"
            for item in aggregates
        )

        raw = await services["usage-records"].list_resources(context, PageQuery())
        assert raw == ()
        before = accounting.query()[0]
        await repository.save_membership(
            replace(
                membership,
                status=MembershipStatus.SUSPENDED,
                suspended_at=BASE + timedelta(seconds=10),
            )
        )
        after = await services["usage-aggregates"].list_resources(
            context,
            PageQuery(),
        )
        assert not any(
            isinstance(item.get("scope"), dict)
            and item["scope"].get("team_id") == team_a.id
            for item in after
        )
        assert accounting.query()[0] == before
        assert (
            accounting.query()[0].provenance["membership_at_record_time"]
            == "historical"
        )

    asyncio.run(scenario())


def test_workspace_archive_retains_latest_snapshot_until_deletion(tmp_path) -> None:
    project_id = new_id("project")
    context = _data_context(project_id)
    files = LocalFileProvider(
        tmp_path / "files",
        tmp_path / "files.sqlite3",
    )
    first = asyncio.run(files.create_file(b"abc", context))
    second = asyncio.run(files.create_file(b"defgh", context))
    workspace = Workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="user", id="alice"),
        workspace_type=WorkspaceType.PERSISTENT_PROJECT,
    )
    accounting = AccountingService(InMemoryUsageStore())
    snapshots = WorkspaceSnapshotAccounting(accounting, files)
    first_snapshot = WorkspaceSnapshot(
        workspace_id=workspace.id,
        revision=1,
        files=(
            WorkspaceFile(
                relative_path="a.txt",
                file_id=first.file_id,
                sha256=first.sha256,
            ),
        ),
        content_checksum="1" * 64,
    )
    asyncio.run(
        snapshots.reconcile(
            workspace,
            first_snapshot,
            context,
            observed_at=BASE,
        )
    )

    archived = replace(workspace, status=WorkspaceStatus.ARCHIVED)
    second_snapshot = WorkspaceSnapshot(
        workspace_id=workspace.id,
        revision=2,
        files=(
            WorkspaceFile(
                relative_path="a.txt",
                file_id=first.file_id,
                sha256=first.sha256,
            ),
            WorkspaceFile(
                relative_path="b.txt",
                file_id=second.file_id,
                sha256=second.sha256,
            ),
        ),
        content_checksum="2" * 64,
        parent_snapshot_id=first_snapshot.id,
    )
    asyncio.run(
        snapshots.reconcile(
            archived,
            second_snapshot,
            context,
            observed_at=BASE + timedelta(seconds=1),
        )
    )
    aggregate = accounting.aggregate(
        UsageQuery(
            metric_type="workspace.snapshot.logical_bytes.current",
            unit="bytes",
            scope=UsageScope(workspace_id=workspace.id),
        )
    )
    assert aggregate.total == 8.0

    deleted = replace(archived, status=WorkspaceStatus.DELETED)
    snapshots.retire(
        deleted,
        observed_at=BASE + timedelta(seconds=2),
    )
    retired = accounting.aggregate(
        UsageQuery(
            metric_type="workspace.snapshot.logical_bytes.current",
            unit="bytes",
            scope=UsageScope(workspace_id=workspace.id),
        )
    )
    assert retired.total == 0.0
