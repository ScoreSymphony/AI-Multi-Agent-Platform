"""Canonical external connector framework."""

from .async_sqlite import SqliteConnectorRepository
from .capability_bridge import ConnectorActorResolver, ConnectorCapabilityProvider
from .durable_github_releases import DurableGitHubReleaseConnectorProvider
from .egress import EgressConnectorService
from .github_releases import (
    GITHUB_API_VERSION,
    GITHUB_RELEASE_ASSET_ATTACH_ACTION,
    GITHUB_RELEASE_CONNECTOR_TYPE,
    GITHUB_RELEASE_CONNECTOR_VERSION,
    GITHUB_RELEASE_CREATE_ACTION,
    GitHubReleaseConnectorProvider,
    GitHubRestResponse,
    GitHubRestTransport,
    UrllibGitHubRestTransport,
)
from .models import (
    ConflictPolicy,
    Connection,
    ConnectionStatus,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorDefinition,
    ConnectorEvent,
    ConnectorResourceQuery,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalNativeReference,
    ExternalResourceReference,
    SyncCheckpoint,
    SyncMode,
    SyncStatus,
    connector_definition_id,
)
from .provider import ConnectorProvider
from .reference import (
    REFERENCE_ACTION,
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    ReferenceConnectorProvider,
)
from .registry import ConnectorRegistry
from .repository import ConnectorRepository, InMemoryConnectorRepository
from .service import ConnectorService

__all__ = [
    "ConflictPolicy",
    "Connection",
    "ConnectionStatus",
    "ConnectorActionInvocation",
    "ConnectorActionResult",
    "ConnectorActorResolver",
    "ConnectorCapabilityProvider",
    "ConnectorDefinition",
    "ConnectorEvent",
    "ConnectorProvider",
    "ConnectorRegistry",
    "ConnectorRepository",
    "ConnectorResourceQuery",
    "ConnectorService",
    "ConnectorSyncRequest",
    "ConnectorSyncResult",
    "DurableGitHubReleaseConnectorProvider",
    "EgressConnectorService",
    "ExternalNativeReference",
    "ExternalResourceReference",
    "GITHUB_API_VERSION",
    "GITHUB_RELEASE_ASSET_ATTACH_ACTION",
    "GITHUB_RELEASE_CONNECTOR_TYPE",
    "GITHUB_RELEASE_CONNECTOR_VERSION",
    "GITHUB_RELEASE_CREATE_ACTION",
    "GitHubReleaseConnectorProvider",
    "GitHubRestResponse",
    "GitHubRestTransport",
    "InMemoryConnectorRepository",
    "REFERENCE_ACTION",
    "REFERENCE_CONNECTOR_TYPE",
    "REFERENCE_CONNECTOR_VERSION",
    "ReferenceConnectorProvider",
    "SqliteConnectorRepository",
    "SyncCheckpoint",
    "SyncMode",
    "SyncStatus",
    "UrllibGitHubRestTransport",
    "connector_definition_id",
]
