from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorRegistry,
    ConnectorService,
    ReferenceConnectorProvider,
    SqliteConnectorRepository,
    SyncMode,
)
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference


def test_incremental_sync_resumes_after_repository_and_provider_recreation(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        database_path = tmp_path / "connectors.sqlite3"
        secrets = LocalSecretProvider()
        secret_reference = SecretReference(
            provider="local-secrets",
            secret_id="issue-416-reference-value",
            scope=project_id,
        )
        await secrets.create(
            secret_reference,
            "fixture-value-416",
            purpose="connector-auth",
            allowed_consumers=("connector.reference",),
            allowed_purposes=("connector-auth",),
        )
        actor = ActorIdentity("user:issue-416-user", ActorType.HUMAN)
        context = OperationContext(
            correlation_id="issue-416-sync-resume",
            owner_type="user",
            owner_id="issue-416-user",
            project_id=project_id,
        )

        first_repository = SqliteConnectorRepository(database_path)
        first_service = ConnectorService(first_repository, ConnectorRegistry())
        await first_service.register_provider(ReferenceConnectorProvider(secrets))
        created = await first_service.create_connection(
            Connection(
                id=new_id("connection"),
                connector_type_id="reference.local",
                connector_version="1.0",
                owner_type="user",
                owner_id="issue-416-user",
                display_name="Restart-resumable reference connection",
                project_id=project_id,
                secret_references=(secret_reference,),
                requested_scopes=("read", "write"),
            ),
            actor=actor,
            context=context,
        )
        initial = await first_service.synchronize(
            created.id,
            "records",
            actor=actor,
            context=context,
        )
        assert len(initial.resources) == 2
        assert initial.checkpoint.cursor == "2"
        assert b"fixture-value-416" not in database_path.read_bytes()
        original_ids = {
            resource.native_reference.native_id: resource.id for resource in initial.resources
        }

        reconstructed_repository = SqliteConnectorRepository(database_path)
        reconstructed_service = ConnectorService(reconstructed_repository, ConnectorRegistry())
        recreated_provider = ReferenceConnectorProvider(secrets)
        await reconstructed_service.register_provider(recreated_provider)
        await reconstructed_service.set_enabled(
            created.id,
            True,
            actor=actor,
            context=context,
        )

        resumed = await reconstructed_service.synchronize(
            created.id,
            "records",
            actor=actor,
            context=context,
        )
        assert resumed.resources == ()
        assert resumed.checkpoint.cursor == "2"

        rebuilt = await reconstructed_service.synchronize(
            created.id,
            "records",
            actor=actor,
            context=context,
            mode=SyncMode.REBUILD,
        )
        assert {
            resource.native_reference.native_id: resource.id for resource in rebuilt.resources
        } == original_ids

    asyncio.run(scenario())
