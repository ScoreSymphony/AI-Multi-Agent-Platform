"""Canonical external connector framework."""

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
    SyncStatus,
)
from .provider import ConnectorProvider
from .registry import ConnectorRegistry
from .repository import ConnectorRepository, InMemoryConnectorRepository

__all__ = [
    "ConflictPolicy",
    "Connection",
    "ConnectionStatus",
    "ConnectorActionInvocation",
    "ConnectorActionResult",
    "ConnectorDefinition",
    "ConnectorEvent",
    "ConnectorProvider",
    "ConnectorRegistry",
    "ConnectorRepository",
    "ConnectorResourceQuery",
    "ConnectorSyncRequest",
    "ConnectorSyncResult",
    "ExternalNativeReference",
    "ExternalResourceReference",
    "InMemoryConnectorRepository",
    "SyncCheckpoint",
    "SyncStatus",
]
