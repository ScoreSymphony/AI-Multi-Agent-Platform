"""Awaitable Connector SQLite adapter with bounded worker-owned persistence I/O."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from ._sqlite_async import AsyncSqliteOffload, map_sqlite_error
from .models import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    ExternalResourceReference,
    SyncCheckpoint,
)
from .repository import _external_resource_identity
from .sqlite_repository import (
    _SCHEMA_VERSION,
    SqliteConnectorRepository as _SyncSqliteConnectorRepository,
    _checkpoint_from_json,
    _checkpoint_to_json,
    _connection_from_json,
    _connection_to_json,
    _definition_from_json,
    _definition_to_json,
    _encode,
    _require_safe_connection_metadata,
    _resource_columns,
    _resource_from_json,
    _resource_to_json,
)


class SqliteConnectorRepository(_SyncSqliteConnectorRepository):
    """Restart-durable Connector repository whose awaitable I/O never blocks the event loop."""

    def __init__(self, database_path: Path, *, max_concurrency: int = 4) -> None:
        # Schema creation/migration is explicit synchronous setup work. Runtime calls below are
        # offloaded through the Connector-owned executor after initialization has completed.
        super().__init__(database_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    @property
    def schema_version(self) -> int:
        # Construction already rejects newer schemas and migrates older ones. Avoid turning this
        # synchronous introspection property into another runtime SQLite read on an event loop.
        return _SCHEMA_VERSION

    async def _run_connector_sqlite[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._async_sqlite.run(operation, write=write)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise map_sqlite_error(exc, message) from exc

    async def save_definition(self, definition: ConnectorDefinition) -> ConnectorDefinition:
        payload = _encode(_definition_to_json(definition))

        def operation() -> ConnectorDefinition:
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
                    ON CONFLICT(connector_type_id,version) DO UPDATE SET
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

        return await self._run_connector_sqlite(
            operation,
            message="failed to persist connector definition",
            write=True,
        )

    async def get_definition(self, connector_type_id: str, version: str) -> ConnectorDefinition:
        def operation() -> ConnectorDefinition:
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

        return await self._run_connector_sqlite(
            operation,
            message="failed to read connector definition",
        )

    async def list_definitions(self) -> tuple[ConnectorDefinition, ...]:
        def operation() -> tuple[ConnectorDefinition, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM connector_definitions
                    ORDER BY connector_type_id, version
                    """
                ).fetchall()
            return tuple(_definition_from_json(str(row["payload_json"])) for row in rows)

        return await self._run_connector_sqlite(
            operation,
            message="failed to list connector definitions",
        )

    async def save_connection(self, connection: Connection) -> Connection:
        _require_safe_connection_metadata(connection)
        payload = _encode(_connection_to_json(connection))

        def operation() -> Connection:
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

        return await self._run_connector_sqlite(
            operation,
            message="failed to persist connector connection",
            write=True,
        )

    async def get_connection(self, connection_id: str) -> Connection:
        validate_id(connection_id, "connection")

        def operation() -> Connection:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM connections WHERE connection_id = ?",
                    (connection_id,),
                ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"connection not found: {connection_id}")
            return _connection_from_json(str(row["payload_json"]))

        return await self._run_connector_sqlite(
            operation,
            message="failed to read connector connection",
        )

    async def list_connections(self, *, project_id: str | None = None) -> tuple[Connection, ...]:
        if project_id is not None:
            validate_id(project_id, "project")

        def operation() -> tuple[Connection, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload_json FROM connections ORDER BY connection_id"
                ).fetchall()
            return tuple(_connection_from_json(str(row["payload_json"])) for row in rows)

        items = await self._run_connector_sqlite(
            operation,
            message="failed to list connector connections",
        )
        if project_id is None:
            return items
        return tuple(item for item in items if item.project_id == project_id)

    async def delete_connection(self, connection_id: str) -> None:
        validate_id(connection_id, "connection")

        def operation() -> None:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "DELETE FROM connections WHERE connection_id = ?",
                    (connection_id,),
                )
                if cursor.rowcount == 0:
                    raise ContractError(
                        ErrorCode.NOT_FOUND,
                        f"connection not found: {connection_id}",
                    )

        await self._run_connector_sqlite(
            operation,
            message="failed to delete connector connection",
            write=True,
        )

    async def remove_connection_if_unused(self, connection_id: str) -> None:
        validate_id(connection_id, "connection")

        def operation() -> None:
            with self._connect() as database:
                database.execute("BEGIN IMMEDIATE")
                row = database.execute(
                    "SELECT payload_json FROM connections WHERE connection_id = ?",
                    (connection_id,),
                ).fetchone()
                if row is None:
                    raise ContractError(
                        ErrorCode.NOT_FOUND,
                        f"connection not found: {connection_id}",
                    )
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

        await self._run_connector_sqlite(
            operation,
            message="failed to compensate unused connector connection",
            write=True,
        )

    async def save_external_resource(
        self,
        resource: ExternalResourceReference,
    ) -> ExternalResourceReference:
        def operation() -> ExternalResourceReference:
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
                    (
                        *_resource_columns(canonical, identity),
                        _encode(_resource_to_json(canonical)),
                    ),
                )
            return canonical

        return await self._run_connector_sqlite(
            operation,
            message="failed to persist external connector resource",
            write=True,
        )

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

        def operation() -> tuple[ExternalResourceReference, ...]:
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
                        (
                            *_resource_columns(resource, identity),
                            _encode(_resource_to_json(resource)),
                        ),
                    )
            return tuple(sorted(canonical, key=lambda item: item.id))

        return await self._run_connector_sqlite(
            operation,
            message="failed to rebuild external connector resources",
            write=True,
        )

    async def get_external_resource(self, resource_id: str) -> ExternalResourceReference:
        validate_id(resource_id, "external_resource")

        def operation() -> ExternalResourceReference:
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

        return await self._run_connector_sqlite(
            operation,
            message="failed to read external connector resource",
        )

    async def list_external_resources(
        self,
        *,
        connection_id: str | None = None,
    ) -> tuple[ExternalResourceReference, ...]:
        if connection_id is not None:
            validate_id(connection_id, "connection")

        def operation() -> tuple[ExternalResourceReference, ...]:
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

        return await self._run_connector_sqlite(
            operation,
            message="failed to list external connector resources",
        )

    async def delete_external_resource(self, resource_id: str) -> None:
        validate_id(resource_id, "external_resource")

        def operation() -> None:
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

        await self._run_connector_sqlite(
            operation,
            message="failed to delete external connector resource",
            write=True,
        )

    async def save_checkpoint(self, checkpoint: SyncCheckpoint) -> SyncCheckpoint:
        payload = _encode(_checkpoint_to_json(checkpoint))

        def operation() -> SyncCheckpoint:
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

        return await self._run_connector_sqlite(
            operation,
            message="failed to persist connector sync checkpoint",
            write=True,
        )

    async def get_checkpoint(self, connection_id: str, stream: str) -> SyncCheckpoint | None:
        validate_id(connection_id, "connection")
        if not stream.strip():
            raise ValueError("stream must not be blank")

        def operation() -> SyncCheckpoint | None:
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

        return await self._run_connector_sqlite(
            operation,
            message="failed to read connector sync checkpoint",
        )
