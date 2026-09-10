"""Durable GitHub Releases connector hydration over canonical Connector persistence."""

from __future__ import annotations

from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import FileProvider

from .github_releases import GitHubReleaseConnectorProvider, GitHubRestTransport
from .models import Connection, ConnectorActionInvocation, ConnectorActionResult
from .repository import ConnectorRepository


class DurableGitHubReleaseConnectorProvider(GitHubReleaseConnectorProvider):
    """Hydrate persisted Connections once per process before GitHub actions execute.

    ``ConnectorService`` owns canonical Connection persistence.  The concrete GitHub adapter
    deliberately keeps only short-lived process-local connection state, so this wrapper restores
    that adapter state from the canonical repository after restart without inventing a second
    source of truth or embedding credentials in provider configuration.
    """

    def __init__(
        self,
        secret_provider: SecretProvider,
        files: FileProvider,
        connection_repository: ConnectorRepository,
        *,
        transport: GitHubRestTransport | None = None,
        provider_id: str = "connector.github-releases",
    ) -> None:
        super().__init__(
            secret_provider,
            files,
            transport=transport,
            provider_id=provider_id,
        )
        self._connection_repository = connection_repository
        self._hydrated_connections: set[str] = set()

    async def validate_connection(
        self,
        connection: Connection,
        context: OperationContext,
    ) -> Connection:
        normalized = await super().validate_connection(connection, context)
        self._hydrated_connections.add(normalized.id)
        return normalized

    async def invoke_action(
        self,
        invocation: ConnectorActionInvocation,
    ) -> ConnectorActionResult:
        if invocation.connection_id not in self._hydrated_connections:
            connection = await self._connection_repository.get_connection(invocation.connection_id)
            await self.validate_connection(connection, invocation.context)
        return await super().invoke_action(invocation)


__all__ = ["DurableGitHubReleaseConnectorProvider"]
