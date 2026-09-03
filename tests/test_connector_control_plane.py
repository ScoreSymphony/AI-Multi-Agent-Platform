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
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    SecretReference,
)
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


async def _create_control_plane_connection(
    http: ControlPlaneHTTP,
    secrets: LocalSecretProvider,
    *,
    project_id: str,
    secret_id: str,
    key: str,
    display_name: str,
) -> str:
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id=secret_id,
        scope=project_id,
    )
    await secrets.create(
        secret_ref,
        f"material-{secret_id}",
        purpose="connector-auth",
        allowed_consumers=("connector.reference",),
        allowed_purposes=("connector-auth",),
    )
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/commands/connection.create",
            headers=_headers(key),
            body={
                "resource_ref": "connections",
                "connector_type_id": "reference.local",
                "connector_version": "1.0",
                "owner_type": "human",
                "owner_id": "user:connector-test",
                "display_name": display_name,
                "project_id": project_id,
                "secret_references": [secret_ref.to_dict()],
                "requested_scopes": ["read", "write"],
                "endpoint_metadata": {"account": "local-fixture"},
            },
        )
    )
    assert response.status == 200
    assert isinstance(response.body, dict)
    connection_id = response.body["id"]
    assert isinstance(connection_id, str)
    return connection_id


def test_control_plane_exposes_canonical_connector_resources_and_lifecycle() -> None:
    async def scenario() -> None:
        control_plane, http, secrets, _ = await _stack()
        assert "connector-definitions" in control_plane.registered_collections
        assert "connections" in control_plane.registered_collections
        assert "connection.create" in control_plane.registered_commands
        assert "connector.sync" in control_plane.registered_commands
        assert "connector.invoke" not in control_plane.registered_commands

        project_id = new_id("project")
        connection_id = await _create_control_plane_connection(
            http,
            secrets,
            project_id=project_id,
            secret_id="control-plane-connector-token",
            key="connection-create-1",
            display_name="Local reference account",
        )

        created = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/connections/{connection_id}",
                headers=_headers(),
            )
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        assert connection_id.startswith("connection_")
        assert created.body["status"] == "ready"
        assert created.body["adapter_metadata"] == [
            {
                "namespace": "reference.local",
                "values": {"account_id": "local-fixture"},
            }
        ]
        assert "material-control-plane-connector-token" not in repr(created.body)

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
        assert items[0]["adapter_metadata"] == [
            {
                "namespace": "platform.reference",
                "values": {
                    "source": "bundled",
                    "provider_id": "connector.reference",
                },
            }
        ]

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
        assert synced.body["mode"] == "incremental"
        assert synced.body["cursor"] == "2"
        assert len(synced.body["events"]) == 2

        resynced = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connector.sync",
                headers=_headers("connection-sync-resync"),
                body={
                    "resource_ref": connection_id,
                    "stream": "records",
                    "mode": "resync",
                },
            )
        )
        assert resynced.status == 200
        assert isinstance(resynced.body, dict)
        assert resynced.body["mode"] == "resync"
        assert len(resynced.body["events"]) == 2

    asyncio.run(scenario())


def test_control_plane_connection_reads_apply_connection_project_scope() -> None:
    async def scenario() -> None:
        _, http, secrets, service = await _stack()
        allowed_project = new_id("project")
        denied_project = new_id("project")
        allowed_connection = await _create_control_plane_connection(
            http,
            secrets,
            project_id=allowed_project,
            secret_id="scope-allowed-token",
            key="scope-create-allowed",
            display_name="Allowed connection",
        )
        denied_connection = await _create_control_plane_connection(
            http,
            secrets,
            project_id=denied_project,
            secret_id="scope-denied-token",
            key="scope-create-denied",
            display_name="Denied connection",
        )

        policy = LocalPrincipalPolicy(
            principal_ref="user:connector-test",
            actor_types=frozenset({ActorType.HUMAN}),
            allowed_actions=frozenset({AuthorizationAction.READ}),
            resource_types=frozenset({ResourceType.CONNECTOR}),
            project_ids=frozenset({allowed_project}),
        )
        service.authorization_gate = AuthorizationGate(LocalAuthorizationProvider((policy,)))

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/connections",
                headers=_headers(),
            )
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        items = listed.body["items"]
        assert isinstance(items, list)
        assert [item["id"] for item in items if isinstance(item, dict)] == [allowed_connection]

        allowed = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/connections/{allowed_connection}",
                headers=_headers(),
            )
        )
        assert allowed.status == 200

        denied = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/connections/{denied_connection}",
                headers=_headers(),
            )
        )
        assert denied.status == 403

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
