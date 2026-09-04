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
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def test_endpoint_metadata_cannot_embed_plaintext_credentials() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=repository,
            ),
            events=repository,
        )
        service = ConnectorService(InMemoryConnectorRepository(), ConnectorRegistry())
        await service.register_provider(ReferenceConnectorProvider(LocalSecretProvider()))
        register_connector_control_plane(control_plane, service)
        http = ControlPlaneHTTP(control_plane)

        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connection.create",
                headers={
                    "Content-Type": "application/json",
                    "X-Request-Id": "request-connector-secret-metadata",
                    "X-Correlation-Id": "correlation-connector-secret-metadata",
                    "X-Principal-Ref": "user:connector-test",
                    "X-Owner-Type": "user",
                    "X-Owner-Id": "connector-owner",
                    "Idempotency-Key": "connection-secret-metadata",
                },
                body={
                    "resource_ref": "connections",
                    "connector_type_id": "reference.local",
                    "connector_version": "1.0",
                    "owner_type": "human",
                    "owner_id": "user:connector-test",
                    "display_name": "Unsafe endpoint",
                    "project_id": new_id("project"),
                    "endpoint_metadata": {"token": "plaintext-must-not-be-stored"},
                },
            )
        )
        assert response.status == 400
        assert isinstance(response.body, dict)
        assert response.body["code"] == "invalid_request"
        assert "plaintext-must-not-be-stored" not in repr(response.body)

    asyncio.run(scenario())
