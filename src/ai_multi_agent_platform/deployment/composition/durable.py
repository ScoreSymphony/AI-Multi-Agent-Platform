"""Typed builders for durable single-node integration layers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ai_multi_agent_platform.application_distribution import (
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationDistributionService,
    ApplicationReleaseGateCoordinator,
    DistributedApplicationBuildLifecycleBackend,
    DistributedBuildTargetMatcher,
    GitHubReleasePublisher,
    JsonApplicationReleaseRepository,
    LocalBuildTargetMatcher,
    ReleaseGatePolicy,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.application_distribution.control_plane import (
    register_application_distribution_control_plane,
)
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    DurableGitHubReleaseConnectorProvider,
    SqliteConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import (
    EventSourcedRunRepository,
    EventSourcedTaskRepository,
    PlatformKernel,
)
from ai_multi_agent_platform.observability import (
    EgressTelemetryAuditSink,
    FailureComponent,
    Telemetry,
    TelemetryContext,
)
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.planning import (
    JsonPlanningRepository,
    PlanningOrchestratorAdapter,
    PlanningService,
    PolicyAwarePlanningEnvironmentResolver,
    ReplanningEvidenceBridge,
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
from ai_multi_agent_platform.verification import CanonicalVerificationAccess
from ai_multi_agent_platform.verification.agent_repair import (
    KernelAgentRepairExecutor,
    ProducerAgentRepairBindingProvider,
)
from ai_multi_agent_platform.verification.async_agent_workflow import (
    AsyncAutomaticReviewerWorkflow,
)
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

from ..config import SingleNodeConfig
from ..egress_bindings import EgressDeploymentBindings
from ..reference_multi_agent import ReferenceMultiAgentPlanner
from .foundation import ObservabilityBundle, SecurityBundle

if TYPE_CHECKING:
    from ..single_node import SingleNodeDeployment as BaseSingleNodeDeployment

_APPLICATION_BUILD_PRINCIPAL = "service:application-distribution"
_APPLICATION_BUILD_SECRET_PRINCIPAL = "service:application-build-secrets"
_GITHUB_RELEASE_CONNECTOR_PRINCIPAL = "connector.github-releases"


@dataclass(frozen=True, slots=True)
class ConnectorFoundationBundle:
    """Durable connector persistence, registry and repository discovery seam."""

    repository: SqliteConnectorRepository
    registry: ConnectorRegistry
    repository_discovery_resolver: RepositoryDiscoveryResolver


@dataclass(frozen=True, slots=True)
class AutomaticReviewBundle:
    """Automatic reviewer workflow and restart reconciler."""

    workflow: AsyncAutomaticReviewerWorkflow
    recovery: AutomaticReviewerStartupReconciler


@dataclass(frozen=True, slots=True)
class EgressConnectorBundle:
    """One durable egress runtime plus the connector service bound to it."""

    egress: EgressDeploymentBindings
    connectors: ConnectorService


@dataclass(frozen=True, slots=True)
class ApplicationDistributionBundle:
    """Application release persistence, build kernel and service."""

    repository: JsonApplicationReleaseRepository
    kernel: PlatformKernel
    service: ApplicationDistributionService


@dataclass(frozen=True, slots=True)
class PlanningBundle:
    """Durable planning repository, proposal kernel and service."""

    repository: JsonPlanningRepository
    kernel: PlatformKernel
    service: PlanningService
    replanning: ReplanningEvidenceBridge


def build_connector_foundation(
    config: SingleNodeConfig,
    *,
    repository_discovery_resolver: RepositoryDiscoveryResolver | None = None,
) -> ConnectorFoundationBundle:
    """Build connector persistence before the base repository layer needs discovery."""

    repository = SqliteConnectorRepository(config.database_dir / "connectors.sqlite3")
    registry = ConnectorRegistry()
    resolver = repository_discovery_resolver or connector_repository_discovery_resolver(
        repository,
        registry,
    )
    return ConnectorFoundationBundle(
        repository=repository,
        registry=registry,
        repository_discovery_resolver=resolver,
    )


def build_egress_foundation(
    config: SingleNodeConfig,
    security: SecurityBundle,
    observability: ObservabilityBundle,
) -> EgressDeploymentBindings:
    """Build the shared durable EgressGate before ModelRuntime construction."""

    runtime = build_durable_egress_runtime(
        config.database_dir / "egress-profiles.json",
        authorization=security.authorization,
        approval_gate=security.approval_gate,
        audit_sink=EgressTelemetryAuditSink(observability.telemetry),
    )
    return EgressDeploymentBindings(runtime)


def build_connector_services(
    base: BaseSingleNodeDeployment,
    connector_foundation: ConnectorFoundationBundle,
    egress: EgressDeploymentBindings,
) -> EgressConnectorBundle:
    """Bind connector invocation and Control Plane surfaces to the shared EgressGate."""

    connectors = egress.connector_service(
        connector_foundation.repository,
        connector_foundation.registry,
        authorization_gate=base.approval_gate,
    )
    register_connector_control_plane(base.control_plane, connectors)
    egress.register_control_plane(base.control_plane)
    return EgressConnectorBundle(egress=egress, connectors=connectors)


def build_automatic_review(base: BaseSingleNodeDeployment) -> AutomaticReviewBundle:
    """Bind automatic verification review through public deployment authorities."""

    completion = base.verification_completion
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
    workflow = AsyncAutomaticReviewerWorkflow(
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
    recovery = AutomaticReviewerStartupReconciler(
        workflow=workflow,
        agents=base.agent_runtime,
        verification=base.verification,
        tasks=base.kernel,
    )
    output = AutomaticReviewerOutputCoordinator(
        kernel=base.kernel,
        runtime=base.verification_runtime,
        completion=completion,
        reviewer=workflow,
    )
    install_automatic_reviewer_output_observer(base.kernel, output)
    return AutomaticReviewBundle(workflow=workflow, recovery=recovery)


def _ensure_application_policies(base: BaseSingleNodeDeployment) -> None:
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


def _application_build_components(
    base: BaseSingleNodeDeployment,
    repository: JsonApplicationReleaseRepository,
    *,
    enable_distributed_execution: bool,
) -> tuple[
    DistributedApplicationBuildLifecycleBackend | ApplicationBuildLifecycleBackend,
    DistributedBuildTargetMatcher | LocalBuildTargetMatcher,
]:
    if enable_distributed_execution and base.distributed_runtime is not None:
        return (
            DistributedApplicationBuildLifecycleBackend(
                repository,
                base.files,
                base.run_workspace_bindings,
                base.distributed_runtime,
            ),
            DistributedBuildTargetMatcher(
                base.distributed_runtime.registry,
                scheduler=base.distributed_runtime.scheduler,
            ),
        )
    return (
        ApplicationBuildLifecycleBackend(
            repository,
            base.workspaces,
            base.files,
            base.run_workspace_bindings,
            ApplicationCommandExecutor(base.workspaces.materialization_root),
            secret_provider=base.secrets,
            secret_consumer_ref=_APPLICATION_BUILD_SECRET_PRINCIPAL,
        ),
        LocalBuildTargetMatcher(),
    )


def _register_github_release_publisher(
    base: BaseSingleNodeDeployment,
    connector_foundation: ConnectorFoundationBundle,
    connectors: ConnectorService,
    service: ApplicationDistributionService,
) -> None:
    if base.secrets is None:
        return
    if not base.authorization.has_policy(_GITHUB_RELEASE_CONNECTOR_PRINCIPAL):
        base.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=_GITHUB_RELEASE_CONNECTOR_PRINCIPAL,
                actor_types=frozenset({ActorType.SERVICE}),
                allowed_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            )
        )
    provider = DurableGitHubReleaseConnectorProvider(
        base.secrets,
        base.files,
        connector_foundation.repository,
    )
    asyncio.run(connectors.register_provider(provider))
    service.register_publisher(GitHubReleasePublisher(connectors))


def _release_gate_coordinator(
    base: BaseSingleNodeDeployment,
    policy: ReleaseGatePolicy | None,
) -> ApplicationReleaseGateCoordinator:
    return ApplicationReleaseGateCoordinator(
        policy=policy if policy is not None else StaticReleaseGatePolicy(),
        files=base.files,
        verification_access=CanonicalVerificationAccess(base.verification),
        evaluations=base.evaluation_repository,
        evaluation_service=base.evaluation,
    )


def build_application_distribution(
    config: SingleNodeConfig,
    base: BaseSingleNodeDeployment,
    connector_foundation: ConnectorFoundationBundle,
    egress_connectors: EgressConnectorBundle,
    *,
    enable_distributed_execution: bool,
    release_gate_policy: ReleaseGatePolicy | None = None,
) -> ApplicationDistributionBundle:
    """Build application release/build services and register their Control Plane surface."""

    _ensure_application_policies(base)
    repository = JsonApplicationReleaseRepository(config.database_dir / "application-releases.json")
    backend, target_matcher = _application_build_components(
        base,
        repository,
        enable_distributed_execution=enable_distributed_execution,
    )
    lifecycle = AuthorizedLifecycleBackend(
        backend,
        base.approval_gate,
        allow_internal_service_reads=True,
    )
    kernel = PlatformKernel(
        orchestrator=ReferenceOrchestrator(),
        lifecycle=lifecycle,
        repository=base.kernel_repository,
    )
    service = ApplicationDistributionService(
        repository,
        kernel=kernel,
        files=base.files,
        workspaces=base.workspaces,
        run_workspace_bindings=base.run_workspace_bindings,
        authorization_gate=base.approval_gate,
        target_matcher=target_matcher,
        gate_coordinator=_release_gate_coordinator(base, release_gate_policy),
    )
    _register_github_release_publisher(
        base,
        connector_foundation,
        egress_connectors.connectors,
        service,
    )
    register_application_distribution_control_plane(base.control_plane, service)
    return ApplicationDistributionBundle(repository=repository, kernel=kernel, service=service)


def build_planning(
    config: SingleNodeConfig,
    base: BaseSingleNodeDeployment,
) -> PlanningBundle:
    """Build durable planning, coordination binding and replanning evidence projection."""

    repository = JsonPlanningRepository(config.database_dir / "planning.json")
    kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(repository),
        lifecycle=PlanningOnlyLifecycleBackend(),
        repository=base.kernel_repository,
    )
    coordinator = PlanningBindingCoordinator(
        repository=repository,
        kernel=base.kernel,
        delegate=base.coordination,
    )
    environment = PolicyAwarePlanningEnvironmentResolver(
        agents=base.agents.repository,
        capabilities=base.capabilities,
        authorization=base.approval_gate,
    )
    service = ReferencePlanningService(
        planner=ReferenceMultiAgentPlanner(),
        repository=repository,
        kernel=kernel,
        agents=base.agents.repository,
        capabilities=base.capabilities,
        models=base.models,
        authorization=base.approval_gate,
        coordinator=coordinator,
        event_sink=_planning_event_sink(base.telemetry),
        environment_resolver=environment,
    )
    replanning = ReplanningEvidenceBridge(
        service,
        coordination_repository=base.coordination_repository,
        verification_repository=base.verification,
        event_sink=_planning_event_sink(base.telemetry),
    )
    for collection, resource_service in planning_resource_services(service).items():
        base.control_plane.register_resource_service(collection, resource_service)
    for command, handler in planning_command_handlers(service).items():
        base.control_plane.register_command(command, handler)
    return PlanningBundle(
        repository=repository,
        kernel=kernel,
        service=service,
        replanning=replanning,
    )


def _planning_event_sink(
    telemetry: Telemetry,
) -> Callable[[str, dict[str, JsonValue]], None]:
    """Project safe planning transition evidence into the canonical timeline."""

    def emit(event_type: str, attributes: dict[str, JsonValue]) -> None:
        raw_task_id = attributes.get("task_id")
        task_id = raw_task_id if isinstance(raw_task_id, str) else None
        telemetry.timeline(
            event_name=event_type,
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(task_id=task_id, correlation_id=task_id),
            attributes=attributes,
        )

    return emit


__all__ = [
    "ApplicationDistributionBundle",
    "AutomaticReviewBundle",
    "ConnectorFoundationBundle",
    "EgressConnectorBundle",
    "PlanningBundle",
    "build_application_distribution",
    "build_automatic_review",
    "build_connector_foundation",
    "build_connector_services",
    "build_egress_foundation",
    "build_planning",
]
