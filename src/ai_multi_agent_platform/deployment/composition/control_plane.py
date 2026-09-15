"""Control Plane assembly for completed single-node domain services."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.agents import register_standard_agent_control_plane
from ai_multi_agent_platform.agents.routing_profile_control_plane import (
    register_routing_profile_aware_agent_control_plane,
)
from ai_multi_agent_platform.control_plane import (
    evaluation_command_handlers,
    evaluation_resource_services,
)
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.coordination import (
    coordination_command_handlers,
    coordination_resource_services,
)
from ai_multi_agent_platform.evaluation.single_node import SingleNodeEvaluationComposition
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
from .execution import ExecutionBundle
from .kernel import KernelBundle
from .observability import ObservabilityBundle
from .platform_services import PlatformServicesBundle
from .repositories import RepositoryFoundationBundle, RepositoryRuntimeBundle
from .runtime_services import RuntimeServicesBundle
from .security import SecurityBundle
from .storage import StorageBundle
from .verification import VerificationBundle


@dataclass(frozen=True, slots=True)
class ControlPlaneBundle:
    """Canonical Control Plane plus the readiness authority it exposes."""

    control_plane: ControlPlane
    health_provider: AggregatedHealthProvider


def build_control_plane(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    observability: ObservabilityBundle,
    runtime: RuntimeServicesBundle,
    platform_services: PlatformServicesBundle,
    repositories: RepositoryFoundationBundle,
    repository_runtime: RepositoryRuntimeBundle,
    execution: ExecutionBundle,
    verification: VerificationBundle,
    kernel: KernelBundle,
    evaluation: SingleNodeEvaluationComposition,
    *,
    accounting_service: AccountingService | None = None,
) -> ControlPlaneBundle:
    """Create the Control Plane once, then install completed domain registrations deterministically."""

    portability_workflow = build_agent_portability_workflow(
        agents=runtime.agents.repository,
        models=runtime.models,
        scopes=storage.scopes,
        platform_version=__version__,
        capabilities=runtime.capabilities,
        templates=platform_services.templates.repository,
        routing_profiles=runtime.routing_profile_repository,
        evaluation=evaluation.service,
        evaluation_fixture_exists=evaluation.fixture_exists,
        research=platform_services.research,
    )
    health_provider = AggregatedHealthProvider(
        (
            ProviderHealthDependency(
                execution.orchestrator,
                required=True,
                name="orchestrator",
            ),
            ProviderHealthDependency(execution.lifecycle, required=True, name="lifecycle"),
            ProviderHealthDependency(storage.files, required=True, name="files"),
        )
    )
    control_plane = ControlPlane(
        kernel=kernel.kernel,
        events=storage.kernel_repository,
        scopes=storage.scopes,
        authorization=security.control_plane_authorization,
        workspace_provider=storage.workspaces,
        run_workspace_bindings=storage.run_workspace_bindings,
        health_providers=(health_provider,),
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
    resolvers = control_plane.workspace_source_resolvers
    if resolvers is None:
        raise RuntimeError(
            "single-node Control Plane did not initialize Workspace source resolvers"
        )
    resolvers.register(RepositoryWorkspaceSourceResolver(repositories.registry, storage.files))
    control_plane.configure_repository_run_integration(repository_runtime.run_integration)
    register_repository_control_plane(
        control_plane,
        repositories.services,
        management=repositories.management,
    )
    register_searchable_research_control_plane(control_plane, platform_services.research)
    for collection, service in evaluation_resource_services(evaluation.service).items():
        control_plane.register_resource_service(collection, service)
    for command, handler in evaluation_command_handlers(evaluation.service).items():
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
            ModelRoutingProfileRef(definition.profile_id, definition.current_revision).canonical_ref
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
    register_verification_control_plane(
        control_plane,
        verification.service,
        verification.completion_authority,
        kernel.verification_evidence,
        kernel.verification_runtime,
    )
    control_plane.bind_observability_timeline(
        CompositeTimelineReader(
            (
                observability.exporter,
                VerificationTimelineReader(verification.service),
            )
        )
    )
    return ControlPlaneBundle(
        control_plane=control_plane,
        health_provider=health_provider,
    )
