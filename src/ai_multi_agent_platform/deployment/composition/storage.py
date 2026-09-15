"""Durable storage foundation for single-node deployment composition."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.kernel import SqliteKernelRepository
from ai_multi_agent_platform.workspaces import SqliteRunWorkspaceBindingRepository
from ai_multi_agent_platform.workspaces.compensation import CompensatingSqliteWorkspaceProvider

from ..config import SingleNodeConfig

_EVALUATION_PROJECT_KEY = "evaluation-system-project-v1"
_EVALUATION_OWNER_ID = "evaluation-single-node"


@dataclass(frozen=True, slots=True)
class StorageBundle:
    """Durable storage authorities needed before higher-level services are composed."""

    kernel_repository: SqliteKernelRepository
    scopes: SqliteScopeStore
    files: LocalFileProvider
    workspaces: CompensatingSqliteWorkspaceProvider
    run_workspace_bindings: SqliteRunWorkspaceBindingRepository
    evaluation_project_id: str


def build_storage(config: SingleNodeConfig) -> StorageBundle:
    """Build the persistent storage foundation without constructing runtime services."""

    database_dir = config.database_dir
    kernel_repository = SqliteKernelRepository(database_dir / "kernel.sqlite3")
    scopes = SqliteScopeStore(database_dir / "scopes.sqlite3")
    files = LocalFileProvider(config.files_dir, database_dir / "files.sqlite3")
    workspaces = CompensatingSqliteWorkspaceProvider(
        config.workspaces_dir,
        files,
        database_dir / "workspaces.sqlite3",
    )
    run_workspace_bindings = SqliteRunWorkspaceBindingRepository(
        database_dir / "run-workspace-bindings.sqlite3"
    )
    evaluation_project = scopes.create_project(
        key=_EVALUATION_PROJECT_KEY,
        name="Platform Evaluation",
        owner_type="service",
        owner_id=_EVALUATION_OWNER_ID,
    )

    return StorageBundle(
        kernel_repository=kernel_repository,
        scopes=scopes,
        files=files,
        workspaces=workspaces,
        run_workspace_bindings=run_workspace_bindings,
        evaluation_project_id=evaluation_project.id,
    )
