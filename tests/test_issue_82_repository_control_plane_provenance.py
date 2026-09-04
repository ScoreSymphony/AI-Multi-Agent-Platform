from __future__ import annotations

import asyncio
from pathlib import Path

from control_plane_contract_helpers import api_headers

from ai_multi_agent_platform.connectors import Connection
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.repositories import (
    LocalGitRepositoryProvider,
    RepositoryBinding,
    RepositoryConnection,
    RepositoryProvenanceStore,
    RepositoryRegistry,
    RepositoryRunIntegration,
    RepositoryWorkspaceSourceResolver,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    SqliteRunWorkspaceBindingRepository,
    SqliteWorkspaceProvider,
)


def test_control_plane_records_repository_input_before_start_and_on_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        kernel_repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=kernel_repository,
        )
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        metadata_db = tmp_path / "workspaces.sqlite"
        workspaces = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            metadata_db,
        )
        bindings = SqliteRunWorkspaceBindingRepository(metadata_db)
        control_plane = ControlPlane(
            kernel=kernel,
            events=kernel_repository,
            workspace_provider=workspaces,
            run_workspace_bindings=bindings,
        )
        http = ControlPlaneHTTP(control_plane)

        project_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=api_headers(idempotency_key="issue-82-project"),
                body={
                    "name": "Repository provenance project",
                    "owner_type": "user",
                    "owner_id": "repository-user",
                },
            )
        )
        assert project_response.status == 201
        assert isinstance(project_response.body, dict)
        project_id = project_response.body["id"]
        assert isinstance(project_id, str)

        operation = OperationContext(
            correlation_id="issue-82-repository-control-plane",
            owner_type="user",
            owner_id="repository-user",
            project_id=project_id,
        )
        connection = RepositoryConnection(
            connection=Connection(
                id=new_id("connection"),
                connector_type_id="local-git",
                connector_version="1.0",
                owner_type="user",
                owner_id="repository-user",
                display_name="Repository control-plane fixture",
                project_id=project_id,
            ),
            provider_id="local-git",
            local=True,
        )
        repository_root = tmp_path / "repo"
        git = LocalGitRepositoryProvider(repository_root, connection)
        repository = await git.initialize(operation)
        (repository_root / "value.txt").write_text("input\n", encoding="utf-8")
        input_commit = await git.commit(
            repository,
            "input",
            operation,
            author_name="Repository Test",
            author_email="repository@example.invalid",
        )

        repositories = RepositoryRegistry()
        repositories.register(RepositoryBinding(connection, repository, git))
        resolvers = control_plane.workspace_source_resolvers
        assert resolvers is not None
        resolvers.register(RepositoryWorkspaceSourceResolver(repositories, files))
        provenance = RepositoryProvenanceStore()
        control_plane.configure_repository_run_integration(
            RepositoryRunIntegration(
                repositories,
                provenance,
                workspaces,
                files,
                kernel,
            )
        )

        workspace_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="issue-82-workspace"),
                body={
                    "project_id": project_id,
                    "workspace_type": "isolated_run",
                    "source_refs": [
                        {
                            "kind": "repository",
                            "ref": repository.id,
                            "revision": "main",
                        }
                    ],
                },
            )
        )
        assert workspace_response.status == 201
        assert isinstance(workspace_response.body, dict)
        workspace_id = workspace_response.body["id"]
        snapshot_id = workspace_response.body["base_snapshot_id"]
        source_refs = workspace_response.body["source_refs"]
        assert isinstance(workspace_id, str)
        assert isinstance(snapshot_id, str)
        assert isinstance(source_refs, list)
        assert isinstance(source_refs[0], dict)
        assert source_refs[0]["revision"] == input_commit.revision

        task_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=api_headers(idempotency_key="issue-82-task"),
                body={
                    "title": "Repository-backed task",
                    "objective": "Prove exact repository Run provenance",
                    "owner_type": "user",
                    "owner_id": "repository-user",
                    "project_id": project_id,
                },
            )
        )
        assert task_response.status == 201
        assert isinstance(task_response.body, dict)
        task_id = task_response.body["id"]
        assert isinstance(task_id, str)
        queued = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=api_headers(idempotency_key="issue-82-queue"),
            )
        )
        assert queued.status == 200

        started = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="issue-82-start"),
                body={
                    "workspace_id": workspace_id,
                    "workspace_snapshot_id": snapshot_id,
                },
            )
        )
        assert started.status == 200
        assert isinstance(started.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)
        recorded = provenance.get(run_id, repository.id)
        assert recorded is not None
        assert recorded.task_id == task_id
        assert recorded.input_revision == input_commit.revision
        assert recorded.branch_ref == "main"
        assert recorded.actor_ref
        assert recorded.agent_id is None

        await kernel.record_run_outcome(
            idempotency_key="issue-82-fail-run",
            task_id=task_id,
            run_id=run_id,
            status=RunStatus.FAILED,
        )
        retried = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:retry",
                headers=api_headers(idempotency_key="issue-82-retry"),
            )
        )
        assert retried.status == 200
        assert isinstance(retried.body, dict)
        retry_run_id = retried.body["id"]
        assert isinstance(retry_run_id, str)
        assert retry_run_id != run_id
        retry_recorded = provenance.get(retry_run_id, repository.id)
        assert retry_recorded is not None
        assert retry_recorded.input_revision == input_commit.revision
        assert retry_recorded.branch_ref == "main"
        assert retry_recorded.task_id == task_id

    asyncio.run(scenario())
