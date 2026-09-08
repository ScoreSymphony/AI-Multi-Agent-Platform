"""Canonical composition helpers for the production Agent Handoff runtime (#651)."""

from __future__ import annotations

from ai_multi_agent_platform.agents import AgentRepository
from ai_multi_agent_platform.context import ContextAssemblyService, ContextBoundAgentRuntime
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository

from .coordination import CoordinatedHandoffService
from .production import CanonicalHandoffReferenceGateway, ProductionHandoffRuntime
from .repository import HandoffRepository
from .service import ConsumerRequirementEvaluator, HandoffAuditSink, HandoffService


def build_production_handoff_runtime(
    *,
    repository: HandoffRepository,
    agents: AgentRepository,
    references: CanonicalHandoffReferenceGateway,
    coordinator: CoordinatorRepository,
    context_assembly: ContextAssemblyService,
    context_runtime: ContextBoundAgentRuntime,
    audit: HandoffAuditSink,
    consumer_requirements: ConsumerRequirementEvaluator | None = None,
) -> ProductionHandoffRuntime:
    """Compose #592 contracts with canonical coordination/context execution boundaries.

    The helper owns no Task/Plan/Step/Run state. It only connects the durable Handoff
    repository to the existing Agent, coordination, authorization and ContextBundle
    authorities used by the deployment profile.
    """

    service = HandoffService(
        repository,
        agents=agents,
        references=references,
        audit=audit,
        consumer_requirements=consumer_requirements,
    )
    coordinated = CoordinatedHandoffService(service, coordinator)
    return ProductionHandoffRuntime(
        service=service,
        coordinated=coordinated,
        repository=repository,
        references=references,
        agents=agents,
        context_assembly=context_assembly,
        context_runtime=context_runtime,
        audit=audit,
    )
