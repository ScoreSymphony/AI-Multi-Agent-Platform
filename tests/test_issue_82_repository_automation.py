from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.automation import (
    DeliveryStatus,
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorEvent,
    ExternalNativeReference,
)
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
    RepositoryEventBridge,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeEventProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def test_verified_repository_event_triggers_canonical_automation_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        kernel_repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=kernel_repository,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=kernel_repository,
            authorization=FakeAuthorizationProvider(),
        )
        project_id = new_id("project")
        automation = await control_plane.automation_service.create_automation(
            name="Repository push watcher",
            description="Create a canonical task for one repository push event.",
            identity=IdentityContext(
                principal_ref="user:repository-user",
                owner_type="user",
                owner_id="repository-user",
            ),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="repository.external.push",
                filters={"repository_id": "placeholder"},
            ),
            task_template=TaskTemplate(
                title="Review repository push",
                objective="Review the canonical repository event.",
                project_id=project_id,
            ),
            project_id=project_id,
        )

        connection = RepositoryConnection(
            connection=Connection(
                id=new_id("connection"),
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type="user",
                owner_id="repository-user",
                display_name="Repository automation fixture",
                project_id=project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        provider = LocalGitRepositoryProvider(tmp_path / "repo", connection)
        repository = await provider.initialize(
            OperationContext(
                correlation_id="issue-82-repository-automation",
                owner_type="user",
                owner_id="repository-user",
                project_id=project_id,
            )
        )
        binding = RepositoryBinding(connection, repository, provider)

        # Recreate the Automation only after the canonical repository identity exists so the
        # filter proves that provider-native event data is not needed at the #18 boundary.
        await control_plane.automation_service.delete_automation(automation.id)
        automation = await control_plane.automation_service.create_automation(
            name="Repository push watcher",
            description="Create a canonical task for one repository push event.",
            identity=IdentityContext(
                principal_ref="user:repository-user",
                owner_type="user",
                owner_id="repository-user",
            ),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="repository.external.push",
                filters={"repository_id": repository.id},
            ),
            task_template=TaskTemplate(
                title="Review repository push",
                objective="Review the canonical repository event.",
                project_id=project_id,
            ),
            project_id=project_id,
        )

        events = FakeEventProvider()
        bridge = RepositoryEventBridge(events)
        external = ConnectorEvent(
            id=new_id("connector_event"),
            connector_type_id="local-git",
            connection_id=connection.id,
            event_type="push",
            native_reference=ExternalNativeReference(
                namespace="local-git",
                native_id="delivery-42",
            ),
            schema_version="1.0",
            dedupe_key="delivery-42",
            received_at=datetime.now(UTC),
            project_id=project_id,
            resource_id=repository.id,
            verified=True,
            provenance={"transport": "fixture"},
            payload={"ref": "refs/heads/main"},
        )
        canonical = await bridge.publish(
            external,
            binding,
            correlation_id="repository-automation-events",
        )
        deliveries = await control_plane.automation_service.deliver_canonical_platform_event(
            canonical
        )

        assert canonical.event_type == "repository.external.push"
        assert canonical.project_id == project_id
        assert canonical.payload["repository_id"] == repository.id
        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery.automation_id == automation.id
        assert delivery.status is DeliveryStatus.SUCCEEDED
        assert delivery.generated_task_id is not None
        generated = await kernel.get_task(delivery.generated_task_id)
        assert generated.task.project_id == project_id
        assert generated.task.title == "Review repository push"

        duplicate = await bridge.publish(
            ConnectorEvent(
                id=new_id("connector_event"),
                connector_type_id="local-git",
                connection_id=connection.id,
                event_type="push",
                native_reference=ExternalNativeReference(
                    namespace="local-git",
                    native_id="delivery-42-retry",
                ),
                schema_version="1.0",
                dedupe_key="delivery-42",
                received_at=external.received_at,
                project_id=project_id,
                resource_id=repository.id,
                verified=True,
                provenance={"transport": "fixture"},
                payload={"ref": "refs/heads/main"},
            ),
            binding,
            correlation_id="repository-automation-events",
        )
        duplicate_deliveries = (
            await control_plane.automation_service.deliver_canonical_platform_event(duplicate)
        )
        stored = await control_plane.automation_service.list_deliveries(automation.id)

        assert duplicate.id == canonical.id
        assert len(duplicate_deliveries) == 1
        assert duplicate_deliveries[0].generated_task_id == delivery.generated_task_id
        assert len(stored) == 1
        assert len(events.publish_calls) == 2
        assert len(await events.read("repository-automation-events")) == 1

    asyncio.run(scenario())
