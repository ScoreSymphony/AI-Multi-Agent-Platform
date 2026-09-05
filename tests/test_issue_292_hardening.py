from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorRegistry,
    ConnectorService,
    ExternalResourceReference,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
    SyncMode,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

PRIVATE_METADATA = "issue-292-private-resource-metadata"
PRIVATE_PROVENANCE = "issue-292-private-resource-provenance"
PRIVATE_URL_TOKEN = "issue-292-private-url-token"
PRINCIPAL = "user:issue-292-hardening"
OWNER_ID = "issue-292-hardening"


class PrivateReferenceConnectorProvider(ReferenceConnectorProvider):
    def _resource(self, connection_id: str, native_id: str) -> ExternalResourceReference:
        resource = super()._resource(connection_id, native_id)
        return replace(
            resource,
            canonical_url=(
                f"https://example.invalid/records/{native_id}?token={PRIVATE_URL_TOKEN}"
            ),
            metadata={"private_payload": PRIVATE_METADATA},
            provenance={"provider_private": PRIVATE_PROVENANCE},
        )


def _context(project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-292-hardening",
        owner_type="user",
        owner_id=OWNER_ID,
        project_id=project_id,
    )


def _actor() -> ActorIdentity:
    return ActorIdentity(PRINCIPAL, ActorType.HUMAN)


def _headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-issue-292-hardening",
        "X-Correlation-Id": "correlation-issue-292-hardening",
        "X-Principal-Ref": PRINCIPAL,
        "X-Owner-Type": "user",
        "X-Owner-Id": OWNER_ID,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _create_connection(
    service: ConnectorService,
    secrets: LocalSecretProvider,
    *,
    project_id: str,
) -> Connection:
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id=f"issue-292-hardening-{project_id}",
        scope=project_id,
    )
    await secrets.create(
        secret_ref,
        "issue-292-hardening-secret",
        purpose="connector-auth",
        allowed_consumers=("connector.reference",),
        allowed_purposes=("connector-auth",),
    )
    return await service.create_connection(
        Connection(
            id=new_id("connection"),
            connector_type_id="reference.local",
            connector_version="1.0",
            owner_type="user",
            owner_id=OWNER_ID,
            display_name="Issue 292 hardening connection",
            project_id=project_id,
            secret_references=(secret_ref,),
            requested_scopes=("read",),
            enabled=True,
        ),
        actor=_actor(),
        context=_context(project_id),
    )


def test_sync_reuses_canonical_external_resource_ids_after_provider_recreation() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        secrets = LocalSecretProvider()
        repository = InMemoryConnectorRepository()

        provider_one = ReferenceConnectorProvider(secrets)
        service_one = ConnectorService(repository, ConnectorRegistry())
        await service_one.register_provider(provider_one)
        connection = await _create_connection(service_one, secrets, project_id=project_id)

        first = await service_one.synchronize(
            connection.id,
            "records",
            actor=_actor(),
            context=_context(project_id),
        )
        first_ids = {
            resource.native_reference.native_id: resource.id for resource in first.resources
        }
        assert set(first_ids) == {"alpha", "beta"}

        provider_two = ReferenceConnectorProvider(secrets)
        service_two = ConnectorService(repository, ConnectorRegistry())
        await service_two.register_provider(provider_two)
        await service_two.set_enabled(
            connection.id,
            True,
            actor=_actor(),
            context=_context(project_id),
        )

        transient = await service_two.list_resources(
            connection.id,
            "record",
            actor=_actor(),
            context=_context(project_id),
        )
        transient_ids = {resource.native_reference.native_id: resource.id for resource in transient}
        assert all(transient_ids[native_id] != first_ids[native_id] for native_id in first_ids)

        rebuilt = await service_two.synchronize(
            connection.id,
            "records",
            actor=_actor(),
            context=_context(project_id),
            mode=SyncMode.REBUILD,
        )
        rebuilt_ids = {
            resource.native_reference.native_id: resource.id for resource in rebuilt.resources
        }
        assert rebuilt_ids == first_ids

        durable = await repository.list_external_resources(connection_id=connection.id)
        assert len(durable) == 2
        assert {resource.id for resource in durable} == set(first_ids.values())
        assert not ({resource.id for resource in durable} & set(transient_ids.values()))

        assert rebuilt.events
        for event in rebuilt.events:
            assert event.resource_id == first_ids[event.native_reference.native_id]

    asyncio.run(scenario())


def test_connector_sync_uses_privacy_minimal_external_resource_projection() -> None:
    async def scenario() -> None:
        kernel_repository = InMemoryKernelRepository()
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=kernel_repository,
            ),
            events=kernel_repository,
        )
        secrets = LocalSecretProvider()
        repository = InMemoryConnectorRepository()
        connectors = ConnectorService(repository, ConnectorRegistry())
        await connectors.register_provider(PrivateReferenceConnectorProvider(secrets))
        register_connector_control_plane(control_plane, connectors)
        http = ControlPlaneHTTP(control_plane)

        project_id = new_id("project")
        connection = await _create_connection(connectors, secrets, project_id=project_id)
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connector.sync",
                headers=_headers(idempotency_key="issue-292-safe-sync"),
                body={
                    "resource_ref": connection.id,
                    "stream": "records",
                },
            )
        )

        assert response.status == 200, response.body
        assert isinstance(response.body, dict)
        resource_refs = response.body["resource_refs"]
        assert isinstance(resource_refs, list)
        assert len(resource_refs) == 2
        for resource in resource_refs:
            assert isinstance(resource, dict)
            assert resource["type"] == "external-resource"
            assert resource["connection_id"] == connection.id
            assert resource["project_id"] == project_id
            assert resource["canonical_url"] is None
            assert "metadata" not in resource
            assert "provenance" not in resource
            assert PRIVATE_METADATA not in repr(resource)
            assert PRIVATE_PROVENANCE not in repr(resource)
            assert PRIVATE_URL_TOKEN not in repr(resource)

    asyncio.run(scenario())
