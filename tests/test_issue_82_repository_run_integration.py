from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
    RepositoryProvenanceStore,
    RepositoryRegistry,
    RepositoryRunIntegration,
    RepositoryWorkspaceSourceResolver,
)
from ai_multi_agent_platform.workspaces import (
    LocalWorkspaceProvider,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceType,
)


class _ArtifactKernel:
    def __init__(self) -> None:
        self.artifact_ids: list[str] = []

    async def attach_artifact(self, **kwargs: object) -> None:
        artifact_id = kwargs["artifact_id"]
        assert isinstance(artifact_id, str)
        self.artifact_ids.append(artifact_id)


def _operation(project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-82-run-integration",
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
            display_name="Local Git run fixture",
            project_id=project_id,
        ),
        provider_id="local-git",
        local=True,
    )


def test_repository_run_records_exact_input_and_returns_changed_file_artifacts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        task_id = new_id("task")
        run_id = new_id("run")
        operation = _operation(project_id)
        data_context = DataAccessContext(operation=operation, actor_ref="user:repository-user")

        repository_root = tmp_path / "repo"
        connection = _connection(project_id)
        provider = LocalGitRepositoryProvider(repository_root, connection)
        repository = await provider.initialize(operation)
        (repository_root / "value.txt").write_text("input\n", encoding="utf-8")
        input_commit = await provider.commit(
            repository,
            "input",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )

        repositories = RepositoryRegistry()
        repositories.register(RepositoryBinding(connection, repository, provider))
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        resolver = RepositoryWorkspaceSourceResolver(repositories, files)
        resolved = await resolver.resolve(
            WorkspaceSourceRef(
                kind=WorkspaceSourceKind.REPOSITORY,
                ref=repository.id,
                revision=input_commit.revision,
            ),
            data_context,
        )

        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="repository-user"),
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=data_context,
            source_refs=(resolved.source_ref,),
            files=resolved.files,
        )
        assert workspace.base_snapshot_id is not None
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)

        provenance = RepositoryProvenanceStore()
        kernel = _ArtifactKernel()
        integration = RepositoryRunIntegration(
            repositories,
            provenance,
            workspaces,
            files,
            kernel,  # type: ignore[arg-type]
        )
        inputs = await integration.record_input_snapshot(
            run_id=run_id,
            task_id=task_id,
            snapshot=snapshot,
            actor_ref="user:repository-user",
            context=data_context,
        )
        assert len(inputs) == 1
        assert inputs[0].repository_id == repository.id
        assert inputs[0].input_revision == input_commit.revision
        assert inputs[0].branch_ref == input_commit.revision

        materialization = await workspaces.materialize(
            workspace.id,
            data_context,
            snapshot_id=snapshot.id,
            task_id=task_id,
            run_id=run_id,
        )
        materialized_root = workspaces.local_path(materialization.id)
        (materialized_root / "value.txt").write_text("changed\n", encoding="utf-8")
        (materialized_root / "new.txt").write_text("new\n", encoding="utf-8")

        bundle = await integration.capture_workspace_changes(
            run_id=run_id,
            task_id=task_id,
            materialization_id=materialization.id,
            actor_ref="user:repository-user",
            context=data_context,
        )
        changed_paths = {change.relative_path for change in bundle.change_set.changes}
        assert changed_paths == {"new.txt", "value.txt"}
        assert bundle.manifest_artifact_id is not None
        assert len(bundle.artifact_ids) == 3
        assert set(kernel.artifact_ids) == set(bundle.artifact_ids)

        current = provenance.get(run_id, repository.id)
        assert current is not None
        assert current.input_revision == input_commit.revision
        assert set(current.diff_artifact_ids) == set(bundle.artifact_ids)
        assert current.output_revision is None

        (repository_root / "value.txt").write_text("changed\n", encoding="utf-8")
        output_commit = await provider.commit(
            repository,
            "output",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )
        updated = integration.record_output_revision(
            run_id=run_id,
            repository_id=repository.id,
            output_revision=output_commit.revision,
            artifact_ids=bundle.artifact_ids,
        )
        assert updated.output_revision == output_commit.revision
        assert updated.input_revision == input_commit.revision
        assert set(updated.diff_artifact_ids) == set(bundle.artifact_ids)
        assert provenance.for_run(run_id) == (updated,)

    asyncio.run(scenario())


def test_run_input_recovers_materialized_sha_when_snapshot_keeps_symbolic_ref(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        run_id = new_id("run")
        operation = _operation(project_id)
        data_context = DataAccessContext(operation=operation, actor_ref="user:repository-user")
        repository_root = tmp_path / "repo"
        connection = _connection(project_id)
        provider = LocalGitRepositoryProvider(repository_root, connection)
        repository = await provider.initialize(operation)
        (repository_root / "value.txt").write_text("input\n", encoding="utf-8")
        input_commit = await provider.commit(
            repository,
            "input",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )

        repositories = RepositoryRegistry()
        repositories.register(RepositoryBinding(connection, repository, provider))
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        resolver = RepositoryWorkspaceSourceResolver(repositories, files)
        resolved = await resolver.resolve(
            WorkspaceSourceRef(
                kind=WorkspaceSourceKind.REPOSITORY,
                ref=repository.id,
                revision="main",
            ),
            data_context,
        )
        assert resolved.source_ref.revision == input_commit.revision

        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="repository-user"),
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=data_context,
            source_refs=(
                WorkspaceSourceRef(
                    kind=WorkspaceSourceKind.REPOSITORY,
                    ref=repository.id,
                    revision="main",
                ),
            ),
            files=resolved.files,
        )
        assert workspace.base_snapshot_id is not None
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)

        provenance = RepositoryProvenanceStore()
        integration = RepositoryRunIntegration(
            repositories,
            provenance,
            workspaces,
            files,
            _ArtifactKernel(),  # type: ignore[arg-type]
        )
        inputs = await integration.record_input_snapshot(
            run_id=run_id,
            snapshot=snapshot,
            actor_ref="user:repository-user",
            context=data_context,
        )
        assert inputs[0].input_revision == input_commit.revision
        assert inputs[0].branch_ref == "main"

    asyncio.run(scenario())


def test_repository_provider_exposes_common_provider_descriptor(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        provider = LocalGitRepositoryProvider(tmp_path / "repo", _connection(project_id))
        assert provider.descriptor.provider_id == "local-git"
        assert provider.descriptor.provider_type == "repository"
        assert await provider.discover_capabilities() == ()

    asyncio.run(scenario())
