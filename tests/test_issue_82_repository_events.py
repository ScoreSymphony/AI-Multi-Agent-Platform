from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorEvent,
    ExternalNativeReference,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
    RepositoryEventBridge,
    repository_platform_event_id,
)
from ai_multi_agent_platform.testing.sqlite_events import SqliteEventProvider


def _operation(project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-82-repository-events",
        owner_type="user",
        owner_id="repository-user",
        project_id=project_id,
    )


def _connection(project_id: str) -> RepositoryConnection:
    return RepositoryConnection(
        connection=Connection(
            id=new_id("connection"),
            connector_type_id="local-git",
            connector_version="1.0",
            owner_type="user",
            owner_id="repository-user",
            display_name="Repository event fixture",
            project_id=project_id,
        ),
        provider_id="local-git",
        local=True,
    )


def _event(
    connection: RepositoryConnection,
    repository_id: str,
    *,
    connector_event_id: str,
    native_id: str,
    verified: bool = True,
) -> ConnectorEvent:
    return ConnectorEvent(
        id=connector_event_id,
        connector_type_id=connection.connection.connector_type_id,
        connection_id=connection.id,
        event_type="repository.changed",
        native_reference=ExternalNativeReference(
            namespace="local-git",
            native_id=native_id,
        ),
        schema_version="1.0",
        dedupe_key="delivery-123",
        received_at=datetime(2026, 9, 4, 1, 0, tzinfo=UTC),
        project_id=connection.connection.project_id,
        resource_id=repository_id,
        verified=verified,
        provenance={"transport": "fixture"},
        payload={"ref": "refs/heads/main"},
    )


def test_repository_event_bridge_deduplicates_by_connector_dedupe_key(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        connection = _connection(project_id)
        provider = LocalGitRepositoryProvider(tmp_path / "repo", connection)
        repository = await provider.initialize(_operation(project_id))
        binding = RepositoryBinding(connection, repository, provider)
        events = SqliteEventProvider(tmp_path / "events.sqlite")
        bridge = RepositoryEventBridge(events)

        first_external = _event(
            connection,
            repository.id,
            connector_event_id=new_id("connector_event"),
            native_id="delivery-native-1",
        )
        duplicate_external = _event(
            connection,
            repository.id,
            connector_event_id=new_id("connector_event"),
            native_id="delivery-native-2",
        )
        first = await bridge.publish(
            first_external,
            binding,
            correlation_id="repository-webhooks",
        )
        duplicate = await bridge.publish(
            duplicate_external,
            binding,
            correlation_id="repository-webhooks",
        )

        assert first.id == duplicate.id == repository_platform_event_id(first_external)
        stored = await events.read("repository-webhooks")
        assert len(stored) == 1
        assert stored[0].subject_type == "project"
        assert stored[0].subject_id == project_id
        assert stored[0].payload["repository_id"] == repository.id
        assert stored[0].payload["dedupe_key"] == "delivery-123"
        assert stored[0].provenance is not None
        assert stored[0].external_refs[0].system == "local-git"

    asyncio.run(scenario())


def test_unverified_repository_event_is_rejected_before_publication(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        connection = _connection(project_id)
        provider = LocalGitRepositoryProvider(tmp_path / "repo", connection)
        repository = await provider.initialize(_operation(project_id))
        events = SqliteEventProvider(tmp_path / "events.sqlite")
        bridge = RepositoryEventBridge(events)
        unverified = _event(
            connection,
            repository.id,
            connector_event_id=new_id("connector_event"),
            native_id="unverified",
            verified=False,
        )

        with pytest.raises(ContractError) as error:
            await bridge.publish(
                unverified,
                RepositoryBinding(connection, repository, provider),
                correlation_id="repository-webhooks",
            )
        assert error.value.code is ErrorCode.UNAUTHORIZED
        assert await events.read("repository-webhooks") == ()

    asyncio.run(scenario())
