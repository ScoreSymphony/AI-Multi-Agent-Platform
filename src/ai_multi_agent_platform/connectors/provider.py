"""Replaceable connector provider contract."""

from __future__ import annotations

from abc import abstractmethod

from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import HealthStatus, OperationContext

from .models import (
    Connection,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorDefinition,
    ConnectorResourceQuery,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalResourceReference,
)


class ConnectorProvider(ProviderContract):
    """Adapter seam for external applications and services."""

    @property
    @abstractmethod
    def definition(self) -> ConnectorDefinition:
        """Return the canonical connector type/version implemented by this provider."""

    @abstractmethod
    async def validate_connection(
        self, connection: Connection, context: OperationContext
    ) -> Connection:
        """Validate/configure one connection and return normalized safe state."""

    @abstractmethod
    async def connection_health(
        self, connection: Connection, context: OperationContext
    ) -> HealthStatus:
        """Return normalized health for one configured connection."""

    @abstractmethod
    async def list_resources(
        self, query: ConnectorResourceQuery
    ) -> tuple[ExternalResourceReference, ...]:
        """Discover/list externally-owned resources."""

    @abstractmethod
    async def read_resource(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> ExternalResourceReference:
        """Refresh/read safe metadata for one external resource reference."""

    @abstractmethod
    async def invoke_action(
        self, invocation: ConnectorActionInvocation
    ) -> ConnectorActionResult:
        """Invoke one connector action behind canonical policy/capability paths."""

    @abstractmethod
    async def synchronize(self, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        """Poll/synchronize one provider stream with explicit checkpoint state."""
