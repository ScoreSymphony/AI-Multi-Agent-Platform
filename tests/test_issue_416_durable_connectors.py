from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    ConnectorRegistry,
    ConnectorService,
    ExternalNativeReference,
    ExternalResourceReference,
    InMemoryConnectorRepository,
    SqliteConnectorRepository,
    SyncCheckpoint,
    SyncStatus,
    connector_definition_id,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.deployment import build_single_node_deployment
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _definition() -> ConnectorDefinition:
    return ConnectorDefinition(
        id=connector_definition_id("fixture.connector", "1.0"),
        connector_type_id="fixture.connector",
        name="Issue 416 fixture",
        version="1.0",
        supported_operations=("sync",),
        resource_types=("record",),
    )


def _connection(
    *,
    connection_id: str | None = None,
    project_id: str | None = None,
    enabled: bool = True,
    status: ConnectionStatus = ConnectionStatus.READY,
    secret_references: tuple[SecretReference, ...] = (),
) -> Connection:
    now = datetime.now(UTC)
    return Connection(
        id=connection_id or new_id("connection"),
        connector_type_id="fixture.connector",
        connector_version="1.0",
        owner_type="user",
        owner_id="issue-416-user",
        display_name="Durable connector",
        project_id=project_id,
        secret_references=secret_references,
        requested_scopes=("read",),
        granted_scopes=("read",),
        enabled=enabled,
        status=status,
        health=HealthStatus.HEALTHY if enabled else HealthStatus.UNAVAILABLE,
        created_at=now,
        updated_at=now,
    )


def _resource(
    connection_id: str,
    native_id: str,
    *,
    resource_id: str | None = None,
) -> ExternalResourceReference:
    return ExternalResourceReference(
        id=resource_id or new_id("external_resource"),
        connection_id=connection_id,
        resource_type="record",
        native_reference=ExternalNativeReference(
            namespace="fixture.records",
            native_id=native_id,
        ),
        revision="1",
        provenance={"source": "fixture"},
        metadata={"label": native_id},
    )


def _checkpoint(connection_id: str, *, cursor: str = "2") -> SyncCheckpoint:
    now = datetime.now(UTC)
    return SyncCheckpoint(
        connection_id=connection_id,
        stream="records",
        cursor=cursor,
        last_successful_sync=now,
        remote_revision="r2",
        status=SyncStatus.SUCCEEDED,
        dedupe_mapping={"alpha": "event-alpha"},
        updated_at=now,
    )


def _headers() -> dict[str, str]:
    return {
        "X-Request-Id": "request-416",
        "X-Correlation-Id": "correlation-416",
        "X-Principal-Ref": "user:issue-416-user",
        "X-Owner-Type": "user",
        "X-Owner-Id": "issue-416-user",
    }


def test_sqlite_repository_reconstructs_connection_definition_and_revision_guard(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        path = tmp_path / "connectors.sqlite3"
        first = SqliteConnectorRepository(path)
        definition = _definition()
        connection = _connection()
        await first.save_definition(definition)
        await first.save_connection(connection)

        reconstructed = SqliteConnectorRepository(path)
        assert (
            await reconstructed.get_definition(definition.connector_type_id, definition.version)
            == definition
        )
        assert await reconstructed.get_connection(connection.id) == connection
        assert reconstructed.schema_version == 1

        newer = replace(connection, revision=3, updated_at=datetime.now(UTC))
        await reconstructed.save_connection(newer)
        with pytest.raises(ContractError) as exc_info:
            await reconstructed.save_connection(replace(connection, revision=2))
        assert exc_info.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_native_identity_reuses_canonical_resource_id_across_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "connectors.sqlite3"
        connection = _connection()
        first = SqliteConnectorRepository(path)
        await first.save_connection(connection)
        original = await first.save_external_resource(_resource(connection.id, "alpha"))

        reconstructed = SqliteConnectorRepository(path)
        replacement_id = new_id("external_resource")
        canonical = await reconstructed.save_external_resource(
            _resource(connection.id, "alpha", resource_id=replacement_id)
        )
        assert canonical.id == original.id
        assert canonical.id != replacement_id

        again = SqliteConnectorRepository(path)
        resources = await again.list_external_resources(connection_id=connection.id)
        assert len(resources) == 1
        assert resources[0].id == original.id

    asyncio.run(scenario())


def test_checkpoint_rebuild_detach_and_connection_cascade_survive_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "connectors.sqlite3"
        connection = _connection()
        first = SqliteConnectorRepository(path)
        await first.save_connection(connection)
        alpha = await first.save_external_resource(_resource(connection.id, "alpha"))
        beta = await first.save_external_resource(_resource(connection.id, "beta"))
        checkpoint = await first.save_checkpoint(_checkpoint(connection.id))

        reconstructed = SqliteConnectorRepository(path)
        assert await reconstructed.get_checkpoint(connection.id, "records") == checkpoint
        rebuilt = await reconstructed.replace_external_resources(
            connection.id,
            (replace(alpha, revision="2"),),
        )
        assert [item.id for item in rebuilt] == [alpha.id]

        after_rebuild = SqliteConnectorRepository(path)
        assert [
            item.id
            for item in await after_rebuild.list_external_resources(connection_id=connection.id)
        ] == [alpha.id]
        with pytest.raises(ContractError) as stale:
            await after_rebuild.get_external_resource(beta.id)
        assert stale.value.code is ErrorCode.NOT_FOUND

        await after_rebuild.delete_external_resource(alpha.id)
        after_detach = SqliteConnectorRepository(path)
        assert await after_detach.list_external_resources(connection_id=connection.id) == ()

        await after_detach.save_external_resource(_resource(connection.id, "gamma"))
        await after_detach.delete_connection(connection.id)
        after_remove = SqliteConnectorRepository(path)
        assert await after_remove.list_external_resources(connection_id=connection.id) == ()
        assert await after_remove.get_checkpoint(connection.id, "records") is None
        with pytest.raises(ContractError) as missing:
            await after_remove.get_connection(connection.id)
        assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_remove_if_unused_protection_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "connectors.sqlite3"
        connection = _connection(enabled=False, status=ConnectionStatus.DISABLED)
        first = SqliteConnectorRepository(path)
        await first.save_connection(connection)
        await first.save_checkpoint(_checkpoint(connection.id))

        reconstructed = SqliteConnectorRepository(path)
        with pytest.raises(ContractError) as exc_info:
            await reconstructed.remove_connection_if_unused(connection.id)
        assert exc_info.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_secret_reference_metadata_is_redacted_before_persistence(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "connectors.sqlite3"
        marker = "fixture-sensitive-marker-416"
        reference = SecretReference(
            provider="local-secrets",
            secret_id="issue-416-secret-reference",
            scope="connector-scope",
            metadata={"authorization": marker},
        )
        repository = SqliteConnectorRepository(path)
        await repository.save_connection(_connection(secret_references=(reference,)))
        persisted_bytes = path.read_bytes()
        assert marker.encode() not in persisted_bytes
        assert b"issue-416-secret-reference" in persisted_bytes

    asyncio.run(scenario())


def test_search_rebuild_uses_reconstructed_durable_connector_sources(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "connectors.sqlite3"
        project_id = new_id("project")
        connection = _connection(project_id=project_id)
        first = SqliteConnectorRepository(path)
        await first.save_connection(connection)
        resource = await first.save_external_resource(_resource(connection.id, "restart-visible"))

        reconstructed = SqliteConnectorRepository(path)
        kernel_repository = InMemoryKernelRepository()
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=kernel_repository,
            ),
            events=kernel_repository,
            authorization=FakeAuthorizationProvider(),
        )
        connectors = ConnectorService(reconstructed, ConnectorRegistry())
        register_connector_control_plane(control_plane, connectors)
        await control_plane.rebuild_search_index()
        http = ControlPlaneHTTP(control_plane)

        result = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                headers=_headers(),
                query={"type": "external-resource", "q": "restart-visible"},
            )
        )
        assert result.status == 200
        assert isinstance(result.body, dict)
        assert result.body["total"] == 1
        assert result.body["items"][0]["resource_id"] == resource.id

    asyncio.run(scenario())


def test_schema_zero_migrates_deterministically_to_current_version(tmp_path: Path) -> None:
    path = tmp_path / "migration-fixture.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 0")

    repository = SqliteConnectorRepository(path)
    assert repository.schema_version == 1
    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "connector_definitions",
        "connections",
        "external_resources",
        "sync_checkpoints",
    } <= tables


def test_in_memory_repository_stays_available_for_ephemeral_profiles() -> None:
    async def scenario() -> None:
        repository = InMemoryConnectorRepository()
        connection = _connection()
        await repository.save_connection(connection)
        assert await repository.get_connection(connection.id) == connection

    asyncio.run(scenario())


def test_public_single_node_composition_uses_durable_connector_repository(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
        first = build_single_node_deployment(config)
        assert isinstance(first.connector_repository, SqliteConnectorRepository)
        assert (
            first.connector_repository.database_path == config.database_dir / "connectors.sqlite3"
        )

        connection = _connection()
        await first.connector_repository.save_connection(connection)
        restarted = build_single_node_deployment(config)
        assert await restarted.connector_repository.get_connection(connection.id) == connection
        assert restarted.connectors.repository is restarted.connector_repository

    asyncio.run(scenario())