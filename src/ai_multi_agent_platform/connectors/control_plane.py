"""Registration-based Control Plane extension for canonical connector resources."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference, infer_actor_identity

from .models import Connection, ConnectorDefinition
from .service import ConnectorService

type ConnectorControlPlaneActorResolver = Callable[[RequestContext], ActorIdentity]


def default_actor_resolver(context: RequestContext) -> ActorIdentity:
    """Translate the already-authenticated Control Plane actor into canonical #15 identity."""

    if context.actor.actor_type is None:
        return infer_actor_identity(context.actor.principal_ref)
    try:
        actor_type = ActorType(context.actor.actor_type)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor type is not recognized by connector security",
        ) from exc
    return ActorIdentity(context.actor.principal_ref, actor_type)


class ConnectorDefinitionResourceService(ResourceService):
    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        definitions = await self._connectors.repository.list_definitions()
        return tuple(_definition_resource(definition) for definition in definitions)

    async def get_resource(
        self, context: RequestContext, resource_id: str
    ) -> dict[str, JsonValue]:
        del context
        for definition in await self._connectors.repository.list_definitions():
            if definition.id == resource_id:
                return _definition_resource(definition)
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"connector definition not found: {resource_id}",
        )


class ConnectionResourceService(ResourceService):
    def __init__(self, connectors: ConnectorService) -> None:
        self._connectors = connectors

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context
        project_id = None if query.filters is None else query.filters.get("project_id")
        connections = await self._connectors.repository.list_connections(project_id=project_id)
        return tuple(_connection_resource(connection) for connection in connections)

    async def get_resource(
        self, context: RequestContext, resource_id: str
    ) -> dict[str, JsonValue]:
        del context
        connection = await self._connectors.repository.get_connection(resource_id)
        return _connection_resource(connection)


def register_connector_control_plane(
    control_plane: ControlPlane,
    connectors: ConnectorService,
    *,
    actor_resolver: ConnectorControlPlaneActorResolver = default_actor_resolver,
) -> None:
    """Expose connector lifecycle without creating an action-invocation bypass.

    External actions intentionally are not registered as generic Control Plane commands.
    They remain available only through the canonical #12 capability pipeline.
    """

    control_plane.register_resource_service(
        "connector-definitions", ConnectorDefinitionResourceService(connectors)
    )
    control_plane.register_resource_service("connections", ConnectionResourceService(connectors))

    async def create_connection(
        request: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        connection = _connection_from_payload(resource_ref, payload)
        actor = actor_resolver(request)
        created = await connectors.create_connection(
            connection,
            actor=actor,
            context=_operation_context(request, connection.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return _connection_resource(created)

    async def enable_connection(
        request: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        updated = await connectors.set_enabled(
            resource_ref,
            True,
            actor=actor_resolver(request),
            context=_operation_context(request, existing.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return _connection_resource(updated)

    async def disable_connection(
        request: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        updated = await connectors.set_enabled(
            resource_ref,
            False,
            actor=actor_resolver(request),
            context=_operation_context(request, existing.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return _connection_resource(updated)

    async def remove_connection(
        request: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        await connectors.remove_connection(
            resource_ref,
            actor=actor_resolver(request),
            context=_operation_context(request, existing.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return {"id": resource_ref, "removed": True}

    async def check_health(
        request: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        existing = await connectors.repository.get_connection(resource_ref)
        checked = await connectors.check_health(
            resource_ref,
            actor=actor_resolver(request),
            context=_operation_context(request, existing.project_id),
        )
        return _connection_resource(checked)

    async def synchronize(
        request: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        stream = _required_string(payload, "stream")
        result = await connectors.synchronize(
            resource_ref,
            stream,
            actor=actor_resolver(request),
            context=_operation_context(request, existing.project_id),
        )
        return {
            "connection_id": result.checkpoint.connection_id,
            "stream": result.checkpoint.stream,
            "cursor": result.checkpoint.cursor,
            "status": result.checkpoint.status.value,
            "last_successful_sync": (
                result.checkpoint.last_successful_sync.isoformat()
                if result.checkpoint.last_successful_sync is not None
                else None
            ),
            "resource_refs": [resource.to_dict() for resource in result.resources],
            "events": [
                {
                    "id": event.id,
                    "connector_type_id": event.connector_type_id,
                    "connection_id": event.connection_id,
                    "event_type": event.event_type,
                    "native_reference": {
                        "namespace": event.native_reference.namespace,
                        "native_id": event.native_reference.native_id,
                    },
                    "schema_version": event.schema_version,
                    "dedupe_key": event.dedupe_key,
                    "received_at": event.received_at.isoformat(),
                    "project_id": event.project_id,
                    "resource_id": event.resource_id,
                    "verified": event.verified,
                    "provenance": dict(event.provenance),
                }
                for event in result.events
            ],
        }

    control_plane.register_command("connection.create", create_connection)
    control_plane.register_command("connection.enable", enable_connection)
    control_plane.register_command("connection.disable", disable_connection)
    control_plane.register_command("connection.remove", remove_connection)
    control_plane.register_command("connection.health", check_health)
    control_plane.register_command("connector.sync", synchronize)


def _operation_context(request: RequestContext, project_id: str | None) -> OperationContext:
    return OperationContext(
        correlation_id=request.correlation_id,
        owner_type=request.actor.owner_type,
        owner_id=request.actor.owner_id,
        project_id=project_id,
    )


def _definition_resource(definition: ConnectorDefinition) -> dict[str, JsonValue]:
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
        "configuration_schema": dict(definition.configuration_schema),
        "health_semantics": dict(definition.health_semantics),
    }


def _connection_resource(connection: Connection) -> dict[str, JsonValue]:
    return {
        "id": connection.id,
        "connector_type_id": connection.connector_type_id,
        "connector_version": connection.connector_version,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "display_name": connection.display_name,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
        "endpoint_metadata": dict(connection.endpoint_metadata),
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
    }


def _connection_from_payload(resource_ref: str, payload: dict[str, JsonValue]) -> Connection:
    allowed = {
        "connector_type_id",
        "connector_version",
        "owner_type",
        "owner_id",
        "display_name",
        "project_id",
        "organization_id",
        "endpoint_metadata",
        "secret_references",
        "requested_scopes",
        "approval_id",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "connection create payload contains unsupported fields",
            details={"fields": cast(JsonValue, unexpected)},
        )
    raw_endpoint = payload.get("endpoint_metadata", {})
    if not isinstance(raw_endpoint, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "endpoint_metadata must be an object")
    raw_refs = payload.get("secret_references", [])
    if not isinstance(raw_refs, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "secret_references must be an array")
    references = tuple(_secret_reference(item) for item in raw_refs)
    raw_scopes = payload.get("requested_scopes", [])
    if not isinstance(raw_scopes, list) or any(not isinstance(item, str) for item in raw_scopes):
        raise ContractError(ErrorCode.INVALID_REQUEST, "requested_scopes must be a string array")
    return Connection(
        id=resource_ref,
        connector_type_id=_required_string(payload, "connector_type_id"),
        connector_version=_required_string(payload, "connector_version"),
        owner_type=_required_string(payload, "owner_type"),
        owner_id=_required_string(payload, "owner_id"),
        display_name=_required_string(payload, "display_name"),
        project_id=_optional_string(payload.get("project_id"), "project_id"),
        organization_id=_optional_string(payload.get("organization_id"), "organization_id"),
        endpoint_metadata=cast(dict[str, JsonValue], raw_endpoint),
        secret_references=references,
        requested_scopes=tuple(cast(list[str], raw_scopes)),
    )


def _secret_reference(value: JsonValue) -> SecretReference:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "secret reference must be an object")
    allowed = {"provider", "secret_id", "scope", "version", "metadata"}
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "secret reference contains unsupported fields",
            details={"fields": cast(JsonValue, unexpected)},
        )
    raw_metadata = value.get("metadata", {})
    if not isinstance(raw_metadata, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "secret reference metadata must be an object")
    return SecretReference(
        provider=_required_string(value, "provider"),
        secret_id=_required_string(value, "secret_id"),
        scope=_required_string(value, "scope"),
        version=_optional_string(value.get("version"), "version"),
        metadata=cast(dict[str, JsonValue], raw_metadata),
    )


def _required_string(payload: dict[str, JsonValue], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-blank string")
    return value


def _optional_string(value: JsonValue, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a non-blank string")
    return value
