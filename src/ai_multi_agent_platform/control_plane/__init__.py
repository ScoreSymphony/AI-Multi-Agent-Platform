"""Versioned platform-owned northbound Control Plane."""

from .authenticated_authorization import ControlPlane
from .authentication_hardening import AuthenticatedControlPlaneHTTP
from .automation_api import (
    AUTOMATION_COLLECTION,
    AUTOMATION_COMMANDS,
    DELIVERY_COLLECTION,
    ControlPlaneHTTP,
    build_openapi,
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

CURRENT_COLLECTIONS = PLATFORM_COLLECTIONS + (AUTOMATION_COLLECTION, DELIVERY_COLLECTION)

__all__ = [
    "APIError",
    "APIException",
    "API_VERSION",
    "AUTOMATION_COLLECTION",
    "AUTOMATION_COMMANDS",
    "ActorContext",
    "AuthenticatedControlPlaneHTTP",
    "CURRENT_COLLECTIONS",
    "CommandHandler",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "DELIVERY_COLLECTION",
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
