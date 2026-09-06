"""Registration-based Control Plane extension for canonical connector resources."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue, OperationContext
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    SecretReference,
    infer_actor_identity,
    redact_sensitive,
)

from .external_resources import (
    EXTERNAL_RESOURCE_DETACH_COMMAND,
    external_resource_projection,
    register_external_resource_control_plane,
)
from .models import Connection, ConnectorDefinition, SyncMode
from .service import ConnectorService

type ConnectorControlPlaneActorResolver = Callable[[RequestContext], ActorIdentity]
type ConnectorHealthEventSink = Callable[[Connection, Connection], Awaitable[None]]

CONNECTOR_DEFINITION_COLLECTION = "connector-definitions"
CONNECTOR_DEFINITION_TYPE = "connector-definition"
CONNECTION_COLLECTION = "connections"
CONNECTION_TYPE = "connection"
CONNECTOR_COMMANDS = (
    "connection.create",
    "connection.enable",
    "connection.disable",
    "connection.remove",
    "connection.health",
    "connector.sync",
    EXTERNAL_RESOURCE_DETACH_COMMAND,
)


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

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        del context
        for definition in await self._connectors.repository.list_definitions():
            if definition.id == resource_id:
                return _definition_resource(definition)
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"connector definition not found: {resource_id}",
        )


class ConnectionResourceService(ResourceService):
    def __init__(
        self,
        connectors: ConnectorService,
        actor_resolver: ConnectorControlPlaneActorResolver,
        *,
        include_organization_scoped_search: bool = False,
    ) -> None:
        self._connectors = connectors
        self._actor_resolver = actor_resolver
        self._include_organization_scoped_search = include_organization_scoped_search

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        project_id = None if query.filters is None else query.filters.get("project_id")
        connections = await self._connectors.list_connections(
            actor=self._actor_resolver(context),
            context=_operation_context(context, project_id),
            project_id=project_id,
        )
        return tuple(_connection_resource(connection) for connection in connections)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate safe derived-search metadata without inventing a system actor.

        The normal collection list remains actor-filtered by ``ConnectorService``. A full
        Search rebuild instead enumerates canonical repository records and relies on the
        Control Plane's per-result authorization before counts or results become visible.
        Organization-scoped Connections are enumerated only when the composed Control
        Plane advertises the #87 live membership visibility guard.
        """

        connections = await self._connectors.repository.list_connections()
        return tuple(
            _connection_search_resource(connection)
            for connection in connections
            if connection.organization_id is None or self._include_organization_scoped_search
        )

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        connection = await self._connectors.get_connection(
            resource_id,
            actor=self._actor_resolver(context),
            context=_operation_context(context, None),
        )
        return _connection_resource(connection)


def register_connector_control_plane(
    control_plane: ControlPlane,
    connectors: ConnectorService,
    *,
    actor_resolver: ConnectorControlPlaneActorResolver = default_actor_resolver,
    health_event_sink: ConnectorHealthEventSink | None = None,
) -> None:
    """Expose connector lifecycle without creating an action-invocation bypass.

    External actions intentionally are not registered as generic Control Plane commands.
    They remain available only through the canonical #12 capability pipeline. A Control Plane
    may expose ``connector_health_event_sink`` as a provider-neutral best-effort observer; this
    keeps #44 authoritative while allowing #75 to project health attention without a hard import.
    """

    if health_event_sink is None:
        discovered_sink = getattr(control_plane, "connector_health_event_sink", None)
        if callable(discovered_sink):
            health_event_sink = cast(ConnectorHealthEventSink, discovered_sink)

    control_plane.register_resource_service(
        CONNECTOR_DEFINITION_COLLECTION, ConnectorDefinitionResourceService(connectors)
    )
    control_plane.register_resource_service(
        CONNECTION_COLLECTION,
        ConnectionResourceService(
            connectors,
            actor_resolver,
            include_organization_scoped_search=bool(
                getattr(control_plane, "organization_search_visibility_available", False)
            ),
        ),
    )
    register_external_resource_control_plane(
        control_plane, connectors, actor_resolver=actor_resolver
    )

    async def create_connection(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, CONNECTION_COLLECTION)
        connection = _connection_from_payload(new_id("connection"), payload)
        actor = actor_resolver(context)
        created = await connectors.create_connection(
            connection,
            actor=actor,
            context=_operation_context(context, connection.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return _connection_resource(created)

    async def enable_connection(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        updated = await connectors.set_enabled(
            resource_ref,
            True,
            actor=actor_resolver(context),
            context=_operation_context(context, existing.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return _connection_resource(updated)

    async def disable_connection(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        updated = await connectors.set_enabled(
            resource_ref,
            False,
            actor=actor_resolver(context),
            context=_operation_context(context, existing.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return _connection_resource(updated)

    async def remove_connection(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        await connectors.remove_connection(
            resource_ref,
            actor=actor_resolver(context),
            context=_operation_context(context, existing.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
        )
        return {"id": resource_ref, "removed": True}

    async def check_health(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        existing = await connectors.repository.get_connection(resource_ref)
        checked = await connectors.check_health(
            resource_ref,
            actor=actor_resolver(context),
            context=_operation_context(context, existing.project_id),
        )
        if health_event_sink is not None and (
            checked.health != existing.health or checked.status != existing.status
        ):
            try:
                await health_event_sink(existing, checked)
            except Exception:
                # #44 already owns and persisted the authoritative Connection health transition.
                # Downstream attention must never falsify a successful health check.
                pass
        return _connection_resource(checked)

    async def synchronize(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        existing = await connectors.repository.get_connection(resource_ref)
        stream = _required_string(payload, "stream")
        mode = _sync_mode(payload.get("mode"))
        result = await connectors.synchronize(
            resource_ref,
            stream,
            actor=actor_resolver(context),
            context=_operation_context(context, existing.project_id),
            mode=mode,
        )
        return {
            "connection_id": result.checkpoint.connection_id,
            "stream": result.checkpoint.stream,
            "mode": mode.value,
            "cursor": result.checkpoint.cursor,
            "status": result.checkpoint.status.value,
            "last_successful_sync": (
                result.checkpoint.last_successful_sync.isoformat()
                if result.checkpoint.last_successful_sync is not None
                else None
            ),
            "resource_refs": [
                external_resource_projection(resource, existing) for resource in result.resources
            ],
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
        "type": CONNECTOR_DEFINITION_TYPE,
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
        "source_metadata": _adapter_metadata(definition.adapter_metadata),
    }


def _connection_resource(connection: Connection) -> dict[str, JsonValue]:
    endpoint_metadata = redact_sensitive(dict(connection.endpoint_metadata))
    if not isinstance(endpoint_metadata, dict):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "connection endpoint metadata cannot be serialized safely",
        )
    return {
        "id": connection.id,
        "type": CONNECTION_TYPE,
        "connector_type_id": connection.connector_type_id,
        "connector_version": connection.connector_version,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "display_name": connection.display_name,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
        "endpoint_metadata": endpoint_metadata,
        "account_metadata": _adapter_metadata(connection.adapter_metadata),
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


def _connection_search_resource(connection: Connection) -> dict[str, JsonValue]:
    """Return the intentionally small, non-secret Connection search projection."""

    return {
        "id": connection.id,
        "type": CONNECTION_TYPE,
        "connector_type_id": connection.connector_type_id,
        "connector_version": connection.connector_version,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "display_name": connection.display_name,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
        "requested_scopes": list(connection.requested_scopes),
        "granted_scopes": list(connection.granted_scopes),
        "enabled": connection.enabled,
        "status": connection.status.value,
        "health": connection.health.value,
        "updated_at": connection.updated_at.isoformat(),
        "revision": connection.revision,
    }


def _adapter_metadata(metadata: tuple[AdapterMetadata, ...]) -> list[JsonValue]:
    serialized: list[JsonValue] = []
    for item in metadata:
        values = redact_sensitive(dict(item.values))
        if not isinstance(values, dict):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector adapter metadata cannot be serialized safely",
            )
        serialized.append({"namespace": item.namespace, "values": values})
    return serialized


def _connection_from_payload(connection_id: str, payload: dict[str, JsonValue]) -> Connection:
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
    endpoint_metadata = raw_endpoint
    if redact_sensitive(endpoint_metadata) != endpoint_metadata:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "endpoint_metadata must not contain embedded credentials; use secret_references",
        )
    raw_refs = payload.get("secret_references", [])
    if not isinstance(raw_refs, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "secret_references must be an array")
    references = tuple(_secret_reference(item) for item in raw_refs)
    raw_scopes = payload.get("requested_scopes", [])
    if not isinstance(raw_scopes, list) or any(not isinstance(item, str) for item in raw_scopes):
        raise ContractError(ErrorCode.INVALID_REQUEST, "requested_scopes must be a string array")
    return Connection(
        id=connection_id,
        connector_type_id=_required_string(payload, "connector_type_id"),
        connector_version=_required_string(payload, "connector_version"),
        owner_type=_required_string(payload, "owner_type"),
        owner_id=_required_string(payload, "owner_id"),
        display_name=_required_string(payload, "display_name"),
        project_id=_optional_string(payload.get("project_id"), "project_id"),
        organization_id=_optional_string(payload.get("organization_id"), "organization_id"),
        endpoint_metadata=endpoint_metadata,
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
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "secret reference metadata must be an object"
        )
    return SecretReference(
        provider=_required_string(value, "provider"),
        secret_id=_required_string(value, "secret_id"),
        scope=_required_string(value, "scope"),
        version=_optional_string(value.get("version"), "version"),
        metadata=raw_metadata,
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


def _sync_mode(value: JsonValue) -> SyncMode:
    if value is None:
        return SyncMode.INCREMENTAL
    if not isinstance(value, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, "sync mode must be a string")
    try:
        return SyncMode(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "sync mode must be incremental, resync, or rebuild",
        ) from exc


def _require_collection(resource_ref: str, expected: str) -> None:
    if resource_ref != expected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"resource_ref must be {expected!r} for create command",
        )
