from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    Connection,
    ConnectorEvent,
    ConnectorRegistry,
    ConnectorService,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalNativeReference,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference


class _CredentialInjectingProvider(ReferenceConnectorProvider):
    async def validate_connection(
        self, connection: Connection, context: OperationContext
    ) -> Connection:
        normalized = await super().validate_connection(connection, context)
        return replace(
            normalized,
            endpoint_metadata={"api_token": "provider-injected-plaintext"},
        )


class _ScopeEscalatingProvider(ReferenceConnectorProvider):
    async def validate_connection(
        self, connection: Connection, context: OperationContext
    ) -> Connection:
        normalized = await super().validate_connection(connection, context)
        return replace(
            normalized,
            granted_scopes=normalized.requested_scopes + ("admin",),
        )


class _UndeclaredSyncEventProvider(ReferenceConnectorProvider):
    async def synchronize(self, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        result = await super().synchronize(request)
        assert result.events
        invalid = replace(result.events[0], event_type="undeclared.changed")
        return replace(result, events=(invalid,) + result.events[1:])


class _WrongProjectSyncEventProvider(ReferenceConnectorProvider):
    async def synchronize(self, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        result = await super().synchronize(request)
        assert result.events
        invalid = replace(result.events[0], project_id=new_id("project"))
        return replace(result, events=(invalid,) + result.events[1:])


class _NormalizingProvider(ReferenceConnectorProvider):
    async def normalize_external_event(
        self,
        connection: Connection,
        native_event: Mapping[str, JsonValue],
        context: OperationContext,
    ) -> ConnectorEvent:
        native_id = native_event.get("native_id")
        if not isinstance(native_id, str):
            raise ContractError(ErrorCode.INVALID_REQUEST, "native_id must be a string")
        return ConnectorEvent(
            id=new_id("connector_event"),
            connector_type_id=REFERENCE_CONNECTOR_TYPE,
            connection_id=connection.id,
            event_type="record.changed",
            native_reference=ExternalNativeReference(
                namespace=REFERENCE_CONNECTOR_TYPE,
                native_id=native_id,
            ),
            schema_version="1.0",
            dedupe_key=f"normalized:{native_id}",
            received_at=datetime.now(UTC),
            project_id=connection.project_id,
            verified=True,
            provenance={"source": "test-normalizer"},
            payload={"native_id": native_id},
        )


async def _build_runtime(
    provider_type: type[ReferenceConnectorProvider] = ReferenceConnectorProvider,
    *,
    credential: str = "valid-reference-credential",
) -> tuple[
    ConnectorService,
    ReferenceConnectorProvider,
    ActorIdentity,
    OperationContext,
    Connection,
]:
    project_id = new_id("project")
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    context = OperationContext(
        correlation_id="connector-hardening",
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        project_id=project_id,
    )
    secrets = LocalSecretProvider()
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id=new_id("secret"),
        scope=project_id,
    )
    await secrets.create(
        secret_ref,
        credential,
        purpose="connector-auth",
        allowed_consumers=("connector.reference",),
        allowed_purposes=("connector-auth",),
    )
    provider = provider_type(secrets)
    service = ConnectorService(InMemoryConnectorRepository(), ConnectorRegistry())
    await service.register_provider(provider)
    connection = Connection(
        id=new_id("connection"),
        connector_type_id=REFERENCE_CONNECTOR_TYPE,
        connector_version=REFERENCE_CONNECTOR_VERSION,
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        display_name="Connector hardening fixture",
        project_id=project_id,
        secret_references=(secret_ref,),
        requested_scopes=("read",),
    )
    return service, provider, actor, context, connection


def test_existing_but_invalid_credential_is_rejected() -> None:
    async def scenario() -> None:
        service, _, actor, context, connection = await _build_runtime(
            credential="invalid-reference-credential"
        )
        with pytest.raises(ContractError) as exc_info:
            await service.create_connection(connection, actor=actor, context=context)
        assert exc_info.value.code is ErrorCode.UNAUTHORIZED
        with pytest.raises(ContractError) as missing:
            await service.repository.get_connection(connection.id)
        assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_provider_cannot_inject_plaintext_credentials_after_validation() -> None:
    async def scenario() -> None:
        service, _, actor, context, connection = await _build_runtime(_CredentialInjectingProvider)
        with pytest.raises(ContractError) as exc_info:
            await service.create_connection(connection, actor=actor, context=context)
        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
        with pytest.raises(ContractError) as missing:
            await service.repository.get_connection(connection.id)
        assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_provider_cannot_grant_unrequested_connection_scopes() -> None:
    async def scenario() -> None:
        service, _, actor, context, connection = await _build_runtime(_ScopeEscalatingProvider)
        with pytest.raises(ContractError) as exc_info:
            await service.create_connection(connection, actor=actor, context=context)
        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "provider_type",
    [_UndeclaredSyncEventProvider, _WrongProjectSyncEventProvider],
)
def test_sync_rejects_invalid_event_contracts(
    provider_type: type[ReferenceConnectorProvider],
) -> None:
    async def scenario() -> None:
        service, _, actor, context, pending = await _build_runtime(provider_type)
        connection = await service.create_connection(pending, actor=actor, context=context)
        with pytest.raises(ContractError) as exc_info:
            await service.synchronize(
                connection.id,
                "records",
                actor=actor,
                context=context,
            )
        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
        assert await service.repository.get_checkpoint(connection.id, "records") is None

    asyncio.run(scenario())


def test_inbound_event_normalization_uses_canonical_event_validation() -> None:
    async def scenario() -> None:
        service, _, actor, context, pending = await _build_runtime(_NormalizingProvider)
        connection = await service.create_connection(pending, actor=actor, context=context)
        event = await service.normalize_external_event(
            connection.id,
            {"native_id": "event-1"},
            actor=actor,
            context=context,
        )
        assert event.connection_id == connection.id
        assert event.connector_type_id == REFERENCE_CONNECTOR_TYPE
        assert event.event_type == "record.changed"
        assert event.project_id == connection.project_id
        assert event.verified is True

    asyncio.run(scenario())
