from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    Connection,
    ConnectionStatus,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
    SyncCheckpoint,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, HealthStatus, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.portability import (
    CONNECTION_RESOURCE_TYPE,
    ConnectionImportMutationHandler,
    ConnectionPortableSnapshot,
    DependencyKind,
    ImportContext,
    ResourceSerializerRegistry,
    register_connector_portability_codec,
    snapshot_connection,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference


def _context(actor: ActorIdentity, project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="portability-connector-test",
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        project_id=project_id,
    )


def _source_connection(
    actor: ActorIdentity,
    context: OperationContext,
    secret_ref: SecretReference,
    *,
    endpoint_metadata: dict[str, str] | None = None,
) -> Connection:
    now = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
    return Connection(
        id=new_id("connection"),
        connector_type_id=REFERENCE_CONNECTOR_TYPE,
        connector_version=REFERENCE_CONNECTOR_VERSION,
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        display_name="Portable reference account",
        project_id=context.project_id,
        endpoint_metadata=endpoint_metadata or {"endpoint": "local-fixture"},
        secret_references=(secret_ref,),
        requested_scopes=("read", "write"),
        granted_scopes=("read",),
        enabled=True,
        status=ConnectionStatus.READY,
        health=HealthStatus.HEALTHY,
        created_at=now,
        updated_at=now,
        last_checked_at=now,
        revision=4,
        adapter_metadata=(
            AdapterMetadata(
                namespace="reference.local",
                values={"account_id": "source-private-id"},
            ),
        ),
    )


def _secret_ref(project_id: str) -> SecretReference:
    return SecretReference(
        provider="local-secrets",
        secret_id="portable-reference-token",
        scope=project_id,
        metadata={"purpose": "connector-auth"},
    )


def test_connection_round_trip_excludes_runtime_and_remaps_canonical_scope() -> None:
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    source_project = new_id("project")
    target_project = new_id("project")
    context = _context(actor, source_project)
    source = _source_connection(actor, context, _secret_ref(source_project))
    definition = ReferenceConnectorProvider().definition

    registry = ResourceSerializerRegistry()
    register_connector_portability_codec(registry)
    resource = registry.serialize(snapshot_connection(source, definition))

    assert resource.resource_type == CONNECTION_RESOURCE_TYPE
    assert resource.payload["source_enabled"] is True
    assert resource.payload["activation_required"] is True
    payload = resource.payload["connection"]
    assert isinstance(payload, dict)
    for forbidden in (
        "enabled",
        "status",
        "health",
        "granted_scopes",
        "last_checked_at",
        "adapter_metadata",
    ):
        assert forbidden not in payload

    kinds = {dependency.kind for dependency in resource.dependencies}
    assert DependencyKind.CONNECTOR in kinds
    assert DependencyKind.SECRET in kinds
    assert DependencyKind.RESOURCE in kinds
    connector_requirement = next(
        dependency
        for dependency in resource.dependencies
        if dependency.kind is DependencyKind.CONNECTOR
    )
    assert connector_requirement.identifier == definition.id
    assert connector_requirement.version_constraint == REFERENCE_CONNECTOR_VERSION

    target_connection = new_id("connection")
    decoded = registry.deserialize(
        resource,
        ImportContext(
            {
                ("connection", source.id): target_connection,
                ("project", source_project): target_project,
            }
        ),
    )
    assert isinstance(decoded, ConnectionPortableSnapshot)
    assert decoded.connection.id == target_connection
    assert decoded.connection.project_id == target_project
    assert decoded.connection.enabled is False
    assert decoded.connection.status is ConnectionStatus.DISABLED
    assert decoded.connection.health is HealthStatus.UNAVAILABLE
    assert decoded.connection.granted_scopes == ()
    assert decoded.connection.last_checked_at is None
    assert decoded.connection.adapter_metadata == ()
    assert decoded.source_enabled is True


def test_connection_plaintext_endpoint_secret_is_rejected() -> None:
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    project_id = new_id("project")
    context = _context(actor, project_id)
    source = _source_connection(
        actor,
        context,
        _secret_ref(project_id),
        endpoint_metadata={"api_key": "plaintext-must-not-export"},
    )
    registry = ResourceSerializerRegistry()
    register_connector_portability_codec(registry)

    with pytest.raises(ContractError) as exc_info:
        registry.serialize(snapshot_connection(source, ReferenceConnectorProvider().definition))
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_connection_import_validates_provider_and_stores_non_running_state() -> None:
    async def scenario() -> None:
        actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
        project_id = new_id("project")
        context = _context(actor, project_id)
        secret_ref = _secret_ref(project_id)
        secrets = LocalSecretProvider()
        await secrets.create(
            secret_ref,
            "portable-secret",
            purpose="connector-auth",
            allowed_consumers=("connector.reference",),
            allowed_purposes=("connector-auth",),
        )
        provider = ReferenceConnectorProvider(secrets)
        source = _source_connection(actor, context, secret_ref)
        codec_registry = ResourceSerializerRegistry()
        register_connector_portability_codec(codec_registry)
        resource = codec_registry.serialize(snapshot_connection(source, provider.definition))
        import_context = ImportContext(
            {
                ("connection", source.id): source.id,
                ("project", project_id): project_id,
            }
        )
        decoded = codec_registry.deserialize(resource, import_context)

        repository = InMemoryConnectorRepository()
        service = ConnectorService(repository, ConnectorRegistry())
        await service.register_provider(provider)
        handler = ConnectionImportMutationHandler(service, actor=actor, context=context)
        await handler.preflight(resource, decoded, import_context)
        token = await handler.apply(resource, decoded, import_context)

        assert token == source.id
        stored = await repository.get_connection(source.id)
        assert stored.enabled is False
        assert stored.status is ConnectionStatus.DISABLED
        assert stored.health is HealthStatus.UNAVAILABLE
        assert stored.granted_scopes == ()
        assert stored.last_checked_at is None
        assert stored.adapter_metadata == ()
        assert stored.revision == source.revision
        assert stored.updated_at == source.updated_at

        with pytest.raises(ContractError) as exc_info:
            await service.list_resources(
                source.id,
                "record",
                actor=actor,
                context=context,
            )
        assert exc_info.value.code is ErrorCode.UNAVAILABLE

        await handler.rollback(resource, decoded, token, import_context)
        with pytest.raises(ContractError) as exc_info:
            await repository.get_connection(source.id)
        assert exc_info.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_connection_import_rejects_missing_connector_before_mutation() -> None:
    async def scenario() -> None:
        actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
        project_id = new_id("project")
        context = _context(actor, project_id)
        source = _source_connection(actor, context, _secret_ref(project_id))
        registry = ResourceSerializerRegistry()
        register_connector_portability_codec(registry)
        resource = registry.serialize(
            snapshot_connection(source, ReferenceConnectorProvider().definition)
        )
        import_context = ImportContext(
            {
                ("connection", source.id): source.id,
                ("project", project_id): project_id,
            }
        )
        decoded = registry.deserialize(resource, import_context)
        repository = InMemoryConnectorRepository()
        service = ConnectorService(repository, ConnectorRegistry())
        handler = ConnectionImportMutationHandler(service, actor=actor, context=context)

        with pytest.raises(ContractError) as exc_info:
            await handler.preflight(resource, decoded, import_context)
        assert exc_info.value.code is ErrorCode.UNAVAILABLE
        assert await repository.list_connections() == ()

    asyncio.run(scenario())


def test_connection_import_rejects_implicit_owner_transfer_before_mutation() -> None:
    async def scenario() -> None:
        source_actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
        target_actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
        project_id = new_id("project")
        source_context = _context(source_actor, project_id)
        target_context = _context(target_actor, project_id)
        secret_ref = _secret_ref(project_id)
        source = _source_connection(source_actor, source_context, secret_ref)
        provider = ReferenceConnectorProvider()
        registry = ResourceSerializerRegistry()
        register_connector_portability_codec(registry)
        resource = registry.serialize(snapshot_connection(source, provider.definition))
        import_context = ImportContext(
            {
                ("connection", source.id): source.id,
                ("project", project_id): project_id,
            }
        )
        decoded = registry.deserialize(resource, import_context)
        repository = InMemoryConnectorRepository()
        service = ConnectorService(repository, ConnectorRegistry())
        await service.register_provider(provider)
        handler = ConnectionImportMutationHandler(
            service,
            actor=target_actor,
            context=target_context,
        )

        with pytest.raises(ContractError) as exc_info:
            await handler.preflight(resource, decoded, import_context)
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert await repository.list_connections() == ()

    asyncio.run(scenario())


def test_connection_import_compensation_refuses_sync_history() -> None:
    async def scenario() -> None:
        actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
        project_id = new_id("project")
        context = _context(actor, project_id)
        source = _source_connection(actor, context, _secret_ref(project_id))
        disabled = snapshot_connection(source, ReferenceConnectorProvider().definition).connection
        repository = InMemoryConnectorRepository()
        await repository.save_connection(disabled)
        await repository.save_checkpoint(
            SyncCheckpoint(connection_id=disabled.id, stream="records")
        )

        with pytest.raises(ContractError) as exc_info:
            await repository.remove_connection_if_unused(disabled.id)
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert await repository.get_connection(disabled.id) == disabled

    asyncio.run(scenario())
