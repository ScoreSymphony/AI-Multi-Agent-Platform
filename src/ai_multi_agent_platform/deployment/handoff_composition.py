"""Durable Agent Handoff composition for the public single-node deployment (#651)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.agents import (
    AgentRepository,
    AgentResolver,
    AgentRevisionRef,
    AgentRuntime,
)
from ai_multi_agent_platform.context import (
    ContextAssemblyRequest,
    ContextAssemblyService,
    ContextAwareOrchestratorAdapter,
    ContextBudget,
    ContextCandidate,
    ContextResolver,
    JsonContextBundleRepository,
    JsonContextRunBindingRepository,
    ModelRegistryContextEgressTargetResolver,
    OperationalContextBoundAgentRuntime,
    register_context_control_plane,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationProvider,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.handoffs import (
    CanonicalConsumerRequirementEvaluator,
    CanonicalHandoffReferenceGateway,
    CoordinatedHandoffService,
    DurableConsumedHandoffContextAdapter,
    HandoffConsumerExecution,
    HandoffService,
    ParticipantRef,
    ProductionHandoffRuntime,
    SQLiteHandoffRepository,
    TelemetryHandoffAuditSink,
    register_handoff_control_plane,
)
from ai_multi_agent_platform.kernel import EventSourcedTaskRepository
from ai_multi_agent_platform.models import ModelRuntime
from ai_multi_agent_platform.observability import Telemetry
from ai_multi_agent_platform.research import SqliteResearchRepository
from ai_multi_agent_platform.security import ActorIdentity, EgressGate
from ai_multi_agent_platform.skills import JsonSkillRepository
from ai_multi_agent_platform.verification import VerificationEvidenceResolver


class _OperationalProductionHandoffRuntime(ProductionHandoffRuntime):
    """Bridge #651 consumption into the production #650/#591 Context runtime."""

    def __init__(
        self,
        *,
        service: HandoffService,
        coordinated: CoordinatedHandoffService,
        repository: SQLiteHandoffRepository,
        references: CanonicalHandoffReferenceGateway,
        agents: AgentRepository,
        context_assembly: ContextAssemblyService,
        context_runtime: OperationalContextBoundAgentRuntime,
        audit: TelemetryHandoffAuditSink,
        tasks: EventSourcedTaskRepository,
    ) -> None:
        super().__init__(
            service=service,
            coordinated=coordinated,
            repository=repository,
            references=references,
            agents=agents,
            context_assembly=context_assembly,
            context_runtime=context_runtime,  # type: ignore[arg-type]
            audit=audit,
        )
        self.tasks = tasks

    async def start_consumer(
        self,
        handoff_id: str,
        revision: int,
        *,
        consuming_run_id: str,
        consumer: ParticipantRef,
        consumer_actor: ActorIdentity,
        operation: OperationContext,
        budget: ContextBudget,
        consumer_agent: AgentRevisionRef | None = None,
        candidates: tuple[ContextCandidate, ...] = (),
        workspace_id: str | None = None,
        adapter: ContextAwareOrchestratorAdapter | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
    ) -> HandoffConsumerExecution:
        handoff = self.service.get_handoff(handoff_id, revision)
        task = await self.tasks.get_task(handoff.task_id)
        operation = _bind_handoff_task_project_scope(operation, task.task.project_id)
        runtime_context = await self.consume_handoff(
            handoff_id,
            revision,
            consuming_run_id=consuming_run_id,
            consumer=consumer,
            consumer_actor=consumer_actor,
            operation=operation,
        )
        execution_agent, team_revision = self._execution_identity(consumer, consumer_agent)
        handoff = runtime_context.handoff
        durable_adapter = DurableConsumedHandoffContextAdapter(self.repository, self.agents)
        bundle = await self.context_assembly.assemble(
            ContextAssemblyRequest(
                task_id=handoff.task_id,
                run_id=consuming_run_id,
                agent_id=execution_agent.agent_id,
                agent_revision=execution_agent.revision,
                actor=consumer_actor,
                operation=operation,
                candidates=candidates,
                budget=budget,
                workspace_id=workspace_id,
                plan_id=handoff.content.plan_id,
                step_id=handoff.content.consumer_step_id,
            ),
            adapters=(durable_adapter,),
        )
        self._require_bundle_contains_handoff(bundle, runtime_context)
        context_runtime = cast(OperationalContextBoundAgentRuntime, self.context_runtime)
        context_execution = await context_runtime.start_agent_binding(
            bundle=bundle,
            operation=operation,
            adapter=adapter,
            team_revision=team_revision,
            shared_capability_ids=(
                () if team_revision is None else team_revision.profile.shared_capability_ids
            ),
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            verification_context={
                "handoff_id": handoff.handoff_id,
                "handoff_revision": handoff.revision,
                "handoff_digest": handoff.content_digest,
            },
        )
        record = context_execution.agent_run
        binding = context_execution.binding
        self._require_agent_run_matches(record, consumer, execution_agent)
        return HandoffConsumerExecution(
            runtime_context=runtime_context,
            context_bundle=bundle,
            agent_run=record,
            context_binding=binding,
        )


def _bind_handoff_task_project_scope(
    operation: OperationContext,
    task_project_id: str | None,
) -> OperationContext:
    if task_project_id is None:
        return operation
    if operation.project_id is not None and operation.project_id != task_project_id:
        raise ContractError(
            ErrorCode.NOT_FOUND,
            "Handoff Project scope conflicts with the canonical Task",
        )
    if operation.project_id == task_project_id:
        return operation
    return replace(operation, project_id=task_project_id)


@dataclass(slots=True)
class HandoffDeploymentComposition:
    """Long-lived durable resources backing the normal Agent Handoff runtime."""

    repository: SQLiteHandoffRepository
    research_repository: SqliteResearchRepository
    skill_repository: JsonSkillRepository
    context_bundle_repository: JsonContextBundleRepository
    context_binding_repository: JsonContextRunBindingRepository
    context_resolver: ContextResolver
    context_assembly: ContextAssemblyService
    context_runtime: OperationalContextBoundAgentRuntime
    references: CanonicalHandoffReferenceGateway
    consumer_requirements: CanonicalConsumerRequirementEvaluator
    audit: TelemetryHandoffAuditSink
    service: HandoffService
    coordinated: CoordinatedHandoffService
    runtime: ProductionHandoffRuntime


def build_single_node_handoff_composition(
    *,
    database_dir: Path,
    control_plane: ControlPlane,
    agents: AgentRepository,
    agent_runtime: AgentRuntime,
    coordinator: CoordinatorRepository,
    tasks: EventSourcedTaskRepository,
    authorization: AuthorizationProvider,
    verification: VerificationEvidenceResolver,
    telemetry: Telemetry,
    research_repository: SqliteResearchRepository | None = None,
    skill_repository: JsonSkillRepository | None = None,
    context_bundle_repository: JsonContextBundleRepository | None = None,
    context_binding_repository: JsonContextRunBindingRepository | None = None,
    egress_gate: EgressGate | None = None,
    model_runtime: ModelRuntime | None = None,
) -> HandoffDeploymentComposition:
    """Build the restart-safe #651 composition over existing platform authorities."""

    repository = SQLiteHandoffRepository(database_dir / "handoffs.sqlite3")
    research_repository = research_repository or SqliteResearchRepository(
        database_dir / "research.sqlite3"
    )
    skill_repository = skill_repository or JsonSkillRepository(database_dir / "skills.json")
    supplied_context = (
        context_bundle_repository is not None,
        context_binding_repository is not None,
    )
    if supplied_context[0] != supplied_context[1]:
        raise ValueError(
            "Handoff composition requires both canonical Context repositories or neither"
        )
    reuse_context_registration = all(supplied_context)
    context_bundle_repository = context_bundle_repository or JsonContextBundleRepository(
        database_dir / "context-bundles.json"
    )
    context_binding_repository = context_binding_repository or JsonContextRunBindingRepository(
        database_dir / "context-run-bindings.json"
    )

    context_resolver = ContextResolver(authorization)
    context_assembly = ContextAssemblyService(context_resolver, context_bundle_repository)
    target_resolver = (
        ModelRegistryContextEgressTargetResolver(agent_runtime.model_registry)
        if egress_gate is not None and agent_runtime.model_registry is not None
        else None
    )
    context_runtime = OperationalContextBoundAgentRuntime(
        agent_runtime,
        bundle_repository=context_bundle_repository,
        binding_repository=context_binding_repository,
        egress_gate=egress_gate,
        target_resolver=target_resolver,
        model_runtime=model_runtime if egress_gate is not None else None,
    )

    references = CanonicalHandoffReferenceGateway(
        authorization=authorization,
        verification=verification,
        research=research_repository,
        skills=skill_repository,
        contexts=context_bundle_repository,
    )
    consumer_requirements = CanonicalConsumerRequirementEvaluator(
        agents,
        resolver=AgentResolver(
            agents,
            capability_registry=agent_runtime.capability_registry,
            model_registry=agent_runtime.model_registry,
            routing_profiles=agent_runtime.routing_profiles,
        ),
    )
    audit = TelemetryHandoffAuditSink(telemetry)
    service = HandoffService(
        repository,
        agents=agents,
        references=references,
        audit=audit,
        consumer_requirements=consumer_requirements,
    )
    coordinated = CoordinatedHandoffService(service, coordinator)
    runtime = _OperationalProductionHandoffRuntime(
        service=service,
        coordinated=coordinated,
        repository=repository,
        references=references,
        agents=agents,
        context_assembly=context_assembly,
        context_runtime=context_runtime,
        audit=audit,
        tasks=tasks,
    )

    # Standalone Handoff composition owns Context registration only when no canonical
    # deployment Context composition was supplied. Re-registering supplied repositories would
    # overwrite the authorization-aware Context services installed by #650.
    if not reuse_context_registration:
        register_context_control_plane(
            control_plane,
            context_bundle_repository,
            context_binding_repository,
        )
    register_handoff_control_plane(control_plane, runtime.service)

    return HandoffDeploymentComposition(
        repository=repository,
        research_repository=research_repository,
        skill_repository=skill_repository,
        context_bundle_repository=context_bundle_repository,
        context_binding_repository=context_binding_repository,
        context_resolver=context_resolver,
        context_assembly=context_assembly,
        context_runtime=context_runtime,
        references=references,
        consumer_requirements=consumer_requirements,
        audit=audit,
        service=runtime.service,
        coordinated=runtime.coordinated,
        runtime=runtime,
    )


__all__ = ["HandoffDeploymentComposition", "build_single_node_handoff_composition"]
