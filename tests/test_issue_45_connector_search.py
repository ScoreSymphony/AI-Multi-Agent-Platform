from __future__ import annotations

import asyncio

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    connector_definition_id,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
    HealthStatus,
)
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

ENDPOINT_SECRET = "endpoint-private-marker-must-not-be-searchable"
SECRET_REFERENCE_ID = "secret-reference-id-must-not-be-searchable"
SCHEMA_SECRET = "definition-schema-marker-must-not-be-searchable"
ORG_CONNECTION_NAME = "Organization connection must stay outside search"


class ConnectorSearchAuthorization(FakeAuthorizationProvider):
    def __init__(
        self,
        *,
        denied_project_id: str | None = None,
        deny_definitions: bool = False,
    ) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id
        self.deny_definitions = deny_definitions

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if self.deny_definitions and request.action == "connector-definition:list":
            return AuthorizationDecision(allowed=False, reason="definitions-hidden")
        if (
            request.action == "connection:list"
            and self.denied_project_id is not None
            and request.project_id == self.denied_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="connection-project-hidden")
        return AuthorizationDecision(allowed=True, reason="connector-visible")


def _headers() -> dict[str, str]:
    return {
        "X-Request-Id": "request-search-connector",
        "X-Correlation-Id": "correlation-search-connector",
        "X-Principal-Ref": "user:connector-search",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }


async def _stack(
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[
    ControlPlane,
    ControlPlaneHTTP,
    InMemoryConnectorRepository,
]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=authorization,
    )
    connector_repository = InMemoryConnectorRepository()
    service = ConnectorService(connector_repository, ConnectorRegistry())
    register_connector_control_plane(control_plane, service)
    return control_plane, ControlPlaneHTTP(control_plane), connector_repository


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(method="GET", path="/api/v1/search", query=query, headers=_headers())
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    raw = page["items"]
    assert isinstance(raw, list)
    assert all(isinstance(item, dict) for item in raw)
    return raw


async def _seed(repository: InMemoryConnectorRepository) -> tuple[str, str, str, str]:
    definition = ConnectorDefinition(
        id=connector_definition_id("reference.local", "1.0"),
        connector_type_id="reference.local",
        name="Local reference connector",
        version="1.0",
        description="Deterministic local connector for Search integration tests",
        supported_operations=("resource.list", "resource.read"),
        features=("sync", "events"),
        resource_types=("record",),
        actions=("record.create",),
        event_types=("record.changed",),
        configuration_schema={"private_example": SCHEMA_SECRET},
    )
    await repository.save_definition(definition)

    visible_project = new_id("project")
    hidden_project = new_id("project")
    visible = Connection(
        id=new_id("connection"),
        connector_type_id="reference.local",
        connector_version="1.0",
        owner_type="user",
        owner_id="alice",
        display_name="Visible local account",
        project_id=visible_project,
        endpoint_metadata={"private_note": ENDPOINT_SECRET},
        secret_references=(
            SecretReference(
                provider="local-secrets",
                secret_id=SECRET_REFERENCE_ID,
                scope=visible_project,
            ),
        ),
        requested_scopes=("read", "write"),
        granted_scopes=("read",),
        status=ConnectionStatus.READY,
        health=HealthStatus.HEALTHY,
    )
    hidden = Connection(
        id=new_id("connection"),
        connector_type_id="reference.local",
        connector_version="1.0",
        owner_type="user",
        owner_id="bob",
        display_name="Hidden project account",
        project_id=hidden_project,
        status=ConnectionStatus.READY,
        health=HealthStatus.HEALTHY,
    )
    organization = Connection(
        id=new_id("connection"),
        connector_type_id="reference.local",
        connector_version="1.0",
        owner_type="user",
        owner_id="org-owner",
        display_name=ORG_CONNECTION_NAME,
        organization_id="organization-private",
        status=ConnectionStatus.READY,
        health=HealthStatus.HEALTHY,
    )
    await repository.save_connection(visible)
    await repository.save_connection(hidden)
    await repository.save_connection(organization)
    return definition.id, visible.id, hidden.id, hidden_project


def test_connector_definitions_and_connections_are_discoverable_without_sensitive_metadata() -> (
    None
):
    async def scenario() -> None:
        _, http, repository = await _stack()
        definition_id, visible_id, _, _ = await _seed(repository)

        definitions = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/connector-definitions",
                headers=_headers(),
            )
        )
        assert definitions.status == 200
        assert isinstance(definitions.body, dict)
        definition_resource = definitions.body["items"][0]
        assert definition_resource["type"] == "connector-definition"

        connection = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/connections/{visible_id}",
                headers=_headers(),
            )
        )
        assert connection.status == 200
        assert isinstance(connection.body, dict)
        assert connection.body["type"] == "connection"

        exact_definition = await _search(
            http,
            type="connector-definition",
            id=definition_id,
        )
        assert exact_definition["total"] == 1
        definition_item = _items(exact_definition)[0]
        assert definition_item["title"] == "Local reference connector"
        assert (
            definition_item["summary"]
            == "Deterministic local connector for Search integration tests"
        )
        assert definition_item["canonical_ref"] == (
            f"/api/v1/connector-definitions/{definition_id}"
        )

        by_operation = await _search(
            http,
            type="connector-definition",
            q="resource.list",
        )
        assert by_operation["total"] == 1

        exact_connection = await _search(http, type="connection", id=visible_id)
        assert exact_connection["total"] == 1
        connection_item = _items(exact_connection)[0]
        assert connection_item["title"] == "Visible local account"
        assert connection_item["status"] == "ready"
        assert connection_item["project_id"] is not None
        assert connection_item["owner"] == {"type": "user", "id": "alice"}
        assert connection_item["canonical_ref"] == f"/api/v1/connections/{visible_id}"

        by_status = await _search(http, type="connection", status="ready")
        assert by_status["total"] == 2

        for private_query in (ENDPOINT_SECRET, SECRET_REFERENCE_ID, SCHEMA_SECRET):
            leaked = await _search(http, q=private_query)
            assert leaked["total"] == 0

        organization = await _search(http, type="connection", q=ORG_CONNECTION_NAME)
        assert organization["total"] == 0
        all_connections = await _search(http, type="connection")
        assert all_connections["total"] == 2
        assert ORG_CONNECTION_NAME not in repr(all_connections)

    asyncio.run(scenario())


def test_connector_search_filters_authorization_before_counts_and_exact_ids() -> None:
    async def scenario() -> None:
        authorization = ConnectorSearchAuthorization()
        _, http, repository = await _stack(authorization)
        definition_id, visible_id, hidden_id, hidden_project = await _seed(repository)
        authorization.denied_project_id = hidden_project

        connections = await _search(http, type="connection")
        assert connections["total"] == 1
        assert _items(connections)[0]["resource_id"] == visible_id
        assert hidden_id not in repr(connections)

        hidden_exact = await _search(http, type="connection", id=hidden_id)
        assert hidden_exact["total"] == 0
        assert hidden_id not in repr(hidden_exact)
        assert any(
            call.action == "connection:list" and call.project_id == hidden_project
            for call in authorization.calls
        )

        authorization.deny_definitions = True
        definitions = await _search(http, type="connector-definition")
        assert definitions["total"] == 0
        assert definition_id not in repr(definitions)
        exact_definition = await _search(
            http,
            type="connector-definition",
            id=definition_id,
        )
        assert exact_definition["total"] == 0
        assert definition_id not in repr(exact_definition)

    asyncio.run(scenario())
