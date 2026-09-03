"""Canonical connector lifecycle and security enforcement service."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationOutcome,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus, JsonValue, OperationContext
from ai_multi_agent_platform.security import (
    ActorIdentity,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    redact_sensitive,
)

from .models import (
    Connection,
    ConnectionStatus,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorResourceQuery,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalNativeReference,
    ExternalResourceReference,
    SyncMode,
)
from .provider import ConnectorProvider
from .registry import ConnectorRegistry
from .repository import ConnectorRepository


class ConnectorService:
    """Own canonical connection lifecycle while adapters remain replaceable."""

    def __init__(
        self,
        repository: ConnectorRepository,
        registry: ConnectorRegistry,
        *,
        authorization_gate: AuthorizationGate | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.authorization_gate = authorization_gate

    async def register_provider(self, provider: ConnectorProvider) -> None:
        definition = self.registry.register(provider)
        await self.repository.save_definition(definition)

    async def create_connection(
        self,
        connection: Connection,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
    ) -> Connection:
        self._validate_scope(connection, context)
        provider = self.registry.resolve(connection.connector_type_id, connection.connector_version)
        self._validate_safe_adapter_metadata(connection, provider)
        definition = provider.definition
        if definition.authentication_requirements and not connection.secret_references:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "connector requires credentials but connection has no secret references",
                provider_id=provider.descriptor.provider_id,
            )
        await self._authorize(
            connection,
            actor=actor,
            context=context,
            action=AuthorizationAction.MANAGE_INTEGRATIONS,
            side_effect="connection_create",
            approval_id=approval_id,
            payload=_connection_security_payload(connection),
        )
        normalized = await provider.validate_connection(connection, context)
        self._validate_provider_connection(connection, normalized, provider)
        return await self.repository.save_connection(normalized)

    async def get_connection(
        self,
        connection_id: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
    ) -> Connection:
        connection = await self.repository.get_connection(connection_id)
        self._validate_scope(connection, context)
        await self._authorize(
            connection,
            actor=actor,
            context=context,
            action=AuthorizationAction.READ,
            side_effect="connection_read",
            payload={"connection_id": connection.id},
        )
        return connection

    async def list_connections(
        self,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        project_id: str | None = None,
    ) -> tuple[Connection, ...]:
        connections = await self.repository.list_connections(project_id=project_id)
        visible: list[Connection] = []
        for connection in connections:
            try:
                self._validate_scope(connection, context)
            except ContractError as exc:
                if exc.code is ErrorCode.FORBIDDEN:
                    continue
                raise
            if await self._can_read_connection(connection, actor=actor, context=context):
                visible.append(connection)
        return tuple(visible)

    async def set_enabled(
        self,
        connection_id: str,
        enabled: bool,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
    ) -> Connection:
        connection = await self.repository.get_connection(connection_id)
        self._validate_scope(connection, context)
        await self._authorize(
            connection,
            actor=actor,
            context=context,
            action=AuthorizationAction.MANAGE_INTEGRATIONS,
            side_effect="connection_enable" if enabled else "connection_disable",
            approval_id=approval_id,
            payload={"connection_id": connection.id, "enabled": enabled},
        )
        now = datetime.now(UTC)
        updated = replace(
            connection,
            enabled=enabled,
            status=(ConnectionStatus.CONFIGURING if enabled else ConnectionStatus.DISABLED),
            health=HealthStatus.UNKNOWN if enabled else HealthStatus.UNAVAILABLE,
            updated_at=now,
            revision=connection.revision + 1,
        )
        if enabled:
            provider = self.registry.resolve(updated.connector_type_id, updated.connector_version)
            normalized = await provider.validate_connection(updated, context)
            self._validate_provider_connection(updated, normalized, provider)
            updated = normalized
        return await self.repository.save_connection(updated)

    async def remove_connection(
        self,
        connection_id: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
    ) -> None:
        connection = await self.repository.get_connection(connection_id)
        self._validate_scope(connection, context)
        await self._authorize(
            connection,
            actor=actor,
            context=context,
            action=AuthorizationAction.DELETE,
            side_effect="connection_remove",
            approval_id=approval_id,
            payload={"connection_id": connection.id, "remove": True},
        )
        await self.repository.delete_connection(connection_id)

    async def check_health(
        self,
        connection_id: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
    ) -> Connection:
        connection = await self.repository.get_connection(connection_id)
        self._validate_scope(connection, context)
        await self._authorize(
            connection,
            actor=actor,
            context=context,
            action=AuthorizationAction.READ,
            side_effect="health_read",
            payload={"connection_id": connection.id},
        )
        if not connection.enabled:
            return connection
        provider = self.registry.resolve(connection.connector_type_id, connection.connector_version)
        health = await provider.connection_health(connection, context)
        status = (
            ConnectionStatus.READY
            if health is HealthStatus.HEALTHY
            else ConnectionStatus.DEGRADED
            if health is HealthStatus.DEGRADED
            else ConnectionStatus.ERROR
        )
        now = datetime.now(UTC)
        updated = replace(
            connection,
            health=health,
            status=status,
            last_checked_at=now,
            updated_at=now,
            revision=connection.revision + 1,
        )
        return await self.repository.save_connection(updated)

    async def list_resources(
        self,
        connection_id: str,
        resource_type: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        query: dict[str, JsonValue] | None = None,
    ) -> tuple[ExternalResourceReference, ...]:
        query_payload = query or {}
        connection, provider = await self._usable_connection(
            connection_id,
            actor=actor,
            context=context,
            action=AuthorizationAction.READ,
            side_effect="external_read",
            payload={"resource_type": resource_type, "query": query_payload},
        )
        if resource_type not in provider.definition.resource_types:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"connector does not expose resource type {resource_type!r}",
                provider_id=provider.descriptor.provider_id,
            )
        resources = await provider.list_resources(
            ConnectorResourceQuery(
                connection_id=connection.id,
                resource_type=resource_type,
                context=context,
                query=query_payload,
            )
        )
        for resource in resources:
            self._validate_resource_binding(resource, connection, provider)
        return resources

    async def read_resource(
        self,
        connection_id: str,
        resource: ExternalResourceReference,
        *,
        actor: ActorIdentity,
        context: OperationContext,
    ) -> ExternalResourceReference:
        connection, provider = await self._usable_connection(
            connection_id,
            actor=actor,
            context=context,
            action=AuthorizationAction.READ,
            side_effect="external_read",
            payload={"external_resource_id": resource.id},
        )
        self._validate_resource_binding(resource, connection, provider)
        refreshed = await provider.read_resource(connection, resource, context)
        self._validate_resource_binding(refreshed, connection, provider)
        if refreshed.id != resource.id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector changed canonical external-resource identity during read",
                provider_id=provider.descriptor.provider_id,
            )
        return refreshed

    async def invoke_action(
        self,
        connection_id: str,
        action: str,
        arguments: dict[str, JsonValue],
        *,
        invocation_id: str,
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
    ) -> ConnectorActionResult:
        connection, provider = await self._usable_connection(
            connection_id,
            actor=actor,
            context=context,
            action=AuthorizationAction.EXECUTE,
            side_effect="external_action",
            approval_id=approval_id,
            capability_ref=action,
            payload={
                "connection_id": connection_id,
                "action": action,
                "arguments": arguments,
                "invocation_id": invocation_id,
            },
        )
        if action not in provider.definition.actions:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"connector action {action!r} is not declared",
                provider_id=provider.descriptor.provider_id,
            )
        result = await provider.invoke_action(
            ConnectorActionInvocation(
                invocation_id=invocation_id,
                connection_id=connection.id,
                action=action,
                arguments=arguments,
                context=context,
            )
        )
        if result.invocation_id != invocation_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector returned mismatched invocation_id",
                provider_id=provider.descriptor.provider_id,
            )
        for resource in result.resource_refs:
            self._validate_resource_binding(resource, connection, provider)
        return result

    async def subscribe_events(
        self,
        connection_id: str,
        event_types: tuple[str, ...],
        *,
        configuration: dict[str, JsonValue],
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
    ) -> ExternalNativeReference:
        if not event_types or any(not event_type.strip() for event_type in event_types):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "connector event subscription requires non-blank event types",
            )
        connection, provider = await self._usable_connection(
            connection_id,
            actor=actor,
            context=context,
            action=AuthorizationAction.MANAGE_INTEGRATIONS,
            side_effect="external_event_subscription",
            approval_id=approval_id,
            payload={
                "connection_id": connection_id,
                "event_types": list(event_types),
                "configuration": configuration,
            },
        )
        unsupported = sorted(set(event_types) - set(provider.definition.event_types))
        if unsupported:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "connector subscription requests undeclared event types",
                provider_id=provider.descriptor.provider_id,
                details={"event_types": unsupported},
            )
        return await provider.subscribe_events(
            connection,
            event_types,
            configuration=configuration,
            context=context,
        )

    async def unsubscribe_events(
        self,
        connection_id: str,
        subscription: ExternalNativeReference,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
    ) -> None:
        connection, provider = await self._usable_connection(
            connection_id,
            actor=actor,
            context=context,
            action=AuthorizationAction.MANAGE_INTEGRATIONS,
            side_effect="external_event_unsubscription",
            approval_id=approval_id,
            payload={
                "connection_id": connection_id,
                "subscription": {
                    "namespace": subscription.namespace,
                    "native_id": subscription.native_id,
                },
            },
        )
        await provider.unsubscribe_events(connection, subscription, context=context)

    async def synchronize(
        self,
        connection_id: str,
        stream: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        mode: SyncMode = SyncMode.INCREMENTAL,
    ) -> ConnectorSyncResult:
        connection, provider = await self._usable_connection(
            connection_id,
            actor=actor,
            context=context,
            action=AuthorizationAction.READ,
            side_effect="external_sync",
            payload={
                "connection_id": connection_id,
                "stream": stream,
                "mode": mode.value,
            },
        )
        existing_checkpoint = await self.repository.get_checkpoint(connection.id, stream)
        checkpoint = None if mode is SyncMode.REBUILD else existing_checkpoint
        result = await provider.synchronize(
            ConnectorSyncRequest(
                connection_id=connection.id,
                stream=stream,
                context=context,
                checkpoint=checkpoint,
                mode=mode,
            )
        )
        if result.checkpoint.connection_id != connection.id or result.checkpoint.stream != stream:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector returned checkpoint for another connection or stream",
                provider_id=provider.descriptor.provider_id,
            )
        for resource in result.resources:
            self._validate_resource_binding(resource, connection, provider)
        for event in result.events:
            if event.connection_id != connection.id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "connector event is bound to another connection",
                    provider_id=provider.descriptor.provider_id,
                )
            if event.connector_type_id != connection.connector_type_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "connector event type provenance does not match connection",
                    provider_id=provider.descriptor.provider_id,
                )
        await self.repository.save_checkpoint(result.checkpoint)
        return result

    async def _usable_connection(
        self,
        connection_id: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        action: AuthorizationAction,
        side_effect: str | None = None,
        approval_id: str | None = None,
        capability_ref: str | None = None,
        payload: dict[str, JsonValue] | None = None,
    ) -> tuple[Connection, ConnectorProvider]:
        connection = await self.repository.get_connection(connection_id)
        self._validate_scope(connection, context)
        await self._authorize(
            connection,
            actor=actor,
            context=context,
            action=action,
            side_effect=side_effect,
            approval_id=approval_id,
            capability_ref=capability_ref,
            payload=payload,
        )
        if not connection.enabled or connection.status is ConnectionStatus.DISABLED:
            raise ContractError(ErrorCode.UNAVAILABLE, "connection is disabled")
        provider = self.registry.resolve(connection.connector_type_id, connection.connector_version)
        return connection, provider

    async def _can_read_connection(
        self,
        connection: Connection,
        *,
        actor: ActorIdentity,
        context: OperationContext,
    ) -> bool:
        if self.authorization_gate is None:
            return True
        proposal = self._authorization_proposal(
            connection,
            actor=actor,
            context=context,
            action=AuthorizationAction.READ,
            side_effect="connection_list",
            payload={"connection_id": connection.id},
        )
        decision = normalize_authorization_decision(
            await self.authorization_gate.provider.authorize(
                proposal.context.to_request(requested_action_digest=proposal.digest)
            )
        )
        return decision.outcome is AuthorizationOutcome.ALLOW

    async def _authorize(
        self,
        connection: Connection,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        action: AuthorizationAction,
        side_effect: str | None,
        approval_id: str | None = None,
        capability_ref: str | None = None,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        if self.authorization_gate is None:
            return
        proposed = self._authorization_proposal(
            connection,
            actor=actor,
            context=context,
            action=action,
            side_effect=side_effect,
            capability_ref=capability_ref,
            payload=payload,
        )
        await self.authorization_gate.enforce(proposed, approval_id=approval_id)

    @staticmethod
    def _authorization_proposal(
        connection: Connection,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        action: AuthorizationAction,
        side_effect: str | None,
        capability_ref: str | None = None,
        payload: dict[str, JsonValue] | None = None,
    ) -> ProposedAction:
        scoped_context = replace(
            context,
            owner_type=connection.owner_type,
            owner_id=connection.owner_id,
            project_id=connection.project_id or context.project_id,
        )
        return ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=action,
                resource_type=ResourceType.CONNECTOR,
                resource_id=connection.id,
                operation=scoped_context,
                organization_id=connection.organization_id,
                workspace_id=None,
                capability_ref=capability_ref,
                side_effect=side_effect,
                security_labels=("external_integration",),
            ),
            payload=payload or _connection_security_payload(connection),
        )

    @staticmethod
    def _validate_scope(connection: Connection, context: OperationContext) -> None:
        if (
            context.project_id is not None
            and connection.project_id is not None
            and context.project_id != connection.project_id
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "connection is outside the operation project scope",
            )

    @staticmethod
    def _validate_provider_connection(
        original: Connection,
        normalized: Connection,
        provider: ConnectorProvider,
    ) -> None:
        immutable = (
            "id",
            "connector_type_id",
            "connector_version",
            "owner_type",
            "owner_id",
            "project_id",
            "organization_id",
            "secret_references",
        )
        if any(getattr(original, field) != getattr(normalized, field) for field in immutable):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "connector provider changed canonical connection identity/scope",
                provider_id=provider.descriptor.provider_id,
            )
        ConnectorService._validate_safe_adapter_metadata(normalized, provider)

    @staticmethod
    def _validate_safe_adapter_metadata(
        connection: Connection,
        provider: ConnectorProvider,
    ) -> None:
        for metadata in connection.adapter_metadata:
            values = dict(metadata.values)
            if redact_sensitive(values) != values:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "connection adapter metadata must not contain embedded credentials",
                    provider_id=provider.descriptor.provider_id,
                    details={"namespace": metadata.namespace},
                )

    @staticmethod
    def _validate_resource_binding(
        resource: ExternalResourceReference,
        connection: Connection,
        provider: ConnectorProvider,
    ) -> None:
        if resource.connection_id != connection.id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "external resource reference is bound to another connection",
                provider_id=provider.descriptor.provider_id,
            )
        if resource.resource_type not in provider.definition.resource_types:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "external resource type is not declared by connector",
                provider_id=provider.descriptor.provider_id,
            )


def _connection_security_payload(connection: Connection) -> dict[str, JsonValue]:
    """Bind approvals to the exact safe connection configuration, never secret material."""

    return {
        "connection_id": connection.id,
        "connector_type_id": connection.connector_type_id,
        "connector_version": connection.connector_version,
        "owner_type": connection.owner_type,
        "owner_id": connection.owner_id,
        "project_id": connection.project_id,
        "organization_id": connection.organization_id,
        "display_name": connection.display_name,
        "endpoint_metadata": dict(connection.endpoint_metadata),
        "secret_references": [reference.to_dict() for reference in connection.secret_references],
        "requested_scopes": list(connection.requested_scopes),
        "enabled": connection.enabled,
    }
