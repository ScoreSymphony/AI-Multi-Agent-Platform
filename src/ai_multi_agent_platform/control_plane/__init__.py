"""Versioned platform-owned northbound Control Plane."""

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
from .observability_contract import ControlPlane, ControlPlaneHTTP, build_openapi
from .service import ScopeStore

CURRENT_COLLECTIONS = PLATFORM_COLLECTIONS

__all__ = [
    "APIError",
    "APIException",
    "API_VERSION",
    "ActorContext",
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
    "WorkspaceIdentity",
    "build_openapi",
]
