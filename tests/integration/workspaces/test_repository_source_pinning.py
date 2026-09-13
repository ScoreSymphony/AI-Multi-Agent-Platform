from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from control_plane_contract_helpers import api_headers

from ai_multi_agent_platform.control_plane import (
    ControlPlane as ComposedControlPlane,
)
from ai_multi_agent_platform.control_plane import (
    ControlPlaneHTTP as ComposedControlPlaneHTTP,
)
from ai_multi_agent_platform.control_plane import HTTPRequest, HTTPResponse
from ai_multi_agent_platform.control_plane.workspace_contract import (
    ControlPlane as WorkspaceControlPlane,
)
from ai_multi_agent_platform.control_plane.workspace_contract import (
    ControlPlaneHTTP as WorkspaceControlPlaneHTTP,
)
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    ResolvedWorkspaceSource,
    SqliteWorkspaceProvider,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceSourceResolver,
)

_SHA = "b" * 40


class _HTTPHandler(Protocol):
    async def handle(self, request: HTTPRequest) -> HTTPResponse: ...


class _RepositoryPinningResolver(WorkspaceSourceResolver):
    @property
    def kind(self) -> WorkspaceSourceKind:
        return WorkspaceSourceKind.REPOSITORY

    async def resolve(
        self,
        source_ref: WorkspaceSourceRef,
        context: DataAccessContext,
    ) -> ResolvedWorkspaceSource:
        del context
        assert source_ref.revision == "main"
        return ResolvedWorkspaceSource(
            source_ref=replace(
                source_ref,
                revision=_SHA,
                metadata={**dict(source_ref.metadata), "requested_revision": "main"},
            )
        )


def _workspace_stack(tmp_path: Path) -> tuple[_HTTPHandler, SqliteWorkspaceProvider]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
    workspaces = SqliteWorkspaceProvider(
        tmp_path / "materializations",
        files,
        tmp_path / "workspaces.sqlite",
    )
    control_plane = WorkspaceControlPlane(
        kernel=kernel,
        events=repository,
        workspace_provider=workspaces,
    )
    resolvers = control_plane.workspace_source_resolvers
    assert resolvers is not None
    resolvers.register(_RepositoryPinningResolver())
    return WorkspaceControlPlaneHTTP(control_plane), workspaces


def _composed_stack(tmp_path: Path) -> tuple[_HTTPHandler, SqliteWorkspaceProvider]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
    workspaces = SqliteWorkspaceProvider(
        tmp_path / "materializations",
        files,
        tmp_path / "workspaces.sqlite",
    )
    control_plane = ComposedControlPlane(
        kernel=kernel,
        events=repository,
        workspace_provider=workspaces,
    )
    resolvers = control_plane.workspace_source_resolvers
    assert resolvers is not None
    resolvers.register(_RepositoryPinningResolver())
    return ComposedControlPlaneHTTP(control_plane), workspaces


def test_control_plane_persists_resolved_repository_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        stacks = (
            _workspace_stack(tmp_path / "workspace-contract"),
            _composed_stack(tmp_path / "composed"),
        )
        for index, (http, provider) in enumerate(stacks):
            project_response = await http.handle(
                HTTPRequest(
                    method="POST",
                    path="/api/v1/projects",
                    headers=api_headers(idempotency_key=f"project-create-{index}"),
                    body={
                        "name": "Pinned repository project",
                        "owner_type": "user",
                        "owner_id": "repository-user",
                    },
                )
            )
            assert project_response.status == 201
            assert isinstance(project_response.body, dict)
            project_id = project_response.body["id"]
            assert isinstance(project_id, str)

            created = await http.handle(
                HTTPRequest(
                    method="POST",
                    path="/api/v1/workspaces",
                    headers=api_headers(idempotency_key=f"workspace-create-{index}"),
                    body={
                        "project_id": project_id,
                        "workspace_type": "isolated_run",
                        "source_refs": [
                            {
                                "kind": "repository",
                                "ref": "external_resource_repository-fixture",
                                "revision": "main",
                            }
                        ],
                    },
                )
            )
            assert created.status == 201
            assert isinstance(created.body, dict)
            source_refs = created.body["source_refs"]
            assert isinstance(source_refs, list) and len(source_refs) == 1
            assert isinstance(source_refs[0], dict)
            assert source_refs[0]["revision"] == _SHA
            metadata = source_refs[0]["metadata"]
            assert isinstance(metadata, dict)
            assert metadata["requested_revision"] == "main"

            workspace_id = created.body["id"]
            assert isinstance(workspace_id, str)
            workspace = await provider.get_workspace(workspace_id)
            assert workspace.source_refs[0].revision == _SHA
            assert workspace.source_refs[0].metadata["requested_revision"] == "main"

    asyncio.run(scenario())
