"""Agent/model and pre-kernel platform-service builders."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ai_multi_agent_platform.agents import (
    AgentRuntime,
    AgentService,
    DurableRoutingProfileAgentRuntime,
    JsonAgentRepository,
)
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.capabilities.assignments import (
    CallableCapabilityAssignmentTargetResolver,
    CapabilityAssignmentService,
    JsonCapabilityAssignmentRepository,
)
from ai_multi_agent_platform.conversations import (
    ConversationService,
    DurableRoutingProfileConversationResponseProvider,
    JsonConversationRepository,
)
from ai_multi_agent_platform.models import (
    JsonModelRegistryStore,
    JsonModelRoutingProfileRepository,
    ModelRegistry,
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfileService,
    ModelRuntime,
)
from ai_multi_agent_platform.onboarding import (
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    OnboardingModelAdapter,
    OnboardingService,
)
from ai_multi_agent_platform.research import ResearchService, SqliteResearchRepository
from ai_multi_agent_platform.templates import (
    AgentTeamTemplateExporter,
    AgentTemplateExporter,
    ContextualTemplateHandlerRegistry,
    JsonTemplateRepository,
    ProjectTemplateExporter,
    TemplateApplicationService,
    WorkspaceStructureTemplateExporter,
    register_agent_template_handlers,
    register_capability_assignment_template_handler,
    register_project_template_handler,
    register_workflow_template_handler,
    register_workspace_structure_template_handler,
)
from ai_multi_agent_platform.templates.compensation import register_template_compensators
from ai_multi_agent_platform.workflows import (
    AuthorizedWorkflowService,
    JsonWorkflowRepository,
    WorkflowService,
)

from ..config import SingleNodeConfig
from .foundation import SecurityBundle, StorageBundle

ModelRuntimeFactory = Callable[[ModelRegistry], ModelRuntime]


@dataclass(frozen=True, slots=True)
class RuntimeServicesBundle:
    """Agent, conversation, model, routing and onboarding runtime services."""

    agents: AgentService
    conversations: ConversationService
    agent_runtime: AgentRuntime
    capabilities: CapabilityRegistry
    models: ModelRegistry
    routing_profile_repository: JsonModelRoutingProfileRepository
    routing_profiles: ModelRoutingProfileService
    routing_profile_assignment_gate: ModelRoutingProfileAssignmentGate
    model_runtime: ModelRuntime
    conversation_response_provider: DurableRoutingProfileConversationResponseProvider
    onboarding: OnboardingService


@dataclass(frozen=True, slots=True)
class PlatformServicesBundle:
    """Pre-kernel application services with no Control Plane dependency."""

    workflows: AuthorizedWorkflowService
    research: ResearchService
    template_handlers: ContextualTemplateHandlerRegistry
    templates: TemplateApplicationService
    capability_assignments: CapabilityAssignmentService
    agent_template_exporter: AgentTemplateExporter
    agent_team_template_exporter: AgentTeamTemplateExporter
    project_template_exporter: ProjectTemplateExporter
    workspace_template_exporter: WorkspaceStructureTemplateExporter


def build_runtime_services(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    *,
    onboarding_model_adapters: Iterable[OnboardingModelAdapter] = (),
    model_runtime_factory: ModelRuntimeFactory | None = None,
) -> RuntimeServicesBundle:
    """Construct agent/model services from explicit storage and security inputs."""

    database_dir = config.database_dir
    agents = AgentService(JsonAgentRepository(database_dir / "agents.json"))
    conversations = ConversationService(
        JsonConversationRepository(database_dir / "conversations.json")
    )
    capabilities = CapabilityRegistry()
    models = ModelRegistry()
    routing_profile_repository = JsonModelRoutingProfileRepository(
        database_dir / "model-routing-profiles.json"
    )
    agent_runtime = DurableRoutingProfileAgentRuntime(
        agents,
        routing_profile_repository=routing_profile_repository,
        model_registry=models,
        capability_registry=capabilities,
    )
    onboarding = OnboardingService(
        models=models,
        model_store=JsonModelRegistryStore(database_dir / "models.json"),
        provider_store=JsonModelProviderSetupStore(database_dir / "model-providers.json"),
        command_store=JsonOnboardingCommandStore(database_dir / "onboarding-commands.json"),
        scopes=storage.scopes,
        workspace_provider=storage.workspaces,
        agents=agents,
        agent_runtime=agent_runtime,
        model_adapters=onboarding_model_adapters,
    )
    onboarding.restore()
    model_runtime = (
        model_runtime_factory(models) if model_runtime_factory is not None else ModelRuntime(models)
    )
    routing_profiles = ModelRoutingProfileService(
        routing_profile_repository,
        authorization=security.authorization,
    )
    routing_profile_assignment_gate = ModelRoutingProfileAssignmentGate(
        routing_profile_repository,
        authorization=security.authorization,
    )
    conversation_response_provider = DurableRoutingProfileConversationResponseProvider(
        model_runtime,
        agents,
        routing_profile_repository=routing_profile_repository,
    )
    return RuntimeServicesBundle(
        agents=agents,
        conversations=conversations,
        agent_runtime=agent_runtime,
        capabilities=capabilities,
        models=models,
        routing_profile_repository=routing_profile_repository,
        routing_profiles=routing_profiles,
        routing_profile_assignment_gate=routing_profile_assignment_gate,
        model_runtime=model_runtime,
        conversation_response_provider=conversation_response_provider,
        onboarding=onboarding,
    )


def build_platform_services(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    runtime: RuntimeServicesBundle,
) -> PlatformServicesBundle:
    """Construct workflow, research, template and capability-assignment services."""

    database_dir = config.database_dir
    workflow_service = WorkflowService(JsonWorkflowRepository(database_dir / "workflows.json"))
    workflows = AuthorizedWorkflowService(workflow_service, security.approval_gate)
    research = ResearchService(
        SqliteResearchRepository(database_dir / "research.sqlite3"),
        authorization=security.approval_gate,
    )

    template_handlers = ContextualTemplateHandlerRegistry()
    register_agent_template_handlers(template_handlers, runtime.agents)
    register_project_template_handler(template_handlers, storage.scopes)
    register_workspace_structure_template_handler(
        template_handlers,
        storage.workspaces,
        storage.scopes,
    )
    register_workflow_template_handler(
        template_handlers,
        workflows,
        agents=runtime.agents,
    )
    register_template_compensators(
        template_handlers,
        agents=runtime.agents,
        scopes=storage.scopes,
        workspaces=storage.workspaces,
    )
    templates = TemplateApplicationService(
        JsonTemplateRepository(database_dir / "templates.json"),
        template_handlers,
    )
    capability_assignments = CapabilityAssignmentService(
        repository=JsonCapabilityAssignmentRepository(database_dir / "capability-assignments.json"),
        capabilities=runtime.capabilities,
        targets=CallableCapabilityAssignmentTargetResolver(
            get_agent=runtime.agents.repository.get_agent,
            get_team=runtime.agents.repository.get_team,
            get_project=storage.scopes.get_project,
        ),
        authorization=security.approval_gate,
    )
    register_capability_assignment_template_handler(template_handlers, capability_assignments)

    return PlatformServicesBundle(
        workflows=workflows,
        research=research,
        template_handlers=template_handlers,
        templates=templates,
        capability_assignments=capability_assignments,
        agent_template_exporter=AgentTemplateExporter(runtime.agents, templates.templates),
        agent_team_template_exporter=AgentTeamTemplateExporter(runtime.agents, templates.templates),
        project_template_exporter=ProjectTemplateExporter(storage.scopes, templates.templates),
        workspace_template_exporter=WorkspaceStructureTemplateExporter(
            storage.workspaces,
            templates.templates,
        ),
    )
