from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.contracts import ExecutionRequest as KernelExecutionRequest
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
    RepositoryProvenanceStore,
    RepositoryRegistry,
    RepositoryRunIntegration,
    RepositoryWorkspaceExecutionCoordinator,
    RepositoryWorkspaceSourceResolver,
)
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    RunWorkspaceBinding,
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


def test_repository_bound_run_executes_in_materialization_and_captures_completion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        task_id = new_id("task")
        run_id = new_id("run")
        operation = OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="repository-user",
            project_id=project_id,
        )
        data_context = DataAccessContext(
            operation=operation,
            actor_ref="user:repository-user",
            task_id=task_id,
            run_id=run_id,
        )

        repository_root = tmp_path / "repository"
        connection = RepositoryConnection(
            connection=Connection(
                id=new_id("connection"),
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type="user",
                owner_id="repository-user",
                display_name="Execution lifecycle repository",
                project_id=project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        repository_provider = LocalGitRepositoryProvider(repository_root, connection)
        repository = await repository_provider.initialize(operation)
        (repository_root / "input.txt").write_text("immutable input\n", encoding="utf-8")
        input_commit = await repository_provider.commit(
            repository,
            "input",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )

        repositories = RepositoryRegistry()
        repositories.register(RepositoryBinding(connection, repository, repository_provider))
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        resolved = await RepositoryWorkspaceSourceResolver(repositories, files).resolve(
            WorkspaceSourceRef(
                kind=WorkspaceSourceKind.REPOSITORY,
                ref=repository.id,
                revision=input_commit.revision,
            ),
            data_context,
        )
        workspaces = LocalWorkspaceProvider(tmp_path / "materializations", files)
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

        bindings = InMemoryRunWorkspaceBindingRepository()
        await bindings.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=task_id,
                workspace_id=workspace.id,
                workspace_snapshot_id=snapshot.id,
                content_checksum=snapshot.content_checksum,
            )
        )
        provenance = RepositoryProvenanceStore()
        artifact_kernel = _ArtifactKernel()
        integration = RepositoryRunIntegration(
            repositories,
            provenance,
            workspaces,
            files,
            artifact_kernel,  # type: ignore[arg-type]
        )
        await integration.record_input_snapshot(
            run_id=run_id,
            task_id=task_id,
            snapshot=snapshot,
            actor_ref="user:repository-user",
            context=data_context,
        )

        coordinator = RepositoryWorkspaceExecutionCoordinator(
            bindings,
            workspaces,
            provenance,
            fallback_workspace="reference",
        )
        coordinator.configure_run_integration(integration)
        lifecycle = ExecutorLifecycleBackend(
            ReferenceExecutor(workspaces.materialization_root),
            workspace="reference",
            action="write_artifact",
            workspace_resolver=coordinator.resolve_execution_workspace,
            terminal_result_observer=coordinator.observe_terminal_result,
        )
        request = KernelExecutionRequest(
            run_id=run_id,
            subject_type="task",
            subject_id=task_id,
            context=operation,
        )

        handle = await lifecycle.start(request)
        assert handle.run_id == run_id
        materialization_dirs = tuple(workspaces.materialization_root.iterdir())
        assert len(materialization_dirs) == 1
        execution_root = materialization_dirs[0]
        assert (execution_root / "input.txt").read_text(encoding="utf-8") == "immutable input\n"
        assert (execution_root / "artifact.txt").exists()

        snapshot_result = await lifecycle.get(run_id, operation)
        assert snapshot_result.status.value == "succeeded"
        assert tuple(workspaces.materialization_root.iterdir()) == ()

        recorded = provenance.get(run_id, repository.id)
        assert recorded is not None
        assert recorded.input_revision == input_commit.revision
        assert recorded.output_revision is None
        assert len(recorded.diff_artifact_ids) == 2
        assert set(recorded.diff_artifact_ids) == set(artifact_kernel.artifact_ids)

        second = await lifecycle.get(run_id, operation)
        assert second == snapshot_result
        assert len(artifact_kernel.artifact_ids) == 2

    asyncio.run(scenario())
