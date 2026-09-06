"""Reusable, versioned configuration Templates."""

from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.models.routing_profile_control_plane import (
    MODEL_ROUTING_PROFILE_COLLECTION,
    ModelRoutingProfileResourceService,
)

from .agent_handlers import (
    AgentTeamTemplateHandler,
    AgentTemplateExporter,
    AgentTemplateHandler,
    register_agent_template_handlers,
)
from .agent_team_exporter import AgentTeamTemplateExporter
from .application import (
    CompositeTemplateHandler,
    ContextualTemplateHandlerRegistry,
    ContextualTemplateResourceHandler,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from .automation_handler import (
    AutomationTemplateExporter,
    AutomationTemplateHandler,
    register_automation_template_handler,
)
from .capability_assignment_control_plane import (
    register_capability_assignment_template_export_control_plane,
)
from .capability_assignment_exporter import CapabilityAssignmentTemplateExporter
from .capability_assignment_handler import (
    CapabilityAssignmentTemplateHandler,
    register_capability_assignment_template_handler,
)
from .control_plane import TemplateEnvironmentResolver
from .control_plane import register_template_control_plane as _register_template_control_plane
from .environment import PlatformTemplateEnvironmentResolver
from .model_routing_control_plane import (
    register_model_routing_policy_template_export_control_plane,
)
from .model_routing_exporter import ModelRoutingPolicyTemplateExporter
from .model_routing_handler import (
    ModelRoutingPolicyTemplateHandler,
    register_model_routing_policy_template_handler,
)
from .models import (
    CapabilityRequirement,
    TemplateCompatibility,
    TemplateConfiguration,
    TemplateContent,
    TemplateDefinition,
    TemplateDependency,
    TemplateInstantiation,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateRevisionRef,
    TemplateRevisionState,
    TemplateTrust,
    TemplateType,
)
from .persistence import TEMPLATE_REPOSITORY_SCHEMA_VERSION, JsonTemplateRepository
from .project_handler import (
    ProjectTemplateExporter,
    ProjectTemplateHandler,
    register_project_template_handler,
)
from .repository import InMemoryTemplateRepository, TemplateRepository
from .service import (
    TemplateEnvironment,
    TemplateHandlerRegistry,
    TemplatePreview,
    TemplateResourceHandler,
    TemplateService,
    validate_template_configuration,
)
from .trust import activate_untrusted_revision
from .workflow_control_plane import register_workflow_template_export_control_plane
from .workflow_exporter import WorkflowTemplateExporter
from .workflow_handler import WorkflowTemplateHandler, register_workflow_template_handler
from .workspace_structure_handler import (
    WorkspaceStructureTemplateExporter,
    WorkspaceStructureTemplateHandler,
    register_workspace_structure_template_handler,
)


def register_template_control_plane(
    control_plane: ControlPlane,
    application: TemplateApplicationService,
    *,
    environment_resolver: TemplateEnvironmentResolver | None = None,
    agent_exporter: AgentTemplateExporter | None = None,
    automation_exporter: AutomationTemplateExporter | None = None,
) -> None:
    """Register the Template surface plus integrations for composed canonical owner domains.

    Optional create-from-existing commands are derived from the same contextual handlers
    that own Template application. This keeps the standard deployment from maintaining a
    second service graph solely for Template export.
    """

    _register_template_control_plane(
        control_plane,
        application,
        environment_resolver=environment_resolver,
        agent_exporter=agent_exporter,
        automation_exporter=automation_exporter,
    )

    capability_handler = application.handlers.get(TemplateType.CAPABILITY_ASSIGNMENT)
    if isinstance(capability_handler, CapabilityAssignmentTemplateHandler):
        register_capability_assignment_template_export_control_plane(
            control_plane,
            CapabilityAssignmentTemplateExporter(
                capability_handler.service,
                application.templates,
            ),
        )

    workflow_handler = application.handlers.get(TemplateType.WORKFLOW_PLAN)
    if (
        isinstance(workflow_handler, WorkflowTemplateHandler)
        and workflow_handler.agents is not None
    ):
        register_workflow_template_export_control_plane(
            control_plane,
            application.repository,
            WorkflowTemplateExporter(
                workflow_handler.service,
                workflow_handler.agents,
                application.templates,
            ),
        )

    if MODEL_ROUTING_PROFILE_COLLECTION in control_plane.registered_collections:
        resource_service = control_plane._registered_resource_service(
            MODEL_ROUTING_PROFILE_COLLECTION
        )
        if isinstance(resource_service, ModelRoutingProfileResourceService):
            if application.handlers.get(TemplateType.MODEL_ROUTING_POLICY) is None:
                register_model_routing_policy_template_handler(
                    application.handlers,
                    resource_service.service,
                )
            register_model_routing_policy_template_export_control_plane(
                control_plane,
                ModelRoutingPolicyTemplateExporter(
                    resource_service.service,
                    application.templates,
                ),
            )


__all__ = [
    "AgentTeamTemplateExporter",
    "AgentTeamTemplateHandler",
    "AgentTemplateExporter",
    "AgentTemplateHandler",
    "AutomationTemplateExporter",
    "AutomationTemplateHandler",
    "CapabilityAssignmentTemplateExporter",
    "CapabilityAssignmentTemplateHandler",
    "CapabilityRequirement",
    "CompositeTemplateHandler",
    "ContextualTemplateHandlerRegistry",
    "ContextualTemplateResourceHandler",
    "InMemoryTemplateRepository",
    "JsonTemplateRepository",
    "ModelRoutingPolicyTemplateExporter",
    "ModelRoutingPolicyTemplateHandler",
    "PlatformTemplateEnvironmentResolver",
    "ProjectTemplateExporter",
    "ProjectTemplateHandler",
    "TEMPLATE_REPOSITORY_SCHEMA_VERSION",
    "TemplateApplicationService",
    "TemplateCompatibility",
    "TemplateConfiguration",
    "TemplateContent",
    "TemplateDefinition",
    "TemplateDependency",
    "TemplateEnvironment",
    "TemplateHandlerRegistry",
    "TemplateInstantiation",
    "TemplateInstantiationContext",
    "TemplateInstantiationProvenance",
    "TemplatePreview",
    "TemplateProvenance",
    "TemplateRepository",
    "TemplateRequirements",
    "TemplateResourceChange",
    "TemplateResourceHandler",
    "TemplateResourceRef",
    "TemplateRevision",
    "TemplateRevisionRef",
    "TemplateRevisionState",
    "TemplateService",
    "TemplateTrust",
    "TemplateType",
    "WorkflowTemplateExporter",
    "WorkflowTemplateHandler",
    "WorkspaceStructureTemplateExporter",
    "WorkspaceStructureTemplateHandler",
    "activate_untrusted_revision",
    "register_agent_template_handlers",
    "register_automation_template_handler",
    "register_capability_assignment_template_handler",
    "register_model_routing_policy_template_handler",
    "register_project_template_handler",
    "register_template_control_plane",
    "register_workflow_template_handler",
    "register_workspace_structure_template_handler",
    "validate_template_configuration",
]
