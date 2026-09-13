from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.single_node import build_single_node_deployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
)

_PASSWORD = "correct horse battery staple"


def test_verified_repository_event_enters_normal_single_node_automation_runtime(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("repository-user", _PASSWORD)
        owner_id = admin.user_id
        project = deployment.scopes.create_project(
            key="issue-82-runtime-event-project",
            name="Repository runtime event project",
            owner_type="user",
            owner_id=owner_id,
        )
        operation = OperationContext(
            correlation_id="issue-82-runtime-event",
            owner_type="user",
            owner_id=owner_id,
            project_id=project.id,
        )
        connection = RepositoryConnection(
            connection=Connection(
                id=new_id("connection"),
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type="user",
                owner_id=owner_id,
                display_name="Repository runtime event fixture",
                project_id=project.id,
            ),
            provider_id="local-git",
            local=True,
        )
        provider = LocalGitRepositoryProvider(tmp_path / "repository", connection)
        repository = await provider.initialize(operation)
        deployment.repository_registry.register(RepositoryBinding(connection, repository, provider))

        automation = await deployment.control_plane.automation_service.create_automation(
            name="Repository push runtime watcher",
            description="Create a canonical task from the normal platform event runtime.",
            identity=IdentityContext(
                principal_ref=owner_id,
                owner_type="user",
                owner_id=owner_id,
            ),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="repository.external.push",
                filters={"repository_id": repository.id},
            ),
            task_template=TaskTemplate(
                title="Review repository runtime push",
                objective="Review the verified canonical repository event.",
                project_id=project.id,
            ),
            project_id=project.id,
        )
        ingress = deployment.repository_event_ingress

        def connector_event(*, verified: bool, native_id: str) -> ConnectorEvent:
            return ConnectorEvent(
                id=new_id("connector_event"),
                connector_type_id="local-git",
                connection_id=connection.id,
                event_type="push",
                native_reference=ExternalNativeReference(
                    namespace="local-git",
                    native_id=native_id,
                ),
                schema_version="1.0",
                dedupe_key="runtime-delivery-42",
                received_at=datetime.now(UTC),
                project_id=project.id,
                resource_id=repository.id,
                verified=verified,
                provenance={"transport": "verified-fixture"},
                payload={"ref": "refs/heads/main"},
            )

        with pytest.raises(ContractError) as exc_info:
            await ingress.publish(
                connector_event(verified=False, native_id="unverified"),
                correlation_id="issue-82-runtime-events",
            )
        assert exc_info.value.code is ErrorCode.UNAUTHORIZED
        assert await deployment.kernel_repository.read_events(project.id) == ()

        canonical = await ingress.publish(
            connector_event(verified=True, native_id="delivery-42"),
            correlation_id="issue-82-runtime-events",
        )
        stored_events = await deployment.kernel_repository.read_events(project.id)
        assert stored_events == (canonical,)

        tick = await deployment.control_plane.automation_runtime.run_once()
        assert canonical.id in tick.processed_event_ids
        deliveries = await deployment.control_plane.automation_service.list_deliveries(
            automation.id
        )
        assert len(deliveries) == 1
        delivery = deliveries[0]
        assert delivery.status is DeliveryStatus.SUCCEEDED, (
            delivery.error_code,
            delivery.error_message,
        )
        assert delivery.generated_task_id is not None
        generated = await deployment.kernel.get_task(delivery.generated_task_id)
        assert generated.task.project_id == project.id
        assert generated.task.title == "Review repository runtime push"

        duplicate = await ingress.publish(
            connector_event(verified=True, native_id="delivery-42-retry"),
            correlation_id="issue-82-runtime-events",
        )
        assert duplicate.id == canonical.id
        assert await deployment.kernel_repository.read_events(project.id) == (canonical,)

        second_tick = await deployment.control_plane.automation_runtime.run_once()
        assert second_tick.event_delivery_ids == ()
        persisted_deliveries = await deployment.control_plane.automation_service.list_deliveries(
            automation.id
        )
        assert len(persisted_deliveries) == 1
        assert persisted_deliveries[0].generated_task_id == delivery.generated_task_id

    asyncio.run(scenario())
