from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryCallContext,
    RepositoryConnection,
    RepositoryRegistry,
    RepositoryService,
    RepositoryWorkspaceSourceResolver,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    SecretReference,
)
from ai_multi_agent_platform.workspaces import (
    LocalWorkspaceProvider,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceType,
)


def _operation(project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-82-tests",
        owner_type="user",
        owner_id="repository-user",
        project_id=project_id,
    )


def _connection(project_id: str, *, with_secret: bool = False) -> RepositoryConnection:
    secrets = (
        SecretReference(provider="local", secret_id="git-token", scope="repository"),
    ) if with_secret else ()
    return RepositoryConnection(
        connection=Connection(
            id=new_id("connection"),
            connector_type_id="local-git",
            connector_version="1.0",
            owner_type="user",
            owner_id="repository-user",
            display_name="Local Git fixture",
            project_id=project_id,
            secret_references=secrets,
        ),
        provider_id="local-git",
        local=True,
    )


def test_local_git_initialize_branch_status_diff_commit_and_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = _operation(project_id)
        root = tmp_path / "repo"
        provider = LocalGitRepositoryProvider(root, _connection(project_id))
        repository = await provider.initialize(operation)

        (root / "README.md").write_text("one\n", encoding="utf-8")
        first = await provider.commit(
            repository,
            "initial",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )
        resolved = await provider.resolve_revision(repository, "HEAD", operation)
        assert resolved.commit_sha == first.revision

        branch = await provider.create_branch(
            repository,
            "feature/test",
            operation,
            start_revision=first.revision,
            checkout=True,
        )
        assert branch.commit_sha == first.revision
        assert "feature/test" in await provider.branches(repository, operation)

        (root / "README.md").write_text("two\n", encoding="utf-8")
        status = await provider.status(repository, operation)
        assert status.branch == "feature/test"
        assert "README.md" in status.modified_paths
        diff = await provider.diff(repository, operation, base_revision=first.revision)
        assert diff.base_revision == first.revision
        assert diff.changed_paths == ("README.md",)
        assert "two" in diff.patch

        second = await provider.commit(
            repository,
            "change",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )
        assert second.revision != first.revision
        assert (await provider.status(repository, operation)).clean

    asyncio.run(scenario())


def test_workspace_materializes_exact_repository_revision_into_canonical_files(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = _operation(project_id)
        data_context = DataAccessContext(operation=operation, actor_ref="user:repository-user")
        root = tmp_path / "repo"
        connection = _connection(project_id)
        provider = LocalGitRepositoryProvider(root, connection)
        repository = await provider.initialize(operation)
        (root / "value.txt").write_text("old\n", encoding="utf-8")
        old = await provider.commit(
            repository,
            "old",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )
        (root / "value.txt").write_text("new\n", encoding="utf-8")
        await provider.commit(
            repository,
            "new",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )

        registry = RepositoryRegistry()
        registry.register(RepositoryBinding(connection, repository, provider))
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        resolver = RepositoryWorkspaceSourceResolver(registry, files)
        resolved = await resolver.resolve(
            WorkspaceSourceRef(
                kind=WorkspaceSourceKind.REPOSITORY,
                ref=repository.id,
                revision=old.revision,
            ),
            data_context,
        )
        assert resolved.source_ref.revision == old.revision
        assert len(resolved.files) == 1

        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="repository-user"),
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=data_context,
            source_refs=(resolved.source_ref,),
            files=resolved.files,
        )
        materialization = await workspaces.materialize(workspace.id, data_context)
        assert (workspaces.local_path(materialization.id) / "value.txt").read_text() == "old\n"

        record = await files.get_file(resolved.files[0].file_id, data_context)
        assert record.metadata["repository_revision"] == old.revision
        assert record.metadata["repository_id"] == repository.id

    asyncio.run(scenario())


def test_push_is_denied_before_provider_side_effect(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = _operation(project_id)
        connection = _connection(project_id)
        provider = LocalGitRepositoryProvider(tmp_path / "repo", connection)
        repository = await provider.initialize(operation)
        registry = RepositoryRegistry()
        registry.register(RepositoryBinding(connection, repository, provider))
        authorization = AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref="user:repository-user",
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.READ}),
                    ),
                )
            )
        )
        service = RepositoryService(registry, authorization)
        with pytest.raises(ContractError) as error:
            await service.push(
                repository.id,
                RepositoryCallContext(operation, actor_ref="user:repository-user"),
            )
        assert error.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_secret_reference_and_external_identity_do_not_leak_local_clone_path(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        connection = _connection(project_id, with_secret=True)
        provider = LocalGitRepositoryProvider(tmp_path / "private-repo", connection)
        repository = await provider.initialize(_operation(project_id))
        assert connection.secret_references[0].secret_id == "git-token"
        payload = repository.to_dict()
        serialized = str(payload)
        assert str(tmp_path) not in serialized
        assert "git-token" not in serialized
        native = repository.external_resource.native_reference
        assert native.namespace == "local-git"
        assert native.native_id != str(tmp_path / "private-repo")

    asyncio.run(scenario())


def test_missing_git_binary_is_provider_unavailable(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        provider = LocalGitRepositoryProvider(
            tmp_path / "repo",
            _connection(project_id),
            git_binary=str(tmp_path / "missing-git"),
        )
        with pytest.raises(ContractError) as error:
            await provider.initialize(_operation(project_id))
        assert error.value.code is ErrorCode.UNAVAILABLE

    asyncio.run(scenario())


def test_provider_replacement_preserves_canonical_repository_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        connection = _connection(project_id)
        first_provider = LocalGitRepositoryProvider(tmp_path / "repo", connection)
        repository = await first_provider.initialize(_operation(project_id))
        registry = RepositoryRegistry()
        registry.register(RepositoryBinding(connection, repository, first_provider))

        replacement = LocalGitRepositoryProvider(
            tmp_path / "repo",
            connection,
            repository=repository,
            provider_id="local-git",
        )
        registry.replace(RepositoryBinding(connection, repository, replacement))
        binding = registry.resolve(repository.id)
        assert binding.reference.id == repository.id
        assert binding.reference.external_resource.resource_type == "repository"
        assert type(binding.reference).__name__ == "RepositoryReference"

    asyncio.run(scenario())
