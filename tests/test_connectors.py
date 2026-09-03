from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    REFERENCE_ACTION,
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    Connection,
    ConnectionStatus,
    ConnectorCapabilityProvider,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    SecretReference,
)


def _context(actor: ActorIdentity, project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="connector-test",
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        project_id=project_id,
    )


def _runtime(
    *,
    gate: AuthorizationGate | None = None,
) -> tuple[
    ConnectorService,
    ReferenceConnectorProvider,
    LocalSecretProvider,
    ActorIdentity,
    OperationContext,
    SecretReference,
]:
    project_id = new_id("project")
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    context = _context(actor, project_id)
    secrets = LocalSecretProvider()
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id="reference-connector-token",
        scope=project_id,
    )
    asyncio.run(
        secrets.create(
            secret_ref,
            "test-secret",
            purpose="connector-auth",
            allowed_consumers=("connector.reference",),
            allowed_purposes=("connector-auth",),
        )
    )
    provider = ReferenceConnectorProvider(secrets)
    service = ConnectorService(
        InMemoryConnectorRepository(),
        ConnectorRegistry(),
        authorization_gate=gate,
    )
    asyncio.run(service.register_provider(provider))
    return service, provider, secrets, actor, context, secret_ref


def _connection(
    actor: ActorIdentity,
    context: OperationContext,
    secret_ref: SecretReference,
) -> Connection:
    return Connection(
        id=new_id("connection"),
        connector_type_id=REFERENCE_CONNECTOR_TYPE,
        connector_version=REFERENCE_CONNECTOR_VERSION,
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        display_name="Reference account",
        project_id=context.project_id,
        secret_references=(secret_ref,),
        requested_scopes=("read", "write"),
    )


def test_create_configure_disable_and_reenable_connection() -> None:
    service, _, _, actor, context, secret_ref = _runtime()
    connection = _connection(actor, context, secret_ref)

    created = asyncio.run(service.create_connection(connection, actor=actor, context=context))
    assert created.status is ConnectionStatus.READY
    assert created.health is HealthStatus.HEALTHY
    assert created.granted_scopes == ("read", "write")

    disabled = asyncio.run(service.set_enabled(created.id, False, actor=actor, context=context))
    assert disabled.status is ConnectionStatus.DISABLED
    assert disabled.health is HealthStatus.UNAVAILABLE

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.list_resources(
                created.id,
                "record",
                actor=actor,
                context=context,
            )
        )
    assert exc_info.value.code is ErrorCode.UNAVAILABLE

    enabled = asyncio.run(service.set_enabled(created.id, True, actor=actor, context=context))
    assert enabled.status is ConnectionStatus.READY


def test_missing_and_invalid_credentials_fail_canonically() -> None:
    service, _, _, actor, context, _ = _runtime()
    no_secret = Connection(
        id=new_id("connection"),
        connector_type_id=REFERENCE_CONNECTOR_TYPE,
        connector_version=REFERENCE_CONNECTOR_VERSION,
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        display_name="Missing credential",
        project_id=context.project_id,
    )
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(service.create_connection(no_secret, actor=actor, context=context))
    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION

    missing_ref = SecretReference(
        provider="local-secrets",
        secret_id="does-not-exist",
        scope=context.project_id or "project",
    )
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.create_connection(
                _connection(actor, context, missing_ref),
                actor=actor,
                context=context,
            )
        )
    assert exc_info.value.code is ErrorCode.NOT_FOUND


def test_health_failure_and_recovery_are_queryable() -> None:
    service, provider, _, actor, context, secret_ref = _runtime()
    created = asyncio.run(
        service.create_connection(
            _connection(actor, context, secret_ref),
            actor=actor,
            context=context,
        )
    )

    provider.set_health(HealthStatus.UNAVAILABLE)
    failed = asyncio.run(service.check_health(created.id, actor=actor, context=context))
    assert failed.health is HealthStatus.UNAVAILABLE
    assert failed.status is ConnectionStatus.ERROR

    provider.set_health(HealthStatus.HEALTHY)
    recovered = asyncio.run(service.check_health(created.id, actor=actor, context=context))
    assert recovered.health is HealthStatus.HEALTHY
    assert recovered.status is ConnectionStatus.READY


def test_resource_list_read_and_external_serialization_preserve_namespaced_identity() -> None:
    service, _, _, actor, context, secret_ref = _runtime()
    created = asyncio.run(
        service.create_connection(
            _connection(actor, context, secret_ref),
            actor=actor,
            context=context,
        )
    )
    resources = asyncio.run(
        service.list_resources(
            created.id,
            "record",
            actor=actor,
            context=context,
            query={"prefix": "a"},
        )
    )
    assert len(resources) == 1
    resource = resources[0]
    refreshed = asyncio.run(
        service.read_resource(
            created.id,
            resource,
            actor=actor,
            context=context,
        )
    )
    assert refreshed.id == resource.id
    payload = refreshed.to_dict()
    assert payload["connection_id"] == created.id
    assert payload["native_reference"] == {
        "namespace": REFERENCE_CONNECTOR_TYPE,
        "native_id": "alpha",
    }


def test_connector_action_uses_canonical_capability_pipeline() -> None:
    service, _, _, actor, context, secret_ref = _runtime()
    connection = asyncio.run(
        service.create_connection(
            _connection(actor, context, secret_ref),
            actor=actor,
            context=context,
        )
    )
    registry = CapabilityRegistry()
    bridge = ConnectorCapabilityProvider(
        service,
        actor_resolver=lambda operation: actor,
    )
    asyncio.run(registry.register_provider(bridge))
    invoker = CapabilityInvoker(registry)
    request = CapabilityInvocation(
        invocation_id="connector-action-1",
        capability_id=REFERENCE_ACTION,
        arguments={
            "connection_id": connection.id,
            "message": "hello",
        },
        context=context,
        trace=InvocationTrace(
            correlation_id=context.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=context.project_id,
        ),
    )
    result = asyncio.run(invoker.invoke(request))
    assert result.output == {
        "echo": "hello",
        "connection_id": connection.id,
    }
    assert result.provider_id == "platform.connector-bridge"


def test_permission_denial_blocks_connector_action() -> None:
    project_id = new_id("project")
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    policy = LocalPrincipalPolicy(
        principal_ref=actor.actor_id,
        actor_types=frozenset({ActorType.HUMAN}),
        allowed_actions=frozenset(
            {
                AuthorizationAction.MANAGE_INTEGRATIONS,
                AuthorizationAction.READ,
            }
        ),
        resource_types=frozenset({ResourceType.CONNECTOR}),
        project_ids=frozenset({project_id}),
    )
    gate = AuthorizationGate(LocalAuthorizationProvider((policy,)))
    context = _context(actor, project_id)
    secrets = LocalSecretProvider()
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id="permission-test-token",
        scope=project_id,
    )
    asyncio.run(
        secrets.create(
            secret_ref,
            "test-secret",
            purpose="connector-auth",
            allowed_consumers=("connector.reference",),
            allowed_purposes=("connector-auth",),
        )
    )
    provider = ReferenceConnectorProvider(secrets)
    service = ConnectorService(
        InMemoryConnectorRepository(),
        ConnectorRegistry(),
        authorization_gate=gate,
    )
    asyncio.run(service.register_provider(provider))
    connection = asyncio.run(
        service.create_connection(
            _connection(actor, context, secret_ref),
            actor=actor,
            context=context,
        )
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.invoke_action(
                connection.id,
                REFERENCE_ACTION,
                {"message": "blocked"},
                invocation_id="denied-action",
                actor=actor,
                context=context,
            )
        )
    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_external_event_dedupe_metadata_and_sync_checkpoint_resume() -> None:
    service, _, _, actor, context, secret_ref = _runtime()
    connection = asyncio.run(
        service.create_connection(
            _connection(actor, context, secret_ref),
            actor=actor,
            context=context,
        )
    )
    first = asyncio.run(
        service.synchronize(
            connection.id,
            "records",
            actor=actor,
            context=context,
        )
    )
    assert len(first.resources) == 2
    assert len(first.events) == 2
    assert len({event.dedupe_key for event in first.events}) == 2
    assert all(event.verified for event in first.events)
    assert all(event.provenance["source"] == "reference-local" for event in first.events)
    assert first.checkpoint.cursor == "2"

    resumed = asyncio.run(
        service.synchronize(
            connection.id,
            "records",
            actor=actor,
            context=context,
        )
    )
    assert resumed.resources == ()
    assert resumed.events == ()
    assert resumed.checkpoint.cursor == "2"


def test_adapter_removal_does_not_redefine_historical_external_reference() -> None:
    service, _, _, actor, context, secret_ref = _runtime()
    connection = asyncio.run(
        service.create_connection(
            _connection(actor, context, secret_ref),
            actor=actor,
            context=context,
        )
    )
    resource = asyncio.run(
        service.list_resources(
            connection.id,
            "record",
            actor=actor,
            context=context,
        )
    )[0]
    historical = resource.to_dict()

    service.registry.unregister(
        REFERENCE_CONNECTOR_TYPE,
        REFERENCE_CONNECTOR_VERSION,
    )
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.list_resources(
                connection.id,
                "record",
                actor=actor,
                context=context,
            )
        )
    assert exc_info.value.code is ErrorCode.UNAVAILABLE
    assert resource.to_dict() == historical


def test_reference_connector_requires_no_automation_broker_or_plugin_loader() -> None:
    service, _, _, actor, context, secret_ref = _runtime()
    connection = asyncio.run(
        service.create_connection(
            _connection(actor, context, secret_ref),
            actor=actor,
            context=context,
        )
    )
    result = asyncio.run(
        service.invoke_action(
            connection.id,
            REFERENCE_ACTION,
            {"message": "local"},
            invocation_id="local-only",
            actor=actor,
            context=context,
        )
    )
    assert result.output == {
        "echo": "local",
        "connection_id": connection.id,
    }
