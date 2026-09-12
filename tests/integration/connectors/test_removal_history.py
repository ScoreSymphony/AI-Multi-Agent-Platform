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


def test_connection_removal_preserves_historical_external_reference() -> None:
    project_id = new_id("project")
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    context = OperationContext(
        correlation_id="connector-removal-history",
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        project_id=project_id,
    )
    secrets = LocalSecretProvider()
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id="connector-removal-token",
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
                display_name="Removal-history fixture",
                project_id=project_id,
                secret_references=(secret_ref,),
            ),
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

    asyncio.run(
        service.remove_connection(
            connection.id,
            actor=actor,
            context=context,
        )
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(service.repository.get_connection(connection.id))
    assert exc_info.value.code is ErrorCode.NOT_FOUND
    assert resource.to_dict() == historical

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.read_resource(
                connection.id,
                resource,
                actor=actor,
                context=context,
            )
        )
    assert exc_info.value.code is ErrorCode.NOT_FOUND
