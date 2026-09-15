"""Kernel-independent platform service construction for single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.capability_assignments import (
    CallableCapabilityAssignmentTargetResolver,
    CapabilityAssignmentService,
    JsonCapabilityAssignmentRepository,
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
from .runtime_services import RuntimeServicesBundle
from .security import SecurityBundle
from .storage import StorageBundle


@dataclass(frozen=True, slots=True)
class PlatformServicesBundle:
    """Kernel-independent workflow, research, template and assignment services."""

    workflows: AuthorizedWorkflowService
    research: ResearchService
    template_handlers: ContextualTemplateHandlerRegistry
    templates: TemplateApplicationService
    capability_assignments: CapabilityAssignmentService
    agent_template_exporter: AgentTemplateExporter
    agent_team_template_exporter: AgentTeamTemplateExporter
    project_template_exporter: ProjectTemplateExporter
    workspace_template_exporter: WorkspaceStructureTemplateExporter


def build_platform_services(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    runtime: RuntimeServicesBundle,
) -> PlatformServicesBundle:
    """Build platform services that do not need the canonical Task/Run kernel."""

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
