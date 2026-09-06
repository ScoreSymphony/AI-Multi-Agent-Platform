"""Restart-durable local ConnectorRepository implementation.

SQLite is an implementation detail of the single-node profile. Canonical connector identity and
lifecycle semantics remain defined by :mod:`ai_multi_agent_platform.connectors.repository`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, HealthStatus, JsonValue
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.security import SecretReference, redact_sensitive

from .models import (
    ConflictPolicy,
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    ExternalNativeReference,
    ExternalResourceReference,
    SyncCheckpoint,
    SyncStatus,
)
from .repository import ConnectorRepository, ExternalResourceIdentity, _external_resource_identity

_SCHEMA_VERSION = 1


class SqliteConnectorRepository(ConnectorRepository):
    """Restart-durable Connector repository for local/self-hosted deployments.

    The database stores only canonical connector metadata. Credential values are never resolved by
    this layer; Connections persist their canonical ``SecretReference`` objects only.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > _SCHEMA_VERSION:
                raise RuntimeError(
                    "connector persistence schema is newer than this runtime supports: "
                    f"{current} > {_SCHEMA_VERSION}"
                )
            if current < 1:
                self._migrate_to_v1(connection)
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _migrate_to_v1(connection: sqlite3.Connection) -> None:
        """Create the initial deterministic connector-state schema from version zero."""

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS connector_definitions (
                connector_type_id TEXT NOT NULL,
                version TEXT NOT NULL,
                definition_id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (connector_type_id, version)
            );

            CREATE TABLE IF NOT EXISTS connections (
                connection_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS external_resources (
                resource_id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                native_namespace TEXT NOT NULL,
                native_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY (connection_id)
                    REFERENCES connections(connection_id)
                    ON DELETE CASCADE,
                UNIQUE (connection_id, resource_type, native_namespace, native_id)
            );

            CREATE INDEX IF NOT EXISTS idx_external_resources_connection
                ON external_resources(connection_id, resource_id);

            CREATE TABLE IF NOT EXISTS sync_checkpoints (
                connection_id TEXT NOT NULL,
                stream TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (connection_id, stream),
                FOREIGN KEY (connection_id)
                    REFERENCES connections(connection_id)
                    ON DELETE CASCADE
            );
            """
        )

    async def save_definition(self, definition: ConnectorDefinition) -> ConnectorDefinition:
        payload = _encode(_definition_to_json(definition))
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT definition_id
                FROM connector_definitions
                WHERE connector_type_id = ? AND version = ?
                """,
                (definition.connector_type_id, definition.version),
            ).fetchone()
            if current is not None and str(current["definition_id"]) != definition.id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "connector definition canonical identity cannot be rebound",
                    details={
                        "connector_type_id": definition.connector_type_id,
                        "version": definition.version,
                    },
                )
            connection.execute(
                """
                INSERT INTO connector_definitions (
                    connector_type_id, version, definition_id, payload_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(connector_type_id, version) DO UPDATE SET
                    definition_id = excluded.definition_id,
                    payload_json = excluded.payload_json
                """,
                (
                    definition.connector_type_id,
                    definition.version,
                    definition.id,
                    payload,
                ),
            )
        return definition

    async def get_definition(self, connector_type_id: str, version: str) -> ConnectorDefinition:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM connector_definitions
                WHERE connector_type_id = ? AND version = ?
                """,
                (connector_type_id, version),
            ).fetchone()
        if row is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"connector definition not found: {connector_type_id!r} {version!r}",
            )
        return _definition_from_json(str(row["payload_json"]))

    async def list_definitions(self) -> tuple[ConnectorDefinition, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM connector_definitions
                ORDER BY connector_type_id, version
                """
            ).fetchall()
        return tuple(_definition_from_json(str(row["payload_json"])) for row in rows)

    async def save_connection(self, connection: Connection) -> Connection:
        _require_safe_connection_metadata(connection)
        payload = _encode(_connection_to_json(connection))
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT revision FROM connections WHERE connection_id = ?",
                (connection.id,),
            ).fetchone()
            if row is not None and connection.revision < int(row["revision"]):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "connection revision must not move backwards",
                )
            database.execute(
                """
                INSERT INTO connections (connection_id, revision, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(connection_id) DO UPDATE SET
                    revision = excluded.revision,
                    payload_json = excluded.payload_json
                """,
                (connection.id, connection.revision, payload),
            )
        return connection

    async def get_connection(self, connection_id: str) -> Connection:
        validate_id(connection_id, "connection")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"connection not found: {connection_id}")
        return _connection_from_json(str(row["payload_json"]))

    async def list_connections(self, *, project_id: str | None = None) -> tuple[Connection, ...]:
        if project_id is not None:
            validate_id(project_id, "project")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM connections ORDER BY connection_id"
            ).fetchall()
        items = tuple(_connection_from_json(str(row["payload_json"])) for row in rows)
        if project_id is None:
            return items
        return tuple(item for item in items if item.project_id == project_id)

    async def delete_connection(self, connection_id: str) -> None:
        validate_id(connection_id, "connection")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM connections WHERE connection_id = ?",
                (connection_id,),
            )
            if cursor.rowcount == 0:
                raise ContractError(ErrorCode.NOT_FOUND, f"connection not found: {connection_id}")

    async def remove_connection_if_unused(self, connection_id: str) -> None:
        validate_id(connection_id, "connection")
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT payload_json FROM connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"connection not found: {connection_id}")
            connection = _connection_from_json(str(row["payload_json"]))
            if connection.enabled or connection.status is not ConnectionStatus.DISABLED:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "cannot compensate a Connection that has entered active lifecycle state",
                    details={"connection_id": connection_id},
                )
            checkpoint = database.execute(
                "SELECT 1 FROM sync_checkpoints WHERE connection_id = ? LIMIT 1",
                (connection_id,),
            ).fetchone()
            if checkpoint is not None:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "cannot compensate a Connection with synchronization history",
                    details={"connection_id": connection_id},
                )
            resource = database.execute(
                "SELECT 1 FROM external_resources WHERE connection_id = ? LIMIT 1",
                (connection_id,),
            ).fetchone()
            if resource is not None:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "cannot compensate a Connection with durable external-resource references",
                    details={"connection_id": connection_id},
                )
            database.execute(
                "DELETE FROM connections WHERE connection_id = ?",
                (connection_id,),
            )

    async def save_external_resource(
        self, resource: ExternalResourceReference
    ) -> ExternalResourceReference:
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            self._require_connection(database, resource.connection_id)
            canonical = self._canonical_external_resource(database, resource)
            identity = _external_resource_identity(canonical)
            database.execute(
                """
                INSERT INTO external_resources (
                    resource_id, connection_id, resource_type,
                    native_namespace, native_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_id) DO UPDATE SET
                    connection_id = excluded.connection_id,
                    resource_type = excluded.resource_type,
                    native_namespace = excluded.native_namespace,
                    native_id = excluded.native_id,
                    payload_json = excluded.payload_json
                """,
                (*_resource_columns(canonical, identity), _encode(_resource_to_json(canonical))),
            )
        return canonical

    async def replace_external_resources(
        self,
        connection_id: str,
        resources: tuple[ExternalResourceReference, ...],
    ) -> tuple[ExternalResourceReference, ...]:
        validate_id(connection_id, "connection")
        if any(resource.connection_id != connection_id for resource in resources):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "external-resource rebuild contains a wrapper for another Connection",
                details={"connection_id": connection_id},
            )
        if len({resource.id for resource in resources}) != len(resources):
            raise ContractError(
                ErrorCode.CONFLICT,
                "external-resource rebuild contains duplicate proposed canonical IDs",
                details={"connection_id": connection_id},
            )
        identities = tuple(_external_resource_identity(resource) for resource in resources)
        if len(set(identities)) != len(identities):
            raise ContractError(
                ErrorCode.CONFLICT,
                "external-resource rebuild contains duplicate provider-native identities",
                details={"connection_id": connection_id},
            )

        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            self._require_connection(database, connection_id)
            canonical = tuple(
                self._canonical_external_resource(database, item) for item in resources
            )
            if len({resource.id for resource in canonical}) != len(canonical):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "external-resource rebuild resolves multiple wrappers to one canonical ID",
                    details={"connection_id": connection_id},
                )
            database.execute(
                "DELETE FROM external_resources WHERE connection_id = ?",
                (connection_id,),
            )
            for resource in canonical:
                identity = _external_resource_identity(resource)
                database.execute(
                    """
                    INSERT INTO external_resources (
                        resource_id, connection_id, resource_type,
                        native_namespace, native_id, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (*_resource_columns(resource, identity), _encode(_resource_to_json(resource))),
                )
        return tuple(sorted(canonical, key=lambda item: item.id))

    async def get_external_resource(self, resource_id: str) -> ExternalResourceReference:
        validate_id(resource_id, "external_resource")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM external_resources WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()
        if row is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"external resource not found: {resource_id}",
            )
        return _resource_from_json(str(row["payload_json"]))

    async def list_external_resources(
        self, *, connection_id: str | None = None
    ) -> tuple[ExternalResourceReference, ...]:
        if connection_id is not None:
            validate_id(connection_id, "connection")
        with self._connect() as connection:
            if connection_id is None:
                rows = connection.execute(
                    "SELECT payload_json FROM external_resources ORDER BY resource_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM external_resources
                    WHERE connection_id = ?
                    ORDER BY resource_id
                    """,
                    (connection_id,),
                ).fetchall()
        return tuple(_resource_from_json(str(row["payload_json"])) for row in rows)

    async def delete_external_resource(self, resource_id: str) -> None:
        validate_id(resource_id, "external_resource")
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM external_resources WHERE resource_id = ?",
                (resource_id,),
            )
            if cursor.rowcount == 0:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"external resource not found: {resource_id}",
                )

    async def save_checkpoint(self, checkpoint: SyncCheckpoint) -> SyncCheckpoint:
        payload = _encode(_checkpoint_to_json(checkpoint))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_connection(connection, checkpoint.connection_id)
            connection.execute(
                """
                INSERT INTO sync_checkpoints (connection_id, stream, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(connection_id, stream) DO UPDATE SET
                    payload_json = excluded.payload_json
                """,
                (checkpoint.connection_id, checkpoint.stream, payload),
            )
        return checkpoint

    async def get_checkpoint(self, connection_id: str, stream: str) -> SyncCheckpoint | None:
        validate_id(connection_id, "connection")
        if not stream.strip():
            raise ValueError("stream must not be blank")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM sync_checkpoints
                WHERE connection_id = ? AND stream = ?
                """,
                (connection_id, stream),
            ).fetchone()
        if row is None:
            return None
        return _checkpoint_from_json(str(row["payload_json"]))

    @staticmethod
    def _require_connection(database: sqlite3.Connection, connection_id: str) -> None:
        row = database.execute(
            "SELECT 1 FROM connections WHERE connection_id = ?",
            (connection_id,),
        ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"connection not found: {connection_id}")

    @staticmethod
    def _canonical_external_resource(
        database: sqlite3.Connection,
        resource: ExternalResourceReference,
    ) -> ExternalResourceReference:
        identity = _external_resource_identity(resource)
        row_by_id = database.execute(
            """
            SELECT connection_id, resource_type, native_namespace, native_id
            FROM external_resources
            WHERE resource_id = ?
            """,
            (resource.id,),
        ).fetchone()
        if row_by_id is not None and _row_identity(row_by_id) != identity:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external-resource canonical identity cannot be rebound",
                details={"external_resource_id": resource.id},
            )
        row_by_identity = database.execute(
            """
            SELECT resource_id
            FROM external_resources
            WHERE connection_id = ?
              AND resource_type = ?
              AND native_namespace = ?
              AND native_id = ?
            """,
            identity,
        ).fetchone()
        if row_by_identity is None:
            return resource
        canonical_id = str(row_by_identity["resource_id"])
        if canonical_id == resource.id:
            return resource
        from dataclasses import replace

        return replace(resource, id=canonical_id)


def _resource_columns(
    resource: ExternalResourceReference,
    identity: ExternalResourceIdentity,
) -> tuple[str, str, str, str, str]:
    return (resource.id, *identity)


def _row_identity(row: sqlite3.Row) -> ExternalResourceIdentity:
    return (
        str(row["connection_id"]),
        str(row["resource_type"]),
        str(row["native_namespace"]),
        str(row["native_id"]),
    )


def _encode(value: dict[str, JsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _decode(encoded: str) -> dict[str, JsonValue]:
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("persisted connector payload must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    return {key: _json_value(item) for key, item in value.items()}


def _adapter_metadata_to_json(metadata: tuple[AdapterMetadata, ...]) -> list[JsonValue]:
    return [{"namespace": item.namespace, "values": _json_object(item.values)} for item in metadata]


def _adapter_metadata_from_json(value: object) -> tuple[AdapterMetadata, ...]:
    if not isinstance(value, list):
        raise ValueError("adapter_metadata must be an array")
    items: list[AdapterMetadata] = []
    for raw in value:
        data = _object(raw, "adapter_metadata")
        items.append(
            AdapterMetadata(
                namespace=_string(data, "namespace"),
                values=_object(data.get("values"), "adapter_metadata.values"),
            )
        )
    return tuple(items)


def _definition_to_json(definition: ConnectorDefinition) -> dict[str, JsonValue]:
    return {
        "id": definition.id,
        "connector_type_id": definition.connector_type_id,
        "name": definition.name,
        "version": definition.version,
        "description": definition.description,
        "supported_operations": list(definition.supported_operations),
        "features": list(definition.features),
        "authentication_requirements": list(definition.authentication_requirements),
        "resource_types": list(definition.resource_types),
        "actions": list(definition.actions),
        "event_types": list(definition.event_types),
        "configuration_schema": _json_object(definition.configuration_schema),
        "health_semantics": _json_object(definition.health_semantics),
        "adapter_metadata": _adapter_metadata_to_json(definition.adapter_metadata),
    }


def _definition_from_json(encoded: str) -> ConnectorDefinition:
    data = _decode(encoded)
    return ConnectorDefinition(
        id=_string(data, "id"),
        connector_type_id=_string(data, "connector_type_id"),
        name=_string(data, "name"),
        version=_string(data, "version"),
        description=_string_allow_blank(data, "description"),
        supported_operations=_string_tuple(
            data.get("supported_operations"), "supported_operations"
        ),
        features=_string_tuple(data.get("features"), "features"),
        authentication_requirements=_string_tuple(
            data.get("authentication_requirements"), "authentication_requirements"
        ),
        resource_types=_string_tuple(data.get("resource_types"), "resource_types"),
        actions=_string_tuple(data.get("actions"), "actions"),
        event_types=_string_tuple(data.get("event_types"), "event_types"),
        configuration_schema=_object(data.get("configuration_schema"), "configuration_schema"),
        health_semantics=_object(data.get("health_semantics"), "health_semantics"),
        adapter_metadata=_adapter_metadata_from_json(data.get("adapter_metadata")),
    )


def _require_safe_connection_metadata(connection: Connection) -> None:
    endpoint = _json_object(connection.endpoint_metadata)
    if redact_sensitive(endpoint) != endpoint:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "connection endpoint metadata must not contain embedded credentials",
        )


def _connection_to_json(connection: Connection) -> dict[str, JsonValue]:
    return {
        "id": connection.id,
        "connector_type_id": connection.connector_type_id,
        "connector_version": connection.connector_version,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "display_name": connection.display_name,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
        "endpoint_metadata": _json_object(connection.endpoint_metadata),
        "secret_references": [reference.to_dict() for reference in connection.secret_references],
        "requested_scopes": list(connection.requested_scopes),
        "granted_scopes": list(connection.granted_scopes),
        "enabled": connection.enabled,
        "status": connection.status.value,
        "health": connection.health.value,
        "created_at": connection.created_at.isoformat(),
        "updated_at": connection.updated_at.isoformat(),
        "last_checked_at": (
            connection.last_checked_at.isoformat()
            if connection.last_checked_at is not None
            else None
        ),
        "revision": connection.revision,
        "adapter_metadata": _adapter_metadata_to_json(connection.adapter_metadata),
    }


def _connection_from_json(encoded: str) -> Connection:
    data = _decode(encoded)
    raw_refs = data.get("secret_references")
    if not isinstance(raw_refs, list):
        raise ValueError("secret_references must be an array")
    return Connection(
        id=_string(data, "id"),
        connector_type_id=_string(data, "connector_type_id"),
        connector_version=_string(data, "connector_version"),
        owner_type=_string(data, "owner_type"),
        owner_id=_string(data, "owner_id"),
        display_name=_string(data, "display_name"),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        organization_id=_optional_string(data.get("organization_id"), "organization_id"),
        endpoint_metadata=_object(data.get("endpoint_metadata"), "endpoint_metadata"),
        secret_references=tuple(_secret_reference_from_json(item) for item in raw_refs),
        requested_scopes=_string_tuple(data.get("requested_scopes"), "requested_scopes"),
        granted_scopes=_string_tuple(data.get("granted_scopes"), "granted_scopes"),
        enabled=_boolean(data.get("enabled"), "enabled"),
        status=ConnectionStatus(_string(data, "status")),
        health=HealthStatus(_string(data, "health")),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
        last_checked_at=_optional_timestamp(data.get("last_checked_at"), "last_checked_at"),
        revision=_positive_int(data.get("revision"), "revision"),
        adapter_metadata=_adapter_metadata_from_json(data.get("adapter_metadata")),
    )


def _secret_reference_from_json(value: object) -> SecretReference:
    data = _object(value, "secret_reference")
    return SecretReference(
        provider=_string(data, "provider"),
        secret_id=_string(data, "secret_id"),
        scope=_string(data, "scope"),
        version=_optional_string(data.get("version"), "secret_reference.version"),
        metadata=_object(data.get("metadata"), "secret_reference.metadata"),
    )


def _resource_to_json(resource: ExternalResourceReference) -> dict[str, JsonValue]:
    return {
        "id": resource.id,
        "connection_id": resource.connection_id,
        "resource_type": resource.resource_type,
        "native_reference": {
            "namespace": resource.native_reference.namespace,
            "native_id": resource.native_reference.native_id,
        },
        "canonical_url": resource.canonical_url,
        "version": resource.version,
        "revision": resource.revision,
        "provenance": _json_object(resource.provenance),
        "metadata": _json_object(resource.metadata),
        "adapter_metadata": _adapter_metadata_to_json(resource.adapter_metadata),
    }


def _resource_from_json(encoded: str) -> ExternalResourceReference:
    data = _decode(encoded)
    native = _object(data.get("native_reference"), "native_reference")
    return ExternalResourceReference(
        id=_string(data, "id"),
        connection_id=_string(data, "connection_id"),
        resource_type=_string(data, "resource_type"),
        native_reference=ExternalNativeReference(
            namespace=_string(native, "namespace"),
            native_id=_string(native, "native_id"),
        ),
        canonical_url=_optional_string(data.get("canonical_url"), "canonical_url"),
        version=_optional_string(data.get("version"), "version"),
        revision=_optional_string(data.get("revision"), "revision"),
        provenance=_object(data.get("provenance"), "provenance"),
        metadata=_object(data.get("metadata"), "metadata"),
        adapter_metadata=_adapter_metadata_from_json(data.get("adapter_metadata")),
    )


def _checkpoint_to_json(checkpoint: SyncCheckpoint) -> dict[str, JsonValue]:
    return {
        "connection_id": checkpoint.connection_id,
        "stream": checkpoint.stream,
        "cursor": checkpoint.cursor,
        "last_successful_sync": (
            checkpoint.last_successful_sync.isoformat()
            if checkpoint.last_successful_sync is not None
            else None
        ),
        "remote_revision": checkpoint.remote_revision,
        "status": checkpoint.status.value,
        "retry_count": checkpoint.retry_count,
        "error_code": checkpoint.error_code,
        "dedupe_mapping": _json_object(checkpoint.dedupe_mapping),
        "conflict_policy": checkpoint.conflict_policy.value,
        "updated_at": checkpoint.updated_at.isoformat(),
    }


def _checkpoint_from_json(encoded: str) -> SyncCheckpoint:
    data = _decode(encoded)
    return SyncCheckpoint(
        connection_id=_string(data, "connection_id"),
        stream=_string(data, "stream"),
        cursor=_optional_string(data.get("cursor"), "cursor"),
        last_successful_sync=_optional_timestamp(
            data.get("last_successful_sync"), "last_successful_sync"
        ),
        remote_revision=_optional_string(data.get("remote_revision"), "remote_revision"),
        status=SyncStatus(_string(data, "status")),
        retry_count=_nonnegative_int(data.get("retry_count"), "retry_count"),
        error_code=_optional_string(data.get("error_code"), "error_code"),
        dedupe_mapping=_object(data.get("dedupe_mapping"), "dedupe_mapping"),
        conflict_policy=ConflictPolicy(_string(data, "conflict_policy")),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
    )


def _object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _string(data: Mapping[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _string_allow_blank(data: Mapping[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string or null")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{field_name} must contain non-blank strings")
    return tuple(cast(str, item) for item in value)


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _optional_timestamp(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field_name)


def _positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value
