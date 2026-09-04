from __future__ import annotations

import asyncio

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
    REFERENCE_CONNECTOR_TYPE,
    REFERENCE_CONNECTOR_VERSION,
    ReferenceConnectorProvider,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts import HealthStatus, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    NotificationCategory,
    NotificationQuery,
    RecipientRef,
    RecipientType,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def test_connector_health_failure_projects_attention_through_registered_control_plane() -> None:
    async def scenario() -> None:
        kernel_repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=kernel_repository,
        )
        control_plane = ControlPlane(kernel=kernel, events=kernel_repository)
        secrets = LocalSecretProvider()
        provider = ReferenceConnectorProvider(secrets)
        connectors = ConnectorService(InMemoryConnectorRepository(), ConnectorRegistry())
        await connectors.register_provider(provider)
        register_connector_control_plane(control_plane, connectors)

        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        project_id = new_id("project")
        secret_ref = SecretReference(
            provider="local-secrets",
            secret_id="issue75-connector-token",
            scope=project_id,
        )
        await secrets.create(
            secret_ref,
            "connector-secret-material",
            purpose="connector-auth",
            allowed_consumers=("connector.reference",),
            allowed_purposes=("connector-auth",),
        )
        connection = await connectors.create_connection(
            Connection(
                id=new_id("connection"),
                connector_type_id=REFERENCE_CONNECTOR_TYPE,
                connector_version=REFERENCE_CONNECTOR_VERSION,
                owner_type=recipient.type.value,
                owner_id=recipient.id,
                display_name="Issue 75 connector fixture",
                project_id=project_id,
                secret_references=(secret_ref,),
                requested_scopes=("read",),
            ),
            actor=ActorIdentity(recipient.id, ActorType.HUMAN),
            context=OperationContext(
                owner_type=recipient.type.value,
                owner_id=recipient.id,
                project_id=project_id,
            ),
        )
        provider.set_health(HealthStatus.UNAVAILABLE)
        context = RequestContext(
            request_id="issue75-connector-health",
            correlation_id="issue75-connector-health",
            idempotency_key="issue75-connector-health",
            actor=ActorContext(
                principal_ref=recipient.id,
                actor_type=ActorType.HUMAN.value,
                owner_type=recipient.type.value,
                owner_id=recipient.id,
            ),
        )

        result = await control_plane.execute_command(
            context,
            "connection.health",
            connection.id,
            {},
        )
        notifications = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )

        assert result["status"] == "error"
        assert result["health"] == HealthStatus.UNAVAILABLE.value
        assert len(notifications) == 1
        assert notifications[0].category is NotificationCategory.CONNECTOR
        assert notifications[0].source.resource_type == "connector"
        assert notifications[0].source.resource_id == connection.id
        assert notifications[0].summary["attention"] == "health:error"

    asyncio.run(scenario())
