"""Reusable, versioned configuration Templates."""

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
from .environment import PlatformTemplateEnvironmentResolver
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
from .workspace_structure_handler import (
    WorkspaceStructureTemplateExporter,
    WorkspaceStructureTemplateHandler,
    register_workspace_structure_template_handler,
)

__all__ = [
    "AgentTeamTemplateExporter",
    "AgentTeamTemplateHandler",
    "AgentTemplateExporter",
    "AgentTemplateHandler",
    "AutomationTemplateExporter",
    "AutomationTemplateHandler",
    "CapabilityRequirement",
    "CompositeTemplateHandler",
    "ContextualTemplateHandlerRegistry",
    "ContextualTemplateResourceHandler",
    "InMemoryTemplateRepository",
    "JsonTemplateRepository",
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
    "WorkspaceStructureTemplateExporter",
    "WorkspaceStructureTemplateHandler",
    "register_agent_template_handlers",
    "register_automation_template_handler",
    "register_project_template_handler",
    "register_workspace_structure_template_handler",
    "validate_template_configuration",
]
