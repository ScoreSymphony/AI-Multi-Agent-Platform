"""Replaceable connector provider contract."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import HealthStatus, JsonValue, OperationContext

from .models import (
    Connection,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorDefinition,
    ConnectorEvent,
    ConnectorResourceQuery,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalResourceReference,
)


class ConnectorProvider(ProviderContract):
    """Adapter seam for external applications and services.

    The baseline lifecycle/resource/action/sync methods are mandatory. Broader
    integration operations are explicit optional hooks with fail-closed defaults so
    consumers never have to assume that every connector supports search, webhooks,
    file transfer or knowledge ingestion.
    """

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

    async def search_resources(
        self, query: ConnectorResourceQuery
    ) -> tuple[ExternalResourceReference, ...]:
        """Search external resources when the provider explicitly supports it."""

        self._unsupported("resource.search")

    @abstractmethod
    async def read_resource(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> ExternalResourceReference:
        """Refresh/read safe metadata for one external resource reference."""

    @abstractmethod
    async def invoke_action(self, invocation: ConnectorActionInvocation) -> ConnectorActionResult:
        """Invoke one connector action behind canonical policy/capability paths."""

    async def normalize_external_event(
        self,
        connection: Connection,
        native_event: Mapping[str, JsonValue],
        context: OperationContext,
    ) -> ConnectorEvent:
        """Verify/normalize one inbound provider event without executing privileged work."""

        self._unsupported("event.normalize")

    @abstractmethod
    async def synchronize(self, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        """Poll/synchronize one provider stream with explicit checkpoint state."""

    async def import_file_content(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> bytes:
        """Read external file bytes for handoff to the canonical #13 FileProvider."""

        self._unsupported("file.import")

    async def export_file_content(
        self,
        connection: Connection,
        data: bytes,
        *,
        resource_type: str,
        metadata: Mapping[str, JsonValue],
        context: OperationContext,
    ) -> ExternalResourceReference:
        """Export bytes supplied by #13 storage and return an external reference."""

        self._unsupported("file.export")

    async def read_knowledge_content(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> str:
        """Read source text for handoff to the canonical #13 KnowledgeProvider."""

        self._unsupported("knowledge.ingest")

    def _unsupported(self, operation: str) -> None:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"connector operation {operation!r} is not supported",
            provider_id=self.descriptor.provider_id,
            details={"operation": operation},
        )
