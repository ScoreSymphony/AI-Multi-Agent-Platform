"""Hosted/self-hosted repository composition over canonical #44 connector state."""

from __future__ import annotations

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectionStatus,
    ConnectorRegistry,
    ConnectorRepository,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .catalog import SqliteRepositoryBindingCatalog
from .connector_repository import ConnectorRepositoryProvider
from .management import RepositoryDiscoveryResolver
from .models import RepositoryConnection
from .service import RepositoryBinding, RepositoryRegistry


def connector_repository_discovery_resolver(
    connections: ConnectorRepository,
    connectors: ConnectorRegistry,
) -> RepositoryDiscoveryResolver:
    """Resolve a configured Connection into the provider-neutral repository bridge.

    The resolver owns no credential material. The canonical ``Connection`` loaded from #44 keeps
    its ``SecretReference`` values, while the concrete connector implementation is selected only
    from connector type/version metadata.
    """

    async def resolve(
        connection_id: str,
        provider_id: str,
    ) -> tuple[RepositoryConnection, ConnectorRepositoryProvider]:
        connection = await connections.get_connection(connection_id)
        _require_usable_connection(connection)
        connector = connectors.resolve(
            connection.connector_type_id,
            connection.connector_version,
        )
        expected_provider_id = f"repository-{connector.descriptor.provider_id}"
        if provider_id != expected_provider_id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "repository provider is not bound to the configured connector connection",
                details={"connection_id": connection_id, "provider_id": provider_id},
            )
        repository_connection = RepositoryConnection(
            connection=connection,
            provider_id=provider_id,
            local=False,
        )
        provider = ConnectorRepositoryProvider(
            connector,
            repository_connection,
            provider_id=provider_id,
        )
        return repository_connection, provider

    return resolve


async def restore_connector_repositories(
    catalog: SqliteRepositoryBindingCatalog,
    repository_registry: RepositoryRegistry,
    connections: ConnectorRepository,
    connectors: ConnectorRegistry,
) -> tuple[RepositoryBinding, ...]:
    """Restore durable non-local repository bindings from canonical connector state.

    Provider instances and credentials are intentionally not persisted by the repository catalog.
    Restart reconstructs them from the current #44 Connection and ConnectorRegistry. Missing or
    disabled providers fail closed instead of silently substituting a different adapter.
    """

    restored: list[RepositoryBinding] = []
    for record in catalog.list():
        if record.local:
            continue
        try:
            connection = await connections.get_connection(record.connection_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            # A removed canonical Connection cannot retain repository routing/search state.
            catalog.delete(record.repository_id)
            continue
        _require_usable_connection(connection)
        connector = connectors.resolve(
            connection.connector_type_id,
            connection.connector_version,
        )
        repository_connection = RepositoryConnection(
            connection=connection,
            provider_id=record.provider_id,
            local=False,
            metadata=record.connection_metadata,
        )
        provider = ConnectorRepositoryProvider(
            connector,
            repository_connection,
            provider_id=record.provider_id,
        )
        binding = RepositoryBinding(
            connection=repository_connection,
            reference=record.reference,
            provider=provider,
        )
        repository_registry.register(binding)
        restored.append(binding)
    return tuple(restored)


def _require_usable_connection(connection: Connection) -> None:
    if not connection.enabled or connection.status is ConnectionStatus.DISABLED:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "repository connector connection is disabled",
            retryable=True,
        )
