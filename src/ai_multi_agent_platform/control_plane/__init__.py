"""Versioned platform-owned northbound Control Plane."""

from .authentication import AuthenticatedControlPlaneHTTP
from .extensions import (
    FOUNDATION_COLLECTIONS,
    IMPLEMENTED_DOMAIN_COLLECTIONS,
    PLATFORM_COLLECTIONS,
    REQUIRED_COMMANDS,
    CommandHandler,
    InMemoryResourceService,
    ResourceService,
)
from .http import ControlPlaneASGI, HTTPRequest, HTTPResponse
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
from .service import ScopeStore
from .task_management_contract import (
    TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
    TASK_MANAGEMENT_COMMANDS,
    TASK_MANAGEMENT_UPDATE_COMMAND,
)
from .workspace_task_management_api import ControlPlane, ControlPlaneHTTP, build_openapi

CURRENT_COLLECTIONS = PLATFORM_COLLECTIONS

__all__ = [
    "APIError",
    "APIException",
    "API_VERSION",
    "ActorContext",
    "AuthenticatedControlPlaneHTTP",
    "CURRENT_COLLECTIONS",
    "CommandHandler",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "FOUNDATION_COLLECTIONS",
    "HTTPRequest",
    "HTTPResponse",
    "IMPLEMENTED_DOMAIN_COLLECTIONS",
    "InMemoryResourceService",
    "PLATFORM_COLLECTIONS",
    "PageQuery",
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
]
