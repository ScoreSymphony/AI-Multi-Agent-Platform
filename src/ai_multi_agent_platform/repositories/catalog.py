"""Durable repository binding catalog and provider bootstrap seams."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.capabilities import SideEffectClassification
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorProvider,
    ExternalNativeReference,
    ExternalResourceReference,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .capabilities import RepositoryCapability, RepositoryOperation
from .connector_repository import ConnectorRepositoryProvider
from .contracts import RepositoryProvider
from .local_git import LocalGitRepositoryProvider
from .models import RepositoryConnection, RepositoryReference, RepositoryVisibility
from .service import RepositoryBinding, RepositoryRegistry

ConnectionResolver = Callable[[str], Awaitable[Connection]]
RepositoryProviderFactory = Callable[
    ["RepositoryBindingRecord", RepositoryConnection],
    RepositoryProvider,
]


@dataclass(frozen=True, slots=True)
class RepositoryBindingRecord:
    """Persistable routing metadata without live providers or credential material.

    The canonical Connector ``Connection`` remains owned by #44 and is resolved by ID during
    bootstrap. ``adapter_configuration`` is private provider configuration (for example a local
    checkout root); it is not part of canonical repository identity and must never contain
    credentials. Credentials continue to come exclusively from the resolved Connection's
    SecretReferences.
    """

    reference: RepositoryReference
    provider_id: str
    local: bool
    connection_metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    adapter_configuration: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("repository binding record provider_id must not be blank")
        connection_metadata = _json_mapping(self.connection_metadata, "connection_metadata")
        adapter_configuration = _json_mapping(
            self.adapter_configuration,
            "adapter_configuration",
        )
        object.__setattr__(self, "connection_metadata", MappingProxyType(connection_metadata))
        object.__setattr__(
            self,
            "adapter_configuration",
            MappingProxyType(adapter_configuration),
        )

    @property
    def repository_id(self) -> str:
        return self.reference.id

    @property
    def connection_id(self) -> str:
        return self.reference.connection_id

    @classmethod
    def from_binding(
        cls,
        binding: RepositoryBinding,
        *,
        adapter_configuration: Mapping[str, JsonValue] | None = None,
    ) -> RepositoryBindingRecord:
        return cls(
            reference=binding.reference,
            provider_id=binding.connection.provider_id,
            local=binding.connection.local,
            connection_metadata=binding.connection.metadata,
            adapter_configuration=adapter_configuration or {},
        )


class SqliteRepositoryBindingCatalog:
    """Restart-safe catalog for canonical repository bindings.

    Live ``RepositoryProvider`` instances are intentionally never serialized. On process start,
    ``RepositoryRegistryBootstrap`` combines these records with canonical Connections and a
    provider factory selected by provider ID.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repository_bindings (
                        repository_id TEXT PRIMARY KEY,
                        connection_id TEXT NOT NULL,
                        provider_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_repository_bindings_connection
                    ON repository_bindings(connection_id, repository_id)
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize repository binding catalog",
            ) from exc

    def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord:
        encoded = _encode_binding_record(record)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO repository_bindings(
                        repository_id,
                        connection_id,
                        provider_id,
                        payload_json
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(repository_id) DO UPDATE SET
                        connection_id = excluded.connection_id,
                        provider_id = excluded.provider_id,
                        payload_json = excluded.payload_json
                    """,
                    (
                        record.repository_id,
                        record.connection_id,
                        record.provider_id,
                        encoded,
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist repository binding",
            ) from exc
        return record

    def get(self, repository_id: str) -> RepositoryBindingRecord:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM repository_bindings
                    WHERE repository_id = ?
                    """,
                    (repository_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read repository binding",
            ) from exc
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"repository binding not found: {repository_id}")
        return _decode_binding_record(cast(str, row["payload_json"]))

    def list(self, *, connection_id: str | None = None) -> tuple[RepositoryBindingRecord, ...]:
        try:
            with self._connect() as connection:
                if connection_id is None:
                    rows = connection.execute(
                        """
                        SELECT payload_json
                        FROM repository_bindings
                        ORDER BY repository_id
                        """
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT payload_json
                        FROM repository_bindings
                        WHERE connection_id = ?
                        ORDER BY repository_id
                        """,
                        (connection_id,),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to list repository bindings",
            ) from exc
        return tuple(_decode_binding_record(cast(str, row["payload_json"])) for row in rows)

    def delete(self, repository_id: str) -> None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM repository_bindings WHERE repository_id = ?",
                    (repository_id,),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to delete repository binding",
            ) from exc
        if cursor.rowcount == 0:
            raise ContractError(ErrorCode.NOT_FOUND, f"repository binding not found: {repository_id}")


class RepositoryRegistryBootstrap:
    """Rebuild an in-process RepositoryRegistry from durable provider-neutral records."""

    def __init__(
        self,
        catalog: SqliteRepositoryBindingCatalog,
        *,
        factories: Mapping[str, RepositoryProviderFactory] | None = None,
    ) -> None:
        self._catalog = catalog
        self._factories = dict(factories or {})

    def register_factory(self, provider_id: str, factory: RepositoryProviderFactory) -> None:
        if not provider_id.strip():
            raise ValueError("repository provider factory ID must not be blank")
        if provider_id in self._factories:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"repository provider factory already registered: {provider_id}",
            )
        self._factories[provider_id] = factory

    async def restore(
        self,
        registry: RepositoryRegistry,
        resolve_connection: ConnectionResolver,
    ) -> tuple[RepositoryBinding, ...]:
        restored: list[RepositoryBinding] = []
        for record in self._catalog.list():
            factory = self._factories.get(record.provider_id)
            if factory is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    f"repository provider factory unavailable: {record.provider_id}",
                    provider_id=record.provider_id,
                    retryable=True,
                )
            connection = await resolve_connection(record.connection_id)
            if connection.id != record.connection_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repository Connection resolver returned the wrong canonical Connection",
                    provider_id=record.provider_id,
                )
            repository_connection = RepositoryConnection(
                connection=connection,
                provider_id=record.provider_id,
                local=record.local,
                metadata=record.connection_metadata,
            )
            provider = factory(record, repository_connection)
            if provider.provider_id != record.provider_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repository provider factory returned a provider with the wrong ID",
                    provider_id=record.provider_id,
                )
            binding = RepositoryBinding(repository_connection, record.reference, provider)
            registry.register(binding)
            restored.append(binding)
        return tuple(restored)


def local_git_repository_factory(
    record: RepositoryBindingRecord,
    connection: RepositoryConnection,
) -> RepositoryProvider:
    """Rebuild the Local Git adapter from private, non-canonical catalog configuration."""

    root = record.adapter_configuration.get("root")
    if not isinstance(root, str) or not root.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "local Git repository binding requires adapter_configuration.root",
            provider_id=record.provider_id,
        )
    git_binary = record.adapter_configuration.get("git_binary", "git")
    if not isinstance(git_binary, str) or not git_binary.strip():
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "local Git repository binding git_binary must be a non-blank string",
            provider_id=record.provider_id,
        )
    return LocalGitRepositoryProvider(
        root,
        connection,
        git_binary=git_binary,
        repository=record.reference,
        provider_id=record.provider_id,
    )


def connector_repository_factory(connector: ConnectorProvider) -> RepositoryProviderFactory:
    """Create a bootstrap factory for one hosted/self-hosted #44 ConnectorProvider."""

    def build(
        record: RepositoryBindingRecord,
        connection: RepositoryConnection,
    ) -> RepositoryProvider:
        del record
        return ConnectorRepositoryProvider(
            connector,
            connection,
            provider_id=connection.provider_id,
        )

    return build


def _encode_binding_record(record: RepositoryBindingRecord) -> str:
    return json.dumps(
        {
            "reference": record.reference.to_dict(),
            "provider_id": record.provider_id,
            "local": record.local,
            "connection_metadata": dict(record.connection_metadata),
            "adapter_configuration": dict(record.adapter_configuration),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_binding_record(payload: str) -> RepositoryBindingRecord:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "stored repository binding is not valid JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "stored repository binding must be a JSON object",
        )
    try:
        data = cast(dict[str, object], raw)
        reference = _decode_repository_reference(_required_mapping(data, "reference"))
        provider_id = _required_string(data, "provider_id")
        local = data.get("local")
        if not isinstance(local, bool):
            raise ValueError("stored repository binding local must be boolean")
        return RepositoryBindingRecord(
            reference=reference,
            provider_id=provider_id,
            local=local,
            connection_metadata=_json_mapping(
                _required_mapping(data, "connection_metadata"),
                "connection_metadata",
            ),
            adapter_configuration=_json_mapping(
                _required_mapping(data, "adapter_configuration"),
                "adapter_configuration",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "stored repository binding violates the canonical contract",
        ) from exc


def _decode_repository_reference(data: dict[str, object]) -> RepositoryReference:
    external = _required_mapping(data, "external_resource")
    native = _required_mapping(external, "native_reference")
    external_reference = ExternalResourceReference(
        id=_required_string(external, "id"),
        connection_id=_required_string(external, "connection_id"),
        resource_type=_required_string(external, "resource_type"),
        native_reference=ExternalNativeReference(
            namespace=_required_string(native, "namespace"),
            native_id=_required_string(native, "native_id"),
        ),
        canonical_url=_optional_string(external, "canonical_url"),
        version=_optional_string(external, "version"),
        revision=_optional_string(external, "revision"),
        provenance=_json_mapping(_required_mapping(external, "provenance"), "provenance"),
        metadata=_json_mapping(_required_mapping(external, "metadata"), "metadata"),
    )
    raw_capabilities = data.get("capabilities")
    if not isinstance(raw_capabilities, list):
        raise ValueError("stored repository reference capabilities must be an array")
    capabilities: list[RepositoryCapability] = []
    for raw_capability in raw_capabilities:
        if not isinstance(raw_capability, dict):
            raise ValueError("stored repository capability must be an object")
        capability = cast(dict[str, object], raw_capability)
        requires_credentials = capability.get("requires_credentials")
        supported = capability.get("supported")
        if not isinstance(requires_credentials, bool) or not isinstance(supported, bool):
            raise ValueError("stored repository capability flags must be boolean")
        capabilities.append(
            RepositoryCapability(
                operation=RepositoryOperation(_required_string(capability, "operation")),
                side_effects=SideEffectClassification(
                    _required_string(capability, "side_effects")
                ),
                requires_credentials=requires_credentials,
                supported=supported,
            )
        )
    return RepositoryReference(
        external_resource=external_reference,
        default_branch=_optional_string(data, "default_branch"),
        target_revision=_optional_string(data, "target_revision"),
        resolved_revision=_optional_string(data, "resolved_revision"),
        visibility=RepositoryVisibility(_required_string(data, "visibility")),
        capabilities=tuple(capabilities),
        metadata=_json_mapping(_required_mapping(data, "metadata"), "metadata"),
    )


def _json_mapping(value: Mapping[str, object], field_name: str) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        result[key] = _json_value(item, field_name)
    return result


def _json_value(value: object, field_name: str) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return _json_mapping(cast(Mapping[str, object], value), field_name)
    if isinstance(value, list | tuple):
        return [_json_value(item, field_name) for item in value]
    raise ValueError(f"{field_name} contains a non-JSON value: {type(value).__name__}")


def _required_mapping(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"stored repository binding field {key} must be an object")
    return cast(dict[str, object], value)


def _required_string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stored repository binding field {key} must be a non-blank string")
    return value


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stored repository binding field {key} must be string or null")
    return value
