from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectionStatus,
    ConnectorRegistry,
    ConnectorService,
    ExternalNativeReference,
    ExternalResourceReference,
    SqliteConnectorRepository,
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
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class RestartScopedAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if (
            request.action
            in {
                "external-resource:list",
                "external-resource:read",
                "external-resource.detach",
            }
            and request.context.project_id == self.denied_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="restart-hidden-project")
        return AuthorizationDecision(allowed=True, reason="restart-visible-project")


def _connection(project_id: str, owner_id: str) -> Connection:
    return Connection(
        id=new_id("connection"),
        connector_type_id="fixture.connector",
        connector_version="1.0",
        owner_type="user",
        owner_id=owner_id,
        display_name=f"Restart connection {owner_id}",
        project_id=project_id,
        enabled=True,
        status=ConnectionStatus.READY,
        health=HealthStatus.HEALTHY,
    )


def _resource(connection_id: str, native_id: str) -> ExternalResourceReference:
    return ExternalResourceReference(
        id=new_id("external_resource"),
        connection_id=connection_id,
        resource_type="record",
        native_reference=ExternalNativeReference(
            namespace="fixture.records",
            native_id=native_id,
        ),
        revision="1",
    )


def _headers() -> dict[str, str]:
    return {
        "X-Request-Id": "request-416-hardening",
        "X-Correlation-Id": "correlation-416-hardening",
        "X-Principal-Ref": "user:issue-416-user",
        "X-Owner-Type": "user",
        "X-Owner-Id": "issue-416-user",
    }


def test_restart_search_rebuild_filters_unauthorized_durable_resources(tmp_path: Path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "connectors.sqlite3"
        visible_project = new_id("project")
        hidden_project = new_id("project")
        visible_connection = _connection(visible_project, "visible-owner")
        hidden_connection = _connection(hidden_project, "hidden-owner")

        first = SqliteConnectorRepository(database_path)
        await first.save_connection(visible_connection)
        await first.save_connection(hidden_connection)
        visible_resource = await first.save_external_resource(
            _resource(visible_connection.id, "restart-visible")
        )
        hidden_resource = await first.save_external_resource(
            _resource(hidden_connection.id, "restart-hidden")
        )

        reconstructed = SqliteConnectorRepository(database_path)
        kernel_repository = InMemoryKernelRepository()
        authorization = RestartScopedAuthorization(hidden_project)
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=kernel_repository,
            ),
            events=kernel_repository,
            authorization=authorization,
        )
        connectors = ConnectorService(reconstructed, ConnectorRegistry())
        register_connector_control_plane(control_plane, connectors)
        await control_plane.rebuild_search_index()
        http = ControlPlaneHTTP(control_plane)

        response = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                headers=_headers(),
                query={"type": "external-resource"},
            )
        )
        assert response.status == 200
        assert isinstance(response.body, dict)
        assert response.body["total"] == 1
        assert visible_resource.id in repr(response.body)
        assert hidden_resource.id not in repr(response.body)

        hidden_exact = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                headers=_headers(),
                query={"type": "external-resource", "id": hidden_resource.id},
            )
        )
        assert hidden_exact.status == 200
        assert isinstance(hidden_exact.body, dict)
        assert hidden_exact.body["total"] == 0
        assert hidden_resource.id not in repr(hidden_exact.body)

    asyncio.run(scenario())
