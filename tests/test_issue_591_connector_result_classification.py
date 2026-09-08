from __future__ import annotations

import asyncio

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    REFERENCE_ACTION,
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    Connection,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorRegistry,
    EgressConnectorService,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.contracts import DataClassification, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference


class _StrongerResultProvider(ReferenceConnectorProvider):
    async def invoke_action(self, invocation: ConnectorActionInvocation) -> ConnectorActionResult:
        result = await super().invoke_action(invocation)
        return ConnectorActionResult(
            invocation_id=result.invocation_id,
            output=result.output,
            resource_refs=result.resource_refs,
            adapter_metadata=result.adapter_metadata,
            classification=DataClassification.SECRET,
        )


def _runtime(
    *,
    stronger_result: bool = False,
) -> tuple[EgressConnectorService, ActorIdentity, OperationContext, Connection]:
    project_id = new_id("project")
    actor = ActorIdentity(new_id("user"), ActorType.HUMAN)
    context = OperationContext(
        correlation_id="connector-result-classification-591",
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        project_id=project_id,
    )
    secrets = LocalSecretProvider()
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id="reference-connector-token-591",
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
    provider = (
        _StrongerResultProvider(secrets)
        if stronger_result
        else ReferenceConnectorProvider(secrets)
    )
    service = EgressConnectorService(InMemoryConnectorRepository(), ConnectorRegistry())
    asyncio.run(service.register_provider(provider))
    connection = Connection(
        id=new_id("connection"),
        connector_type_id=REFERENCE_CONNECTOR_TYPE,
        connector_version=REFERENCE_CONNECTOR_VERSION,
        owner_type=actor.actor_type.value,
        owner_id=actor.actor_id,
        display_name="Reference account",
        project_id=project_id,
        secret_references=(secret_ref,),
        requested_scopes=("read", "write"),
    )
    connection = asyncio.run(
        service.create_connection(connection, actor=actor, context=context)
    )
    return service, actor, context, connection


def test_action_result_inherits_confidential_input_classification() -> None:
    service, actor, context, connection = _runtime()

    result = asyncio.run(
        service.invoke_action(
            connection.id,
            REFERENCE_ACTION,
            {"message": "derived result"},
            invocation_id="connector-result-591",
            actor=actor,
            context=context,
            data_classification=DataClassification.CONFIDENTIAL,
        )
    )

    assert result.classification is DataClassification.CONFIDENTIAL


def test_provider_can_strengthen_but_not_weaken_result_classification() -> None:
    service, actor, context, connection = _runtime(stronger_result=True)

    result = asyncio.run(
        service.invoke_action(
            connection.id,
            REFERENCE_ACTION,
            {"message": "derived result"},
            invocation_id="connector-result-stronger-591",
            actor=actor,
            context=context,
            data_classification=DataClassification.CONFIDENTIAL,
        )
    )

    assert result.classification is DataClassification.SECRET


def test_resource_list_and_read_preserve_originating_classification() -> None:
    service, actor, context, connection = _runtime()

    resources = asyncio.run(
        service.list_resources(
            connection.id,
            "record",
            actor=actor,
            context=context,
            query={"prefix": "a"},
            data_classification=DataClassification.CONFIDENTIAL,
        )
    )
    assert len(resources) == 1
    assert resources[0].classification is DataClassification.CONFIDENTIAL

    refreshed = asyncio.run(
        service.read_resource(
            connection.id,
            resources[0],
            actor=actor,
            context=context,
            data_classification=DataClassification.INTERNAL,
        )
    )
    assert refreshed.classification is DataClassification.CONFIDENTIAL


def test_sync_resources_and_events_inherit_request_classification() -> None:
    service, actor, context, connection = _runtime()

    result = asyncio.run(
        service.synchronize(
            connection.id,
            "records",
            actor=actor,
            context=context,
            data_classification=DataClassification.RESTRICTED,
        )
    )

    assert result.resources
    assert result.events
    assert all(
        resource.classification is DataClassification.RESTRICTED
        for resource in result.resources
    )
    assert all(event.classification is DataClassification.RESTRICTED for event in result.events)
