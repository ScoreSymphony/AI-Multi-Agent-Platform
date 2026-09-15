"""Durable composition for the normal single-node/server runtime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.application_distribution import (
    ApplicationDistributionService,
    JsonApplicationReleaseRepository,
    ReleaseGatePolicy,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    SqliteConnectorRepository,
)
from ai_multi_agent_platform.context import (
    ContextEntryRole,
    ContextSourceAdapterBinding,
    ContextSourceType,
)
from ai_multi_agent_platform.context.lifecycle import ContextLifecycleSourceRequest
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.kernel import EventSourcedTaskRepository, PlatformKernel
from ai_multi_agent_platform.learning.single_node import (
    SingleNodeLearningComposition,
    build_single_node_learning,
)
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.planning import (
    JsonPlanningRepository,
    PlanningService,
    ReplanningEvidenceBridge,
)
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    AutomationTemplateExporter,
    PlatformTemplateEnvironmentResolver,
    register_template_control_plane,
)
from ai_multi_agent_platform.verification.async_agent_workflow import (
    AsyncAutomaticReviewerWorkflow,
)
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
)

from .composition import (
    build_application_distribution,
    build_connector_foundation,
    build_egress_connectors,
    build_planning,
    build_reviewer,
)
from .config import SingleNodeConfig
from .context_operationalization import (
    SingleNodeContextComposition,
    install_single_node_context,
)
from .egress_bindings import EgressDeploymentBindings
from .handoff_composition import (
    HandoffDeploymentComposition,
    build_single_node_handoff_composition,
)
from .reference_multi_agent import ReferenceIncomingHandoffContextAdapter
from .single_node import SingleNodeDeployment as BaseSingleNodeDeployment
from .single_node import SingleNodeSmokeResult
from .single_node import build_single_node_deployment as _build_base_single_node_deployment


@dataclass(slots=True)
class SingleNodeDeployment(BaseSingleNodeDeployment):
    """Normal single-node deployment with durable public owner-domain state."""

    connector_repository: SqliteConnectorRepository
    connector_registry: ConnectorRegistry
    connectors: ConnectorService
    application_release_repository: JsonApplicationReleaseRepository
    application_build_kernel: PlatformKernel
    application_releases: ApplicationDistributionService
    planning_repository: JsonPlanningRepository
    planning_kernel: PlatformKernel
    planning: PlanningService
    replanning: ReplanningEvidenceBridge
    egress: EgressDeploymentBindings
    context: SingleNodeContextComposition
    learning: SingleNodeLearningComposition
    handoffs: HandoffDeploymentComposition
    automatic_reviewer: AsyncAutomaticReviewerWorkflow
    reviewer_recovery: AutomaticReviewerStartupReconciler


def build_single_node_deployment(
    config: SingleNodeConfig,
    *,
    onboarding_model_adapters: Iterable[OnboardingModelAdapter] = (),
    secret_provider: SecretProvider | None = None,
    accounting_service: AccountingService | None = None,
    observability_exporter: InMemoryExporter | None = None,
    distributed_runtime: DistributedRuntime | None = None,
    enable_distributed_execution: bool = False,
    repository_discovery_resolver: RepositoryDiscoveryResolver | None = None,
    application_release_gate_policy: ReleaseGatePolicy | None = None,
) -> SingleNodeDeployment:
    """Build the normal durable single-node profile through explicit composition builders."""

    config.prepare_directories()
    connector_foundation = build_connector_foundation(config)
    effective_repository_resolver = (
        repository_discovery_resolver or connector_foundation.repository_discovery_resolver
    )
    base = _build_base_single_node_deployment(
        config,
        onboarding_model_adapters=onboarding_model_adapters,
        secret_provider=secret_provider,
        accounting_service=accounting_service,
        observability_exporter=observability_exporter,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=effective_repository_resolver,
    )
    reviewer = build_reviewer(
        kernel=base.kernel,
        kernel_repository=base.kernel_repository,
        files=base.files,
        verification=base.verification,
        completion=base.verification_completion,
        verification_runtime=base.verification_runtime,
        agent_runtime=base.agent_runtime,
        model_runtime=base.model_runtime,
    )
    integrations = build_egress_connectors(
        config,
        connector_foundation,
        authorization=base.authorization,
        approval_gate=base.approval_gate,
        telemetry=base.telemetry,
        model_runtime=base.model_runtime,
        control_plane=base.control_plane,
    )
    distribution = build_application_distribution(
        config,
        connector_foundation,
        integrations,
        kernel_repository=base.kernel_repository,
        files=base.files,
        workspaces=base.workspaces,
        run_workspace_bindings=base.run_workspace_bindings,
        authorization=base.authorization,
        approval_gate=base.approval_gate,
        secrets=base.secrets,
        distributed_runtime=base.distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        verification=base.verification,
        evaluation_repository=base.evaluation_repository,
        evaluation=base.evaluation,
        control_plane=base.control_plane,
        release_gate_policy=application_release_gate_policy,
    )
    planning = build_planning(
        config,
        kernel=base.kernel,
        kernel_repository=base.kernel_repository,
        agents=base.agents.repository,
        capabilities=base.capabilities,
        models=base.models,
        authorization=base.approval_gate,
        coordination=base.coordination,
        coordination_repository=base.coordination_repository,
        verification=base.verification,
        telemetry=base.telemetry,
        control_plane=base.control_plane,
    )
    context = install_single_node_context(base, egress=integrations.egress)
    learning = build_single_node_learning(
        database_dir=config.database_dir,
        agents=base.agents,
        routing_profiles=base.routing_profiles,
        evaluation=base.evaluation,
        verification=base.verification,
        approval_gate=base.approval_gate,
        kernel=base.kernel,
        planning=planning.planning,
        telemetry=base.telemetry,
        skills=context.skills,
        research=context.research,
    )
    learning.register_control_plane(base.control_plane)
    handoffs = build_single_node_handoff_composition(
        database_dir=config.database_dir,
        control_plane=base.control_plane,
        agents=base.agents.repository,
        agent_runtime=base.agent_runtime,
        coordinator=base.coordination_repository,
        tasks=EventSourcedTaskRepository(base.kernel_repository),
        authorization=base.approval_gate.provider,
        verification=base.verification_runtime.evidence,
        telemetry=base.telemetry,
        research_repository=context.research_repository,
        skill_repository=context.skills_repository,
        context_bundle_repository=context.bundles,
        context_binding_repository=context.run_bindings,
        egress_gate=integrations.egress.runtime.gate,
        model_runtime=base.model_runtime,
    )
    _register_handoff_context_source(base, context, handoffs)
    _register_durable_template_environment(
        base,
        connector_foundation.registry,
    )
    return _durable_deployment(
        base,
        connector_foundation=connector_foundation,
        connectors=integrations.connectors,
        distribution=distribution,
        planning=planning,
        egress=integrations.egress,
        context=context,
        learning=learning,
        handoffs=handoffs,
        reviewer=reviewer,
    )


def _register_handoff_context_source(
    base: BaseSingleNodeDeployment,
    context: SingleNodeContextComposition,
    handoffs: HandoffDeploymentComposition,
) -> None:
    incoming_handoffs = ReferenceIncomingHandoffContextAdapter(
        handoffs,
        coordinator=base.coordination_repository,
        kernel=base.kernel,
    )

    def binding_factory(
        source: ContextLifecycleSourceRequest,
    ) -> tuple[ContextSourceAdapterBinding, ...]:
        if source.step_id is None:
            return ()
        return (
            ContextSourceAdapterBinding(
                adapter=incoming_handoffs,
                source_type=ContextSourceType.AGENT_HANDOFF,
                source_id=f"run:{source.run_id}:incoming-handoffs",
                role=ContextEntryRole.CONTEXT,
                project_id=source.project_id,
                workspace_id=source.workspace_id,
            ),
        )

    context.lifecycle.register_source_binding_factory(binding_factory)


def _register_durable_template_environment(
    base: BaseSingleNodeDeployment,
    connector_registry: ConnectorRegistry,
) -> None:
    environment = PlatformTemplateEnvironmentResolver(
        workspaces=base.workspaces,
        capabilities=lambda: (
            capability.capability_id
            for capability in base.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        capability_versions=lambda: (
            (capability.capability_id, capability.version)
            for capability in base.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        connectors=lambda: (definition.id for definition in connector_registry.definitions()),
        model_policies=lambda: (
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision).canonical_ref
            for definition in base.routing_profile_repository.list_definitions()
            if definition.enabled
        ),
        grantable_permissions=lambda context: (
            action.value
            for action in base.authorization.globally_grantable_actions(
                context.actor.principal_ref,
                actor_type=context.actor.actor_type,
            )
        ),
        platform_version=__version__,
    )
    register_template_control_plane(
        base.control_plane,
        base.templates,
        environment_resolver=environment,
        agent_exporter=AgentTemplateExporter(base.agents, base.templates.templates),
        automation_exporter=AutomationTemplateExporter(
            base.control_plane.automation_service,
            base.templates.templates,
        ),
    )


def _durable_deployment(
    base: BaseSingleNodeDeployment,
    *,
    connector_foundation,
    connectors: ConnectorService,
    distribution,
    planning,
    egress: EgressDeploymentBindings,
    context: SingleNodeContextComposition,
    learning: SingleNodeLearningComposition,
    handoffs: HandoffDeploymentComposition,
    reviewer,
) -> SingleNodeDeployment:
    base_values: dict[str, Any] = {
        field.name: getattr(base, field.name) for field in fields(BaseSingleNodeDeployment)
    }
    return SingleNodeDeployment(
        **base_values,
        connector_repository=connector_foundation.repository,
        connector_registry=connector_foundation.registry,
        connectors=connectors,
        application_release_repository=distribution.repository,
        application_build_kernel=distribution.build_kernel,
        application_releases=distribution.service,
        planning_repository=planning.repository,
        planning_kernel=planning.kernel,
        planning=planning.planning,
        replanning=planning.replanning,
        egress=egress,
        context=context,
        learning=learning,
        handoffs=handoffs,
        automatic_reviewer=reviewer.workflow,
        reviewer_recovery=reviewer.recovery,
    )


__all__ = [
    "SingleNodeDeployment",
    "SingleNodeSmokeResult",
    "build_single_node_deployment",
]
