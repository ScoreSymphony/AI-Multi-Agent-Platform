"""Health, Control Plane and HTTP builders for single-node composition."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.agents import register_standard_agent_control_plane
from ai_multi_agent_platform.agents.routing_profile_control_plane import (
    register_routing_profile_aware_agent_control_plane,
)
from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    evaluation_command_handlers,
    evaluation_resource_services,
)
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.control_plane.first_run import BrowserFirstRunControlPlaneHTTP
from ai_multi_agent_platform.coordination import (
    coordination_command_handlers,
    coordination_resource_services,
)
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.observability import (
    AggregatedHealthProvider,
    ProviderHealthDependency,
)
from ai_multi_agent_platform.observability.composite import CompositeTimelineReader
from ai_multi_agent_platform.onboarding import register_onboarding_control_plane
from ai_multi_agent_platform.portability.composition import build_agent_portability_workflow
from ai_multi_agent_platform.repositories import RepositoryWorkspaceSourceResolver
from ai_multi_agent_platform.repositories.control_plane import register_repository_control_plane
from ai_multi_agent_platform.research import register_searchable_research_control_plane
from ai_multi_agent_platform.templates import (
    AutomationTemplateExporter,
    PlatformTemplateEnvironmentResolver,
    register_automation_template_handler,
    register_template_control_plane,
)
from ai_multi_agent_platform.templates.agent_team_control_plane import (
    register_agent_team_template_control_plane,
)
from ai_multi_agent_platform.templates.compensation import register_template_compensators
from ai_multi_agent_platform.templates.project_control_plane import (
    register_project_template_control_plane,
)
from ai_multi_agent_platform.templates.workspace_structure_control_plane import (
    register_workspace_structure_template_control_plane,
)
from ai_multi_agent_platform.verification.control_plane import register_verification_control_plane
from ai_multi_agent_platform.verification.observability import VerificationTimelineReader

from ..config import SingleNodeConfig
from .execution import EvaluationBundle, ExecutionBundle, KernelBundle, VerificationBundle
from .foundation import ObservabilityBundle, SecurityBundle, StorageBundle
from .repositories import RepositoryFoundationBundle, RepositoryRuntimeBundle
from .services import PlatformServicesBundle, RuntimeServicesBundle


@dataclass(frozen=True, slots=True)
class HealthBundle:
    """Health/readiness authority over the supported single-node providers."""

    provider: AggregatedHealthProvider


@dataclass(frozen=True, slots=True)
class ControlPlaneBundle:
    """Canonical Control Plane assembled after all base domain services exist."""

    control_plane: ControlPlane


@dataclass(frozen=True, slots=True)
class HttpBundle:
    """Authenticated northbound HTTP/ASGI façade."""

    http: AuthenticatedControlPlaneHTTP
    app: ControlPlaneASGI


def build_health(
    storage: StorageBundle,
    execution: ExecutionBundle,
) -> HealthBundle:
    """Build required single-node health dependencies explicitly."""

    return HealthBundle(
        provider=AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    execution.orchestrator,
                    required=True,
                    name="orchestrator",
                ),
                ProviderHealthDependency(
                    execution.lifecycle,
                    required=True,
                    name="lifecycle",
                ),
                ProviderHealthDependency(storage.files, required=True, name="files"),
            )
        )
    )


def _register_base_domains(
    control_plane: ControlPlane,
    storage: StorageBundle,
    runtime: RuntimeServicesBundle,
    platform_services: PlatformServicesBundle,
    repositories: RepositoryFoundationBundle,
    repository_runtime: RepositoryRuntimeBundle,
    kernel: KernelBundle,
    evaluation: EvaluationBundle,
) -> None:
    resolvers = control_plane.workspace_source_resolvers
    if resolvers is None:
        raise RuntimeError(
            "single-node Control Plane did not initialize Workspace source resolvers"
        )
    resolvers.register(
        RepositoryWorkspaceSourceResolver(storage.repository_registry, storage.files)
    )
    control_plane.configure_repository_run_integration(
        repository_runtime.repository_run_integration
    )
    register_repository_control_plane(
        control_plane,
        repositories.repositories,
        management=repositories.repository_management,
    )
    register_searchable_research_control_plane(control_plane, platform_services.research)
    for collection, service in evaluation_resource_services(evaluation.composition.service).items():
        control_plane.register_resource_service(collection, service)
    for command, handler in evaluation_command_handlers(evaluation.composition.service).items():
        control_plane.register_command(command, handler)
    for collection, service in coordination_resource_services(kernel.coordination).items():
        control_plane.register_resource_service(collection, service)
    for command, handler in coordination_command_handlers(kernel.coordination).items():
        control_plane.register_command(command, handler)
    register_routing_profile_aware_agent_control_plane(
        control_plane,
        runtime.agents,
        runtime.routing_profile_assignment_gate,
        runtime=runtime.agent_runtime,
    )
    register_standard_agent_control_plane(control_plane, runtime.agents)
    register_onboarding_control_plane(
        control_plane,
        runtime.onboarding,
        first_task=kernel.first_task,
    )


def _register_templates(
    control_plane: ControlPlane,
    storage: StorageBundle,
    security: SecurityBundle,
    runtime: RuntimeServicesBundle,
    platform_services: PlatformServicesBundle,
) -> None:
    register_automation_template_handler(
        platform_services.template_handlers,
        control_plane.automation_service,
    )
    register_template_compensators(
        platform_services.template_handlers,
        automations=control_plane.automation_service,
    )
    automation_template_exporter = AutomationTemplateExporter(
        control_plane.automation_service,
        platform_services.templates.templates,
    )
    template_environment = PlatformTemplateEnvironmentResolver(
        workspaces=storage.workspaces,
        capabilities=lambda: (
            capability.capability_id
            for capability in runtime.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        capability_versions=lambda: (
            (capability.capability_id, capability.version)
            for capability in runtime.capabilities.inventory_capabilities(include_unavailable=False)
        ),
        model_policies=lambda: (
            ModelRoutingProfileRef(
                definition.profile_id,
                definition.current_revision,
            ).canonical_ref
            for definition in runtime.routing_profile_repository.list_definitions()
            if definition.enabled
        ),
        grantable_permissions=lambda context: (
            action.value
            for action in security.authorization.globally_grantable_actions(
                context.actor.principal_ref,
                actor_type=context.actor.actor_type,
            )
        ),
        platform_version=__version__,
    )
    register_template_control_plane(
        control_plane,
        platform_services.templates,
        environment_resolver=template_environment,
        agent_exporter=platform_services.agent_template_exporter,
        automation_exporter=automation_template_exporter,
    )
    register_agent_team_template_control_plane(
        control_plane,
        platform_services.templates.repository,
        platform_services.agent_team_template_exporter,
    )
    register_project_template_control_plane(
        control_plane,
        platform_services.templates.repository,
        platform_services.project_template_exporter,
    )
    register_workspace_structure_template_control_plane(
        control_plane,
        platform_services.templates.repository,
        platform_services.workspace_template_exporter,
    )


def _register_verification_observability(
    control_plane: ControlPlane,
    observability: ObservabilityBundle,
    verification: VerificationBundle,
    kernel: KernelBundle,
) -> None:
    register_verification_control_plane(
        control_plane,
        verification.verification,
        verification.completion_authority,
        kernel.verification_evidence,
        kernel.verification_runtime,
    )
    control_plane.bind_observability_timeline(
        CompositeTimelineReader(
            (
                observability.exporter,
                VerificationTimelineReader(verification.verification),
            )
        )
    )


def build_control_plane(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    observability: ObservabilityBundle,
    runtime: RuntimeServicesBundle,
    platform_services: PlatformServicesBundle,
    repositories: RepositoryFoundationBundle,
    repository_runtime: RepositoryRuntimeBundle,
    verification: VerificationBundle,
    kernel: KernelBundle,
    evaluation: EvaluationBundle,
    health: HealthBundle,
    *,
    accounting_service: AccountingService | None = None,
) -> ControlPlaneBundle:
    """Create the canonical Control Plane once and install completed base domains."""

    portability_workflow = build_agent_portability_workflow(
        agents=runtime.agents.repository,
        models=runtime.models,
        scopes=storage.scopes,
        platform_version=__version__,
        capabilities=runtime.capabilities,
        templates=platform_services.templates.repository,
        routing_profiles=runtime.routing_profile_repository,
        evaluation=evaluation.composition.service,
        evaluation_fixture_exists=evaluation.composition.fixture_exists,
        research=platform_services.research,
    )
    control_plane = ControlPlane(
        kernel=kernel.kernel,
        events=storage.kernel_repository,
        scopes=storage.scopes,
        authorization=security.control_plane_authorization,
        workspace_provider=storage.workspaces,
        run_workspace_bindings=storage.run_workspace_bindings,
        health_providers=(health.provider,),
        model_registry=runtime.models,
        automation_state_path=config.database_dir / "automation.sqlite3",
        notification_state_path=config.database_dir / "notifications.sqlite3",
        conversation_service=runtime.conversations,
        conversation_agent_service=runtime.agents,
        conversation_file_provider=storage.files,
        conversation_response_provider=runtime.conversation_response_provider,
        portability_workflow=portability_workflow,
        approval_gate=security.approval_gate,
        accounting_service=accounting_service,
    )
    _register_base_domains(
        control_plane,
        storage,
        runtime,
        platform_services,
        repositories,
        repository_runtime,
        kernel,
        evaluation,
    )
    _register_templates(control_plane, storage, security, runtime, platform_services)
    _register_verification_observability(
        control_plane,
        observability,
        verification,
        kernel,
    )
    return ControlPlaneBundle(control_plane=control_plane)


def build_http(
    config: SingleNodeConfig,
    security: SecurityBundle,
    control_plane: ControlPlaneBundle,
) -> HttpBundle:
    """Build northbound authenticated HTTP/ASGI only after Control Plane completion."""

    http = BrowserFirstRunControlPlaneHTTP(
        control_plane.control_plane,
        security.authentication,
        security.authorization,
        secure_cookie=config.secure_cookie,
    )
    return HttpBundle(http=http, app=ControlPlaneASGI(http))
