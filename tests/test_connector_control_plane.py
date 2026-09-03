from __future__ import annotations

import asyncio

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-connector-44",
        "X-Correlation-Id": "correlation-connector-44",
        "X-Principal-Ref": "user:connector-test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "connector-owner",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


async def _stack() -> tuple[
    ControlPlane,
    ControlPlaneHTTP,
    LocalSecretProvider,
    ConnectorService,
]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=kernel_repository)
    secrets = LocalSecretProvider()
    provider = ReferenceConnectorProvider(secrets)
    service = ConnectorService(InMemoryConnectorRepository(), ConnectorRegistry())
    await service.register_provider(provider)
    register_connector_control_plane(control_plane, service)
    return control_plane, ControlPlaneHTTP(control_plane), secrets, service


def test_control_plane_exposes_canonical_connector_resources_and_lifecycle() -> None:
    async def scenario() -> None:
        control_plane, http, secrets, _ = await _stack()
        assert "connector-definitions" in control_plane.registered_collections
        assert "connections" in control_plane.registered_collections
        assert "connection.create" in control_plane.registered_commands
        assert "connector.sync" in control_plane.registered_commands
        assert "connector.invoke" not in control_plane.registered_commands

        project_id = new_id("project")
        secret_ref = SecretReference(
            provider="local-secrets",
            secret_id="control-plane-connector-token",
            scope=project_id,
        )
        await secrets.create(
            secret_ref,
            "control-plane-secret-material",
            purpose="connector-auth",
            allowed_consumers=("connector.reference",),
            allowed_purposes=("connector-auth",),
        )

        created = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connection.create",
                headers=_headers("connection-create-1"),
                body={
                    "resource_ref": "connections",
                    "connector_type_id": "reference.local",
                    "connector_version": "1.0",
                    "owner_type": "human",
                    "owner_id": "user:connector-test",
                    "display_name": "Local reference account",
                    "project_id": project_id,
                    "secret_references": [secret_ref.to_dict()],
                    "requested_scopes": ["read", "write"],
                    "endpoint_metadata": {"account": "local-fixture"},
                },
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        connection_id = created.body["id"]
        assert isinstance(connection_id, str)
        assert connection_id.startswith("connection_")
        assert created.body["status"] == "ready"
        assert "control-plane-secret-material" not in repr(created.body)

        definitions = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/connector-definitions",
                headers=_headers(),
            )
        )
        assert definitions.status == 200
        assert isinstance(definitions.body, dict)
        items = definitions.body["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        assert isinstance(items[0], dict)
        assert items[0]["id"].startswith("connector_definition_")

        fetched = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/connections/{connection_id}",
                headers=_headers(),
            )
        )
        assert fetched.status == 200
        assert isinstance(fetched.body, dict)
        assert fetched.body["id"] == connection_id
        assert "control-plane-secret-material" not in repr(fetched.body)

        synced = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connector.sync",
                headers=_headers("connection-sync-1"),
                body={
                    "resource_ref": connection_id,
                    "stream": "records",
                },
            )
        )
        assert synced.status == 200
        assert isinstance(synced.body, dict)
        assert synced.body["cursor"] == "2"
        assert len(synced.body["events"]) == 2

    asyncio.run(scenario())


def test_control_plane_rejects_embedded_plaintext_credential_fields() -> None:
    async def scenario() -> None:
        _, http, _, _ = await _stack()
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connection.create",
                headers=_headers("connection-create-secret-inline"),
                body={
                    "resource_ref": "connections",
                    "connector_type_id": "reference.local",
                    "connector_version": "1.0",
                    "owner_type": "human",
                    "owner_id": "user:connector-test",
                    "display_name": "Invalid plaintext connection",
                    "project_id": new_id("project"),
                    "token": "must-not-be-canonical",
                },
            )
        )
        assert response.status == 400
        assert isinstance(response.body, dict)
        assert response.body["code"] == "invalid_request"
        assert "must-not-be-canonical" not in repr(response.body)

    asyncio.run(scenario())
