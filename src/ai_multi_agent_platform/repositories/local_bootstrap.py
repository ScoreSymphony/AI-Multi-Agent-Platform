"""Synchronous bootstrap for deployment-managed local Git repository bindings."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .catalog import SqliteRepositoryBindingCatalog, local_git_repository_factory
from .models import RepositoryConnection
from .service import RepositoryBinding, RepositoryRegistry

_LOCAL_BOOTSTRAP_KEY = "managed_local_connection"


def managed_local_connection_metadata(
    connection: RepositoryConnection,
) -> dict[str, JsonValue]:
    """Return the non-secret Connection fields needed to restore platform-managed local Git.

    SecretReferences, endpoint metadata and adapter credentials are intentionally excluded. Hosted
    connectors continue to restore through the canonical #44 Connection resolver instead.
    """

    canonical = connection.connection
    return {
        _LOCAL_BOOTSTRAP_KEY: {
            "connector_type_id": canonical.connector_type_id,
            "connector_version": canonical.connector_version,
            "owner_type": canonical.owner_type,
            "owner_id": canonical.owner_id,
            "display_name": canonical.display_name,
            "project_id": canonical.project_id,
            "organization_id": canonical.organization_id,
        }
    }


def restore_managed_local_repositories(
    catalog: SqliteRepositoryBindingCatalog,
    registry: RepositoryRegistry,
) -> tuple[RepositoryBinding, ...]:
    """Restore only managed local Git records without requiring an async Connector resolver."""

    restored: list[RepositoryBinding] = []
    for record in catalog.list():
        if not record.local:
            continue
        raw = record.connection_metadata.get(_LOCAL_BOOTSTRAP_KEY)
        if not isinstance(raw, Mapping):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "managed local repository binding lacks non-secret Connection bootstrap metadata",
                provider_id=record.provider_id,
            )
        bootstrap = dict(raw)
        connection = Connection(
            id=record.connection_id,
            connector_type_id=_required_string(bootstrap, "connector_type_id"),
            connector_version=_required_string(bootstrap, "connector_version"),
            owner_type=_required_string(bootstrap, "owner_type"),
            owner_id=_required_string(bootstrap, "owner_id"),
            display_name=_required_string(bootstrap, "display_name"),
            project_id=_optional_string(bootstrap, "project_id"),
            organization_id=_optional_string(bootstrap, "organization_id"),
        )
        repository_connection = RepositoryConnection(
            connection=connection,
            provider_id=record.provider_id,
            local=True,
            metadata=record.connection_metadata,
        )
        provider = local_git_repository_factory(record, repository_connection)
        if provider.provider_id != record.provider_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "managed local repository factory returned the wrong provider ID",
                provider_id=record.provider_id,
            )
        binding = RepositoryBinding(repository_connection, record.reference, provider)
        registry.register(binding)
        restored.append(binding)
    return tuple(restored)


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"managed local repository bootstrap field {key} must be a non-blank string",
        )
    return value


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"managed local repository bootstrap field {key} must be string or null",
        )
    return value
