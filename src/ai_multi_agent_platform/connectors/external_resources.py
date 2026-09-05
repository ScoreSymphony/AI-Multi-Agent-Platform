"""Control Plane projection for durable canonical Connector external-resource references."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast
from urllib.parse import urlsplit

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.security import ActorIdentity

from .models import Connection, ExternalResourceReference
from .service import ConnectorService

EXTERNAL_RESOURCE_COLLECTION = "external-resources"
EXTERNAL_RESOURCE_TYPE = "external-resource"
EXTERNAL_RESOURCE_DETACH_COMMAND = "external-resource.detach"

type ActorResolver = Callable[[RequestContext], ActorIdentity]


class OrganizationVisibility(Protocol):
    async def actor_can_discover_organization(
        self, *, actor_id: str, organization_id: str
    ) -> bool: ...


class ExternalResourceResourceService(ResourceService):
    """Read durable Connector wrappers without crawling provider state."""

    def __init__(
        self,
        control_plane: ControlPlane,
        connectors: ConnectorService,
        actor_resolver: ActorResolver,
        *,
        include_organization_scoped_search: bool,
    ) -> None:
        self._control_plane = control_plane
        self._connectors = connectors
        self._actor_resolver = actor_resolver
        self._include_organization_scoped_search = include_organization_scoped_search

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        filters = query.filters or {}
        project_id = filters.get("project_id")
        connection_id = filters.get("connection_id")
        resource_type = filters.get("resource_type")

        gate_visible_connections = await self._connectors.list_connections(
            actor=self._actor_resolver(context),
            context=_operation_context(context, project_id),
            project_id=project_id,
        )
        visible: dict[str, Connection] = {}
        for connection in gate_visible_connections:
            if await self._connection_visible(
                context,
                connection,
                action="external-resource:list",
                resource_ref=EXTERNAL_RESOURCE_COLLECTION,
            ):
                visible[connection.id] = connection

        resources = await self._connectors.repository.list_external_resources(
            connection_id=connection_id
        )
        return tuple(
            _external_resource_resource(resource, visible[resource.connection_id])
            for resource in resources
            if resource.connection_id in visible
            and (resource_type is None or resource.resource_type == resource_type)
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        resource = await self._resource_or_not_found(resource_id)
        connection = await self._authorized_connection_or_not_found(
            context,
            resource,
            action="external-resource:read",
        )
        return _external_resource_resource(resource, connection)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate only durable local wrappers for actor-independent Search rebuilds."""

        resources = await self._connectors.repository.list_external_resources()
        projections: list[dict[str, JsonValue]] = []
        for resource in resources:
            try:
                connection = await self._connectors.repository.get_connection(
                    resource.connection_id
                )
            except ContractError as exc:
                if exc.code is ErrorCode.NOT_FOUND:
                    continue
                raise
            if (
                connection.organization_id is not None
                and not self._include_organization_scoped_search
            ):
                continue
            projections.append(_external_resource_search_resource(resource, connection))
        return tuple(projections)

    async def detach_resource(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Detach only the canonical wrapper; never delete provider-native state."""

        if payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "external-resource detach does not accept remote mutation arguments",
            )
        resource = await self._resource_or_not_found(resource_ref)
        await self._authorized_connection_or_not_found(
            context,
            resource,
            action=EXTERNAL_RESOURCE_DETACH_COMMAND,
        )
        await self._connectors.repository.delete_external_resource(resource_ref)
        return {
            "id": resource_ref,
            "detached": True,
            "remote_deleted": False,
        }

    async def _resource_or_not_found(
        self, resource_id: str
    ) -> ExternalResourceReference:
        try:
            return await self._connectors.repository.get_external_resource(resource_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "external resource not found",
                ) from exc
            raise

    async def _authorized_connection_or_not_found(
        self,
        context: RequestContext,
        resource: ExternalResourceReference,
        *,
        action: str,
    ) -> Connection:
        try:
            connection = await self._connectors.repository.get_connection(resource.connection_id)
            connection = await self._connectors.get_connection(
                connection.id,
                actor=self._actor_resolver(context),
                context=_operation_context(context, connection.project_id),
            )
            if not await self._connection_visible(
                context,
                connection,
                action=action,
                resource_ref=resource.id,
            ):
                raise ContractError(ErrorCode.FORBIDDEN, "external resource is hidden")
            return connection
        except ContractError as exc:
            if exc.code in {
                ErrorCode.FORBIDDEN,
                ErrorCode.UNAUTHORIZED,
                ErrorCode.NOT_FOUND,
            }:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "external resource not found",
                ) from exc
            raise

    async def _connection_visible(
        self,
        context: RequestContext,
        connection: Connection,
        *,
        action: str,
        resource_ref: str,
    ) -> bool:
        if connection.organization_id is not None:
            organization_visibility = cast(
                OrganizationVisibility | None,
                getattr(self._control_plane, "_organization_service", None),
            )
            if organization_visibility is None:
                return False
            if not await organization_visibility.actor_can_discover_organization(
                actor_id=context.actor.principal_ref,
                organization_id=connection.organization_id,
            ):
                return False
        return await self._control_plane._allowed(
            context,
            action,
            resource_ref,
            owner_type=connection.owner_type,
            owner_id=connection.owner_id,
            project_id=connection.project_id,
        )


def register_external_resource_control_plane(
    control_plane: ControlPlane,
    connectors: ConnectorService,
    *,
    actor_resolver: ActorResolver,
) -> None:
    include_organization_scoped_search = bool(
        getattr(control_plane, "organization_search_visibility_available", False)
    )
    resource_service = ExternalResourceResourceService(
        control_plane,
        connectors,
        actor_resolver,
        include_organization_scoped_search=include_organization_scoped_search,
    )
    control_plane.register_resource_service(
        EXTERNAL_RESOURCE_COLLECTION,
        resource_service,
    )

    async def detach(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await resource_service.detach_resource(context, resource_ref, payload)

    control_plane.register_command(EXTERNAL_RESOURCE_DETACH_COMMAND, detach)


def _operation_context(request: RequestContext, project_id: str | None) -> OperationContext:
    return OperationContext(
        correlation_id=request.correlation_id,
        owner_type=request.actor.owner_type,
        owner_id=request.actor.owner_id,
        project_id=project_id,
    )


def _external_resource_resource(
    resource: ExternalResourceReference,
    connection: Connection,
) -> dict[str, JsonValue]:
    """Safe northbound metadata; arbitrary provider metadata/provenance stays private."""

    return {
        "id": resource.id,
        "type": EXTERNAL_RESOURCE_TYPE,
        "connection_id": resource.connection_id,
        "resource_type": resource.resource_type,
        "native_reference": {
            "namespace": resource.native_reference.namespace,
            "native_id": resource.native_reference.native_id,
        },
        "canonical_url": _safe_canonical_url(resource.canonical_url),
        "version": resource.version,
        "revision": resource.revision,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
    }


def _external_resource_search_resource(
    resource: ExternalResourceReference,
    connection: Connection,
) -> dict[str, JsonValue]:
    """Minimal Search projection; canonical ID remains the only platform identity."""

    return {
        "id": resource.id,
        "type": EXTERNAL_RESOURCE_TYPE,
        "connection_id": resource.connection_id,
        "resource_type": resource.resource_type,
        "native_namespace": resource.native_reference.namespace,
        "native_id": resource.native_reference.native_id,
        "external_version": resource.version,
        "external_revision": resource.revision,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
    }


def _safe_canonical_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.query or parsed.fragment:
        return None
    return value


__all__ = [
    "EXTERNAL_RESOURCE_COLLECTION",
    "EXTERNAL_RESOURCE_DETACH_COMMAND",
    "EXTERNAL_RESOURCE_TYPE",
    "ExternalResourceResourceService",
    "register_external_resource_control_plane",
]
