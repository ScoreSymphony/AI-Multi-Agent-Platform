"""Durable Connector composition for the normal single-node/server runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.application_distribution import (
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationDistributionService,
    GitHubReleasePublisher,
    JsonApplicationReleaseRepository,
    LocalBuildTargetMatcher,
)
from ai_multi_agent_platform.application_distribution.control_plane import (
    register_application_distribution_control_plane,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    DurableGitHubReleaseConnectorProvider,
    SqliteConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distributed import DistributedRuntime
from ai_multi_agent_platform.kernel import (
    EventSourcedRunRepository,
    EventSourcedTaskRepository,
    PlatformKernel,
)
from ai_multi_agent_platform.learning.single_node import (
    SingleNodeLearningComposition,
    build_single_node_learning,
)
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.observability import (
    EgressTelemetryAuditSink,
    FailureComponent,
    InMemoryExporter,
    Telemetry,
    TelemetryContext,
)
from ai_multi_agent_platform.onboarding import OnboardingModelAdapter
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.planning import (
    DeterministicReferencePlanner,
    JsonPlanningRepository,
    PlanningOrchestratorAdapter,
    PlanningService,
    PolicyAwarePlanningEnvironmentResolver,
    planning_command_handlers,
    planning_resource_services,
)
from ai_multi_agent_platform.planning.composition import (
    PlanningBindingCoordinator,
    PlanningOnlyLifecycleBackend,
    ReferencePlanningService,
)
from ai_multi_agent_platform.repositories import RepositoryDiscoveryResolver
from ai_multi_agent_platform.repositories.connector_bootstrap import (
    connector_repository_discovery_resolver,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizedLifecycleBackend,
    LocalPrincipalPolicy,
    ResourceType,
    build_durable_egress_runtime,
)
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    AutomationTemplateExporter,
    PlatformTemplateEnvironmentResolver,
    register_template_control_plane,
)
from ai_multi_agent_platform.verification.agent_repair import (
    KernelAgentRepairExecutor,
    ProducerAgentRepairBindingProvider,
)
from ai_multi_agent_platform.verification.agent_workflow import AutomaticReviewerWorkflow
from ai_multi_agent_platform.verification.gate import VerificationCompletionAuthority
from ai_multi_agent_platform.verification.output_workflow import (
    AutomaticReviewerOutputCoordinator,
    PolicyMetadataReviewerResolver,
    install_automatic_reviewer_output_observer,
)
from ai_multi_agent_platform.verification.reference_reviewer import ModelRuntimeReviewerExecutor
from ai_multi_agent_platform.verification.repair import VerificationRepairRuntime
from ai_multi_agent_platform.verification.reviewer_input import (
    KernelFileReviewerSubjectInputProvider,
)
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
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
from .single_node import (
    SingleNodeDeployment as BaseSingleNodeDeployment,
)
from .single_node import (
    SingleNodeSmokeResult,
)
from .single_node import (
    build_single_node_deployment as _build_base_single_node_deployment,
)

_APPLICATION_BUILD_PRINCIPAL = "service:application-distribution"
_APPLICATION_BUILD_SECRET_PRINCIPAL = "service:application-build-secrets"
_GITHUB_RELEASE_CONNECTOR_PRINCIPAL = "connector.github-releases"


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
    egress: EgressDeploymentBindings
    context: SingleNodeContextComposition
    learning: SingleNodeLearningComposition
    handoffs: HandoffDeploymentComposition
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
) -> SingleNodeDeployment:
    """Build the normal durable single-node profile.

    The lower-level ``deployment.single_node`` composition remains usable by focused tests and
    explicitly minimal/ephemeral profiles. Public deployment/server composition comes through this
    wrapper so Connector Definitions, Connections, application releases, planning proposals,
    canonical Context Bundle/Run-binding evidence, one durable egress policy runtime, governed
    Learning, automatic reviewer workflows and Agent Handoffs survive process restarts without
    requiring hosted services.
    """

    config.prepare_directories()
    connector_repository = SqliteConnectorRepository(config.database_dir / "connectors.sqlite3")
    connector_registry = ConnectorRegistry()
    effective_repository_resolver = repository_discovery_resolver or (
        connector_repository_discovery_resolver(connector_repository, connector_registry)
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

    # Compose #711 on the normal durable kernel. Ordinary attach_result/attach_artifact calls stay
    # the only producer API; automatic review remains explicit opt-in in versioned Verification
    # policy metadata. A needs_changes result routes one bounded canonical repair Step back through
    # the exact producer Agent before a fresh exact-subject review.
    completion = base.kernel._completion_authority  # noqa: SLF001
    if not isinstance(completion, VerificationCompletionAuthority):
        raise RuntimeError("normal single-node kernel is missing Verification completion authority")
    reviewer_inputs = KernelFileReviewerSubjectInputProvider(
        tasks=EventSourcedTaskRepository(base.kernel_repository),
        runs=EventSourcedRunRepository(base.kernel_repository),
        files=base.files,
    )
    repair_runtime = VerificationRepairRuntime(
        base.verification,
        completion,
        base.kernel,
        binding_provider=ProducerAgentRepairBindingProvider(),
    )
    automatic_reviewer = AutomaticReviewerWorkflow(
        runtime=base.verification_runtime,
        completion=completion,
        agents=base.agent_runtime,
        resolver=PolicyMetadataReviewerResolver(completion),
        executor=ModelRuntimeReviewerExecutor(
            agents=base.agent_runtime,
            models=base.model_runtime,
            inputs=reviewer_inputs,
        ),
        repair_runtime=repair_runtime,
        repair_executor=KernelAgentRepairExecutor(base.kernel),
    )
    reviewer_recovery = AutomaticReviewerStartupReconciler(
        workflow=automatic_reviewer,
        agents=base.agent_runtime,
        verification=base.verification,
    )
    automatic_review_output = AutomaticReviewerOutputCoordinator(
        kernel=base.kernel,
        runtime=base.verification_runtime,
        completion=completion,
        reviewer=automatic_reviewer,
    )
    install_automatic_reviewer_output_observer(base.kernel, automatic_review_output)

    egress_runtime = build_durable_egress_runtime(
        config.database_dir / "egress-profiles.json",
        authorization=base.authorization,
        approval_gate=base.approval_gate,
        audit_sink=EgressTelemetryAuditSink(base.telemetry),
    )
    egress = EgressDeploymentBindings(egress_runtime)
    base.model_runtime.egress_gate = egress.runtime.gate

    connectors = egress.connector_service(
        connector_repository,
        connector_registry,
        authorization_gate=base.approval_gate,
    )
    register_connector_control_plane(base.control_plane, connectors)
    egress.register_control_plane(base.control_plane)

    if not base.authorization.has_policy(_APPLICATION_BUILD_PRINCIPAL):
        base.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_APPLICATION_BUILD_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.EXECUTE,
                        AuthorizationAction.READ,
                        AuthorizationAction.MODIFY,
                    }
                ),
                resource_types=frozenset({ResourceType.RUN}),
            )
        )
    if base.secrets is not None and not base.authorization.has_policy(
        _APPLICATION_BUILD_SECRET_PRINCIPAL
    ):
        base.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_APPLICATION_BUILD_SECRET_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            )
        )

    application_release_repository = JsonApplicationReleaseRepository(
        config.database_dir / "application-releases.json"
    )
    application_build_lifecycle = AuthorizedLifecycleBackend(
        ApplicationBuildLifecycleBackend(
            application_release_repository,
            base.workspaces,
            base.files,
            base.run_workspace_bindings,
            ApplicationCommandExecutor(base.workspaces.materialization_root),
            secret_provider=base.secrets,
            secret_consumer_ref=_APPLICATION_BUILD_SECRET_PRINCIPAL,
        ),
        base.approval_gate,
        allow_internal_service_reads=True,
    )
    application_build_kernel = PlatformKernel(
        orchestrator=ReferenceOrchestrator(),
        lifecycle=application_build_lifecycle,
        repository=base.kernel_repository,
    )
    application_releases = ApplicationDistributionService(
        application_release_repository,
        kernel=application_build_kernel,
        files=base.files,
        workspaces=base.workspaces,
        run_workspace_bindings=base.run_workspace_bindings,
        authorization_gate=base.approval_gate,
        target_matcher=LocalBuildTargetMatcher(),
    )
    if base.secrets is not None:
        if not base.authorization.has_policy(_GITHUB_RELEASE_CONNECTOR_PRINCIPAL):
            base.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=_GITHUB_RELEASE_CONNECTOR_PRINCIPAL,
                    actor_types=frozenset({ActorType.SERVICE}),
                    allowed_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
                    resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
                )
            )
        github_releases = DurableGitHubReleaseConnectorProvider(
            base.secrets,
            base.files,
            connector_repository,
        )
        asyncio.run(connectors.register_provider(github_releases))
        application_releases.register_publisher(GitHubReleasePublisher(connectors))
    register_application_distribution_control_plane(
        base.control_plane,
        application_releases,
    )

    planning_repository = JsonPlanningRepository(config.database_dir / "planning.json")
    planning_kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(planning_repository),
        lifecycle=PlanningOnlyLifecycleBackend(),
        repository=base.kernel_repository,
    )
    planning_coordinator = PlanningBindingCoordinator(
        repository=planning_repository,
        kernel=base.kernel,
        delegate=base.coordination,
    )
    planning_environment = PolicyAwarePlanningEnvironmentResolver(
        agents=base.agents.repository,
        capabilities=base.capabilities,
        authorization=base.approval_gate,
    )
    planning = ReferencePlanningService(
        planner=DeterministicReferencePlanner(),
        repository=planning_repository,
        kernel=planning_kernel,
        agents=base.agents.repository,
        capabilities=base.capabilities,
        models=base.models,
        authorization=base.approval_gate,
        coordinator=planning_coordinator,
        event_sink=_planning_event_sink(base.telemetry),
        environment_resolver=planning_environment,
    )
    for collection, service in planning_resource_services(planning).items():
        base.control_plane.register_resource_service(collection, service)
    for command, handler in planning_command_handlers(planning).items():
        base.control_plane.register_command(command, handler)

    context = install_single_node_context(base, egress=egress)

    learning = build_single_node_learning(
        database_dir=config.database_dir,
        agents=base.agents,
        routing_profiles=base.routing_profiles,
        evaluation=base.evaluation,
        verification=base.verification,
        approval_gate=base.approval_gate,
        kernel=base.kernel,
        planning=planning,
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
        egress_gate=egress.runtime.gate,
        model_runtime=base.model_runtime,
    )

    template_environment = PlatformTemplateEnvironmentResolver(
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
        environment_resolver=template_environment,
        agent_exporter=AgentTemplateExporter(base.agents, base.templates.templates),
        automation_exporter=AutomationTemplateExporter(
            base.control_plane.automation_service,
            base.templates.templates,
        ),
    )

    base_values: dict[str, Any] = {
        field.name: getattr(base, field.name) for field in fields(BaseSingleNodeDeployment)
    }
    return SingleNodeDeployment(
        **base_values,
        connector_repository=connector_repository,
        connector_registry=connector_registry,
        connectors=connectors,
        application_release_repository=application_release_repository,
        application_build_kernel=application_build_kernel,
        application_releases=application_releases,
        planning_repository=planning_repository,
        planning_kernel=planning_kernel,
        planning=planning,
        egress=egress,
        context=context,
        learning=learning,
        handoffs=handoffs,
        reviewer_recovery=reviewer_recovery,
    )


def _planning_event_sink(
    telemetry: Telemetry,
) -> Callable[[str, dict[str, JsonValue]], None]:
    """Project safe planning transition evidence into the canonical observability timeline."""

    def emit(event_type: str, attributes: dict[str, JsonValue]) -> None:
        raw_task_id = attributes.get("task_id")
        task_id = raw_task_id if isinstance(raw_task_id, str) else None
        telemetry.timeline(
            event_name=event_type,
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(
                task_id=task_id,
                correlation_id=task_id,
            ),
            attributes=attributes,
        )

    return emit


__all__ = [
    "SingleNodeDeployment",
    "SingleNodeSmokeResult",
    "build_single_node_deployment",
]
