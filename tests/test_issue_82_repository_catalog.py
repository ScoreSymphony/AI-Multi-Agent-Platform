from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryBindingRecord,
    RepositoryConnection,
    RepositoryRegistry,
    RepositoryRegistryBootstrap,
    SqliteRepositoryBindingCatalog,
    local_git_repository_factory,
)
from ai_multi_agent_platform.security import SecretReference


def _operation(project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-82-catalog",
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
            display_name="Durable local Git fixture",
            project_id=project_id,
            secret_references=(
                SecretReference(provider="local", secret_id="git-token", scope="repository"),
            ),
        ),
        provider_id="local-git",
        local=True,
        metadata={"purpose": "bootstrap-test"},
    )


def test_repository_binding_catalog_restores_local_provider_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = _operation(project_id)
        connection = _connection(project_id)
        root = tmp_path / "repo"
        provider = LocalGitRepositoryProvider(root, connection)
        repository = await provider.initialize(operation)
        (root / "README.md").write_text("durable\n", encoding="utf-8")
        commit = await provider.commit(
            repository,
            "initial",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )
        reference = await provider.read(repository, operation)
        binding = RepositoryBinding(connection, reference, provider)

        path = tmp_path / "repository-catalog.sqlite3"
        catalog = SqliteRepositoryBindingCatalog(path)
        catalog.save(
            RepositoryBindingRecord.from_binding(
                binding,
                adapter_configuration={"root": str(root)},
            )
        )

        # The catalog owns routing/configuration only. Credential values and SecretReference IDs
        # stay in the canonical Connection store and must not be copied into repository storage.
        assert b"git-token" not in path.read_bytes()

        restarted_catalog = SqliteRepositoryBindingCatalog(path)
        restarted_registry = RepositoryRegistry()
        bootstrap = RepositoryRegistryBootstrap(restarted_catalog)
        bootstrap.register_factory("local-git", local_git_repository_factory)

        async def resolve_connection(connection_id: str) -> Connection:
            assert connection_id == connection.id
            return connection.connection

        restored = await bootstrap.restore(restarted_registry, resolve_connection)
        assert len(restored) == 1
        current = restarted_registry.resolve(reference.id)
        assert current.reference.to_dict() == reference.to_dict()
        assert current.connection.metadata == connection.metadata
        assert current.connection.secret_references == connection.secret_references
        resolved = await current.provider.resolve_revision(current.reference, "HEAD", operation)
        assert resolved.commit_sha == commit.revision

    asyncio.run(scenario())


def test_repository_bootstrap_fails_closed_when_provider_factory_is_unavailable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = _operation(project_id)
        connection = _connection(project_id)
        root = tmp_path / "repo"
        provider = LocalGitRepositoryProvider(root, connection)
        repository = await provider.initialize(operation)
        catalog = SqliteRepositoryBindingCatalog(tmp_path / "repository-catalog.sqlite3")
        catalog.save(
            RepositoryBindingRecord.from_binding(
                RepositoryBinding(connection, repository, provider),
                adapter_configuration={"root": str(root)},
            )
        )

        async def resolve_connection(connection_id: str) -> Connection:
            assert connection_id == connection.id
            return connection.connection

        with pytest.raises(ContractError) as error:
            await RepositoryRegistryBootstrap(catalog).restore(
                RepositoryRegistry(),
                resolve_connection,
            )
        assert error.value.code is ErrorCode.UNAVAILABLE
        assert error.value.provider_id == "local-git"

    asyncio.run(scenario())
