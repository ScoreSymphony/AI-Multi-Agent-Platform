from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    Connection,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference


def test_event_subscription_rejects_embedded_credentials_before_provider_dispatch() -> None:
    project_id = new_id("project")
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    context = OperationContext(
        correlation_id="connector-subscription-security",
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        project_id=project_id,
    )
    secrets = LocalSecretProvider()
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id="connector-subscription-token",
        scope=project_id,
    )
    asyncio.run(
        secrets.create(
            secret_ref,
            "secret-material",
            purpose="connector-auth",
            allowed_consumers=("connector.reference",),
            allowed_purposes=("connector-auth",),
        )
    )
    service = ConnectorService(InMemoryConnectorRepository(), ConnectorRegistry())
    asyncio.run(service.register_provider(ReferenceConnectorProvider(secrets)))
    connection = asyncio.run(
        service.create_connection(
            Connection(
                id=new_id("connection"),
                connector_type_id=REFERENCE_CONNECTOR_TYPE,
                connector_version=REFERENCE_CONNECTOR_VERSION,
                owner_type=actor.actor_type.value,
                owner_id=actor.actor_id,
                display_name="Subscription security fixture",
                project_id=project_id,
                secret_references=(secret_ref,),
            ),
            actor=actor,
            context=context,
        )
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.subscribe_events(
                connection.id,
                ("record.changed",),
                configuration={"webhook_secret": "plaintext-must-not-cross-boundary"},
                actor=actor,
                context=context,
            )
        )
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.subscribe_events(
                connection.id,
                ("record.changed",),
                configuration={"callback": "local-fixture"},
                actor=actor,
                context=context,
            )
        )
    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert exc_info.value.details["operation"] == "event.subscribe"
