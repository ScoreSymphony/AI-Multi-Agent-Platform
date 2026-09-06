"""Canonical external connector framework."""

from .capability_bridge import ConnectorActorResolver, ConnectorCapabilityProvider
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
from .sqlite_repository import SqliteConnectorRepository

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
    "ExternalNativeReference",
    "ExternalResourceReference",
    "InMemoryConnectorRepository",
    "REFERENCE_ACTION",
    "REFERENCE_CONNECTOR_TYPE",
    "REFERENCE_CONNECTOR_VERSION",
    "ReferenceConnectorProvider",
    "SqliteConnectorRepository",
    "SyncCheckpoint",
    "SyncMode",
    "SyncStatus",
    "connector_definition_id",
]
