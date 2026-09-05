"""Versioned platform-owned northbound Control Plane."""

from .automation_api import (
    AUTOMATION_COLLECTION,
    AUTOMATION_COMMANDS,
    DELIVERY_COLLECTION,
)
from .automation_runtime_composition import AUTOMATION_STATE_ENV
from .conversation_api import (
    CONVERSATION_COLLECTION,
    CONVERSATION_COLLECTIONS,
    CONVERSATION_COMMANDS,
    CONVERSATION_MESSAGE_COLLECTION,
)
from .conversation_current_composition import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    build_openapi,
)
from .evaluation_contract import (
    EVALUATION_COLLECTIONS,
    EVALUATION_COMMANDS,
    EVALUATION_RUN_COLLECTION,
    EVALUATION_SUITE_COLLECTION,
    EvaluationRunResourceService,
    EvaluationSuiteResourceService,
    evaluation_command_handlers,
    evaluation_resource_services,
)
from .extensions import (
    FOUNDATION_COLLECTIONS,
    IMPLEMENTED_DOMAIN_COLLECTIONS,
    PLATFORM_COLLECTIONS,
    REQUIRED_COMMANDS,
    CommandHandler,
    InMemoryResourceService,
    ResourceService,
)
from .http import HTTPRequest, HTTPResponse
from .models import (
    API_VERSION,
    SUPPORTED_API_VERSIONS,
    ActorContext,
    APIError,
    APIException,
    PageQuery,
    RequestContext,
    WorkspaceIdentity,
)
from .notifications_composition import (
    NOTIFICATION_COLLECTION,
    NOTIFICATION_COMMANDS,
    NOTIFICATION_PREFERENCE_COLLECTION,
)
from .plugin_api import (
    PLUGIN_CANDIDATE_COLLECTION,
    PLUGIN_COLLECTION,
    PLUGIN_COLLECTIONS,
    PLUGIN_COMMANDS,
    PluginPermissionResolver,
)
from .portability_api import (
    PORTABILITY_COLLECTIONS,
    PORTABILITY_COMMANDS,
    PORTABILITY_PACKAGE_COLLECTION,
    PORTABILITY_PREVIEW_COLLECTION,
    PORTABILITY_REPORT_COLLECTION,
    ControlPlane,
)
from .service import ScopeStore
from .task_management_contract import (
    TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
    TASK_MANAGEMENT_COMMANDS,
    TASK_MANAGEMENT_UPDATE_COMMAND,
)

# Conversations remain optional runtime resources. Notifications are always-composed
# canonical domains and therefore remain part of this static compatibility inventory.
CURRENT_COLLECTIONS = PLATFORM_COLLECTIONS + (
    AUTOMATION_COLLECTION,
    DELIVERY_COLLECTION,
    NOTIFICATION_COLLECTION,
    NOTIFICATION_PREFERENCE_COLLECTION,
)

__all__ = [
    "APIError",
    "APIException",
    "API_VERSION",
    "AUTOMATION_COLLECTION",
    "AUTOMATION_COMMANDS",
    "AUTOMATION_STATE_ENV",
    "ActorContext",
    "AuthenticatedControlPlaneHTTP",
    "CONVERSATION_COLLECTION",
    "CONVERSATION_COLLECTIONS",
    "CONVERSATION_COMMANDS",
    "CONVERSATION_MESSAGE_COLLECTION",
    "CURRENT_COLLECTIONS",
    "CommandHandler",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "DELIVERY_COLLECTION",
    "EVALUATION_COLLECTIONS",
    "EVALUATION_COMMANDS",
    "EVALUATION_RUN_COLLECTION",
    "EVALUATION_SUITE_COLLECTION",
    "EvaluationRunResourceService",
    "EvaluationSuiteResourceService",
    "FOUNDATION_COLLECTIONS",
    "HTTPRequest",
    "HTTPResponse",
    "IMPLEMENTED_DOMAIN_COLLECTIONS",
    "InMemoryResourceService",
    "NOTIFICATION_COLLECTION",
    "NOTIFICATION_COMMANDS",
    "NOTIFICATION_PREFERENCE_COLLECTION",
    "PLATFORM_COLLECTIONS",
    "PLUGIN_CANDIDATE_COLLECTION",
    "PLUGIN_COLLECTION",
    "PLUGIN_COLLECTIONS",
    "PLUGIN_COMMANDS",
    "PORTABILITY_COLLECTIONS",
    "PORTABILITY_COMMANDS",
    "PORTABILITY_PACKAGE_COLLECTION",
    "PORTABILITY_PREVIEW_COLLECTION",
    "PORTABILITY_REPORT_COLLECTION",
    "PageQuery",
    "PluginPermissionResolver",
    "REQUIRED_COMMANDS",
    "RequestContext",
    "ResourceService",
    "SUPPORTED_API_VERSIONS",
    "ScopeStore",
    "TASK_MANAGEMENT_BULK_UPDATE_COMMAND",
    "TASK_MANAGEMENT_COMMANDS",
    "TASK_MANAGEMENT_UPDATE_COMMAND",
    "WorkspaceIdentity",
    "build_openapi",
    "evaluation_command_handlers",
    "evaluation_resource_services",
]
