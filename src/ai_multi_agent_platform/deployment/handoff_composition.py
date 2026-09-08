"""Durable Agent Handoff composition for the public single-node deployment (#651)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.agents import AgentRepository, AgentRuntime
from ai_multi_agent_platform.context import (
    ContextAssemblyService,
    ContextBoundAgentRuntime,
    ContextResolver,
    JsonContextBundleRepository,
    JsonContextRunBindingRepository,
    register_context_control_plane,
)
from ai_multi_agent_platform.contracts import AuthorizationProvider
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.handoffs import (
    CanonicalConsumerRequirementEvaluator,
    CanonicalHandoffReferenceGateway,
    CoordinatedHandoffService,
    HandoffService,
    ProductionHandoffRuntime,
    SQLiteHandoffRepository,
    TelemetryHandoffAuditSink,
    build_production_handoff_runtime,
    register_handoff_control_plane,
)
from ai_multi_agent_platform.observability import Telemetry
from ai_multi_agent_platform.research import SqliteResearchRepository
from ai_multi_agent_platform.skills import JsonSkillRepository
from ai_multi_agent_platform.verification import VerificationEvidenceResolver


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
    context_runtime: ContextBoundAgentRuntime
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
    authorization: AuthorizationProvider,
    verification: VerificationEvidenceResolver,
    telemetry: Telemetry,
    research_repository: SqliteResearchRepository | None = None,
    skill_repository: JsonSkillRepository | None = None,
    context_bundle_repository: JsonContextBundleRepository | None = None,
    context_binding_repository: JsonContextRunBindingRepository | None = None,
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
    context_runtime = ContextBoundAgentRuntime(
        agent_runtime,
        bundle_repository=context_bundle_repository,
        binding_repository=context_binding_repository,
    )

    references = CanonicalHandoffReferenceGateway(
        authorization=authorization,
        verification=verification,
        research=research_repository,
        skills=skill_repository,
        contexts=context_bundle_repository,
    )
    consumer_requirements = CanonicalConsumerRequirementEvaluator(agents)
    audit = TelemetryHandoffAuditSink(telemetry)
    runtime = build_production_handoff_runtime(
        repository=repository,
        agents=agents,
        references=references,
        coordinator=coordinator,
        context_assembly=context_assembly,
        context_runtime=context_runtime,
        audit=audit,
        consumer_requirements=consumer_requirements,
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
