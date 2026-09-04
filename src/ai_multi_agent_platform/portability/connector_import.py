"""Rollback-safe Connection import for issue #79."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_multi_agent_platform.connectors.models import Connection, ConnectionStatus
from ai_multi_agent_platform.connectors.service import ConnectorService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus, OperationContext
from ai_multi_agent_platform.security import ActorIdentity

from .connector_codecs import (
    CONNECTION_RESOURCE_TYPE,
    ConnectionPortableSnapshot,
    ConnectorRequirementMetadata,
)
from .models import PortableResource
from .registry import ImportContext


@dataclass(frozen=True, slots=True)
class ConnectionImportPolicy:
    """Explicit exceptions to conservative Connection identity portability defaults."""

    allow_owner_transfer: bool = False
    allow_organization_transfer: bool = False


class ConnectionImportMutationHandler:
    resource_type = CONNECTION_RESOURCE_TYPE

    def __init__(
        self,
        service: ConnectorService,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        policy: ConnectionImportPolicy | None = None,
        approval_id: str | None = None,
    ) -> None:
        self._service = service
        self._actor = actor
        self._context = context
        self._policy = policy or ConnectionImportPolicy()
        self._approval_id = approval_id

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_snapshot(value)
        connection = snapshot.connection

        if connection.project_id is not None and connection.project_id != self._context.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "portable Connection project scope does not match the import context",
                details={"connection_id": connection.id, "project_id": connection.project_id},
            )
        if not self._policy.allow_owner_transfer and (
            connection.owner_type != self._actor.actor_type.value
            or connection.owner_id != self._actor.actor_id
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "portable Connection owner cannot be transferred implicitly",
                details={"connection_id": connection.id},
            )
        if (
            connection.organization_id is not None
            and not self._policy.allow_organization_transfer
            and connection.organization_id != self._actor.organization_id
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "portable Connection organization cannot be transferred implicitly",
                details={"connection_id": connection.id},
            )

        provider = self._service.registry.resolve(
            connection.connector_type_id,
            connection.connector_version,
        )
        target_requirement = ConnectorRequirementMetadata.from_definition(provider.definition)
        if target_requirement != snapshot.connector_requirement:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "destination connector contract does not match portable Connection requirement",
                provider_id=provider.descriptor.provider_id,
                details={
                    "connection_id": connection.id,
                    "connector_definition_id": snapshot.connector_requirement.definition_id,
                },
            )

        try:
            await self._service.repository.get_connection(connection.id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            f"Connection appeared after import preview: {connection.id}",
            details={"connection_id": connection.id},
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_snapshot(value)
        connection = snapshot.connection
        created: Connection | None = None
        try:
            created = await self._service.create_connection(
                connection,
                actor=self._actor,
                context=self._context,
                approval_id=self._approval_id,
            )
            normalized = replace(
                created,
                granted_scopes=(),
                enabled=False,
                status=ConnectionStatus.DISABLED,
                health=HealthStatus.UNAVAILABLE,
                created_at=connection.created_at,
                updated_at=connection.updated_at,
                last_checked_at=None,
                revision=connection.revision,
                adapter_metadata=(),
            )
            stored = await self._service.repository.save_connection(normalized)
            return stored.id
        except Exception:
            if created is not None:
                try:
                    await self._service.repository.delete_connection(created.id)
                except ContractError as cleanup_error:
                    if cleanup_error.code is not ErrorCode.NOT_FOUND:
                        raise ContractError(
                            ErrorCode.BACKEND_ERROR,
                            (
                                "Connection import failed and partial mutation could not be "
                                "compensated"
                            ),
                            details={"connection_id": created.id},
                        ) from cleanup_error
            raise

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Connection rollback token must be the imported Connection ID",
            )
        await self._service.repository.remove_connection_if_unused(token)


def _require_snapshot(value: object) -> ConnectionPortableSnapshot:
    if not isinstance(value, ConnectionPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Connection mutation handler received the wrong decoded resource type",
        )
    return value
