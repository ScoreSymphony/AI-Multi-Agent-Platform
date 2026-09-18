"""Durable Connector composition for the normal single-node/server runtime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

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
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.learning.single_node import SingleNodeLearningComposition
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.planning import (
    JsonPlanningRepository,
    PlanningService,
    ReplanningEvidenceBridge,
)
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.verification.async_agent_workflow import (
    AsyncAutomaticReviewerWorkflow,
)
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
)

from .composition.durable import (
    ApplicationDistributionBundle,
    AutomaticReviewBundle,
    ConnectorFoundationBundle,
    EgressConnectorBundle,
    PlanningBundle,
    build_application_distribution,
    build_automatic_review,
    build_connector_foundation,
    build_connector_services,
    build_egress_foundation,
    build_planning,
)
from .composition.durable_extensions import (
    DurableExtensionBundle,
    build_durable_extensions,
    register_durable_template_environment,
)
from .composition.foundation import build_single_node_foundation
from .config import SingleNodeConfig
from .context_operationalization import SingleNodeContextComposition
from .egress_bindings import EgressDeploymentBindings
from .handoff_composition import HandoffDeploymentComposition
from .single_node import SingleNodeDeployment as BaseSingleNodeDeployment
from .single_node import SingleNodeSmokeResult, build_single_node_deployment_from_foundation
from .startup_recovery import StartupRecoveryExtension


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
    startup_recovery_extensions: tuple[StartupRecoveryExtension, ...]


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
    """Build the durable profile with egress established before ModelRuntime."""

    foundation = build_single_node_foundation(
        config,
        secret_provider=secret_provider,
        observability_exporter=observability_exporter,
    )
    connector_foundation = build_connector_foundation(
        config,
        repository_discovery_resolver=repository_discovery_resolver,
    )
    egress = build_egress_foundation(
        config,
        foundation.security,
        foundation.observability,
    )
    base = build_single_node_deployment_from_foundation(
        config,
        foundation,
        onboarding_model_adapters=onboarding_model_adapters,
        accounting_service=accounting_service,
        distributed_runtime=distributed_runtime,
        enable_distributed_execution=enable_distributed_execution,
        repository_discovery_resolver=connector_foundation.repository_discovery_resolver,
        model_runtime_factory=egress.model_runtime,
    )
    automatic_review = build_automatic_review(base)
    egress_connectors = build_connector_services(base, connector_foundation, egress)
    application = build_application_distribution(
        config,
        base,
        connector_foundation,
        egress_connectors,
        enable_distributed_execution=enable_distributed_execution,
        release_gate_policy=application_release_gate_policy,
    )
    planning = build_planning(config, base)
    extensions = build_durable_extensions(
        base,
        egress=egress,
        planning=planning,
    )
    register_durable_template_environment(base, connector_foundation.registry)
    return _extend_base_deployment(
        base,
        connector_foundation=connector_foundation,
        automatic_review=automatic_review,
        egress_connectors=egress_connectors,
        application=application,
        planning=planning,
        extensions=extensions,
    )


def _extend_base_deployment(
    base: BaseSingleNodeDeployment,
    *,
    connector_foundation: ConnectorFoundationBundle,
    automatic_review: AutomaticReviewBundle,
    egress_connectors: EgressConnectorBundle,
    application: ApplicationDistributionBundle,
    planning: PlanningBundle,
    extensions: DurableExtensionBundle,
) -> SingleNodeDeployment:
    """Promote the base deployment to the durable public profile explicitly."""

    return SingleNodeDeployment(
        config=base.config,
        kernel_repository=base.kernel_repository,
        scopes=base.scopes,
        files=base.files,
        workspaces=base.workspaces,
        run_workspace_bindings=base.run_workspace_bindings,
        repository_registry=base.repository_registry,
        repository_catalog=base.repository_catalog,
        repository_provenance=base.repository_provenance,
        repositories=base.repositories,
        repository_management=base.repository_management,
        repository_run_integration=base.repository_run_integration,
        repository_workspace_execution=base.repository_workspace_execution,
        repository_event_ingress=base.repository_event_ingress,
        agents=base.agents,
        conversations=base.conversations,
        agent_runtime=base.agent_runtime,
        capabilities=base.capabilities,
        capability_assignments=base.capability_assignments,
        models=base.models,
        routing_profile_repository=base.routing_profile_repository,
        routing_profiles=base.routing_profiles,
        model_runtime=base.model_runtime,
        onboarding=base.onboarding,
        first_task=base.first_task,
        secrets=base.secrets,
        templates=base.templates,
        workflows=base.workflows,
        coordination_repository=base.coordination_repository,
        coordination=base.coordination,
        research=base.research,
        evaluation_repository=base.evaluation_repository,
        evaluation=base.evaluation,
        accounting_service=base.accounting_service,
        observability_exporter=base.observability_exporter,
        telemetry=base.telemetry,
        health_provider=base.health_provider,
        distributed_runtime=base.distributed_runtime,
        pre_authorization_lifecycle=base.pre_authorization_lifecycle,
        lifecycle_binding=base.lifecycle_binding,
        authentication=base.authentication,
        authorization=base.authorization,
        authorization_audit=base.authorization_audit,
        approval_gate=base.approval_gate,
        verification=base.verification,
        verification_completion=base.verification_completion,
        verification_runtime=base.verification_runtime,
        kernel=base.kernel,
        control_plane=base.control_plane,
        http=base.http,
        app=base.app,
        connector_repository=connector_foundation.repository,
        connector_registry=connector_foundation.registry,
        connectors=egress_connectors.connectors,
        application_release_repository=application.repository,
        application_build_kernel=application.kernel,
        application_releases=application.service,
        planning_repository=planning.repository,
        planning_kernel=planning.kernel,
        planning=planning.service,
        replanning=planning.replanning,
        egress=egress_connectors.egress,
        context=extensions.context,
        learning=extensions.learning,
        handoffs=extensions.handoffs,
        automatic_reviewer=automatic_review.workflow,
        reviewer_recovery=automatic_review.recovery,
        startup_recovery_extensions=extensions.context.startup_recovery_extensions,
    )


__all__ = [
    "SingleNodeDeployment",
    "SingleNodeSmokeResult",
    "build_single_node_deployment",
]
