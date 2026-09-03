"""Connector definition, connection and sync-state persistence seams."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .models import Connection, ConnectorDefinition, SyncCheckpoint


class ConnectorRepository(ABC):
    @abstractmethod
    async def save_definition(self, definition: ConnectorDefinition) -> ConnectorDefinition: ...

    @abstractmethod
    async def get_definition(self, connector_type_id: str, version: str) -> ConnectorDefinition: ...

    @abstractmethod
    async def list_definitions(self) -> tuple[ConnectorDefinition, ...]: ...

    @abstractmethod
    async def save_connection(self, connection: Connection) -> Connection: ...

    @abstractmethod
    async def get_connection(self, connection_id: str) -> Connection: ...

    @abstractmethod
    async def list_connections(
        self, *, project_id: str | None = None
    ) -> tuple[Connection, ...]: ...

    @abstractmethod
    async def delete_connection(self, connection_id: str) -> None: ...

    @abstractmethod
    async def save_checkpoint(self, checkpoint: SyncCheckpoint) -> SyncCheckpoint: ...

    @abstractmethod
    async def get_checkpoint(self, connection_id: str, stream: str) -> SyncCheckpoint | None: ...


class InMemoryConnectorRepository(ConnectorRepository):
    """Deterministic dependency-free reference repository."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], ConnectorDefinition] = {}
        self._connections: dict[str, Connection] = {}
        self._checkpoints: dict[tuple[str, str], SyncCheckpoint] = {}

    async def save_definition(self, definition: ConnectorDefinition) -> ConnectorDefinition:
        self._definitions[(definition.connector_type_id, definition.version)] = definition
        return definition

    async def get_definition(self, connector_type_id: str, version: str) -> ConnectorDefinition:
        try:
            return self._definitions[(connector_type_id, version)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"connector definition not found: {connector_type_id!r} {version!r}",
            ) from exc

    async def list_definitions(self) -> tuple[ConnectorDefinition, ...]:
        return tuple(
            self._definitions[key]
            for key in sorted(self._definitions, key=lambda item: (item[0], item[1]))
        )

    async def save_connection(self, connection: Connection) -> Connection:
        current = self._connections.get(connection.id)
        if current is not None and connection.revision < current.revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "connection revision must not move backwards",
            )
        self._connections[connection.id] = connection
        return connection

    async def get_connection(self, connection_id: str) -> Connection:
        validate_id(connection_id, "connection")
        try:
            return self._connections[connection_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"connection not found: {connection_id}"
            ) from exc

    async def list_connections(
        self, *, project_id: str | None = None
    ) -> tuple[Connection, ...]:
        if project_id is not None:
            validate_id(project_id, "project")
        items = (
            connection
            for connection in self._connections.values()
            if project_id is None or connection.project_id == project_id
        )
        return tuple(sorted(items, key=lambda item: item.id))

    async def delete_connection(self, connection_id: str) -> None:
        validate_id(connection_id, "connection")
        if connection_id not in self._connections:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"connection not found: {connection_id}"
            )
        del self._connections[connection_id]
        for key in tuple(self._checkpoints):
            if key[0] == connection_id:
                del self._checkpoints[key]

    async def save_checkpoint(self, checkpoint: SyncCheckpoint) -> SyncCheckpoint:
        self._checkpoints[(checkpoint.connection_id, checkpoint.stream)] = checkpoint
        return checkpoint

    async def get_checkpoint(self, connection_id: str, stream: str) -> SyncCheckpoint | None:
        validate_id(connection_id, "connection")
        if not stream.strip():
            raise ValueError("stream must not be blank")
        return self._checkpoints.get((connection_id, stream))
