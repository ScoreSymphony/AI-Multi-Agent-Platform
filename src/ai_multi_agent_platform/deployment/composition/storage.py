"""Storage and repository-foundation construction for single-node deployment."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.kernel import SqliteKernelRepository
from ai_multi_agent_platform.repositories import (
    RepositoryEventRuntimeIngress,
    RepositoryRegistry,
    RepositoryWorkspaceExecutionCoordinator,
    SqliteRepositoryBindingCatalog,
    SqliteRepositoryProvenanceStore,
    restore_managed_local_repositories,
)
from ai_multi_agent_platform.workspaces import SqliteRunWorkspaceBindingRepository
from ai_multi_agent_platform.workspaces.compensation import CompensatingSqliteWorkspaceProvider

from ..config import SingleNodeConfig

_REFERENCE_EXECUTION_WORKSPACE = "reference"
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
    repository_catalog: SqliteRepositoryBindingCatalog
    repository_registry: RepositoryRegistry
    repository_provenance: SqliteRepositoryProvenanceStore
    repository_workspace_execution: RepositoryWorkspaceExecutionCoordinator
    repository_event_ingress: RepositoryEventRuntimeIngress
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
    repository_catalog = SqliteRepositoryBindingCatalog(
        database_dir / "repository-bindings.sqlite3"
    )
    repository_registry = RepositoryRegistry()
    restore_managed_local_repositories(repository_catalog, repository_registry)
    repository_provenance = SqliteRepositoryProvenanceStore(
        database_dir / "repository-provenance.sqlite3"
    )
    repository_workspace_execution = RepositoryWorkspaceExecutionCoordinator(
        run_workspace_bindings,
        workspaces,
        repository_provenance,
        fallback_workspace=_REFERENCE_EXECUTION_WORKSPACE,
    )
    repository_event_ingress = RepositoryEventRuntimeIngress(
        repository_registry,
        kernel_repository,
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
        repository_catalog=repository_catalog,
        repository_registry=repository_registry,
        repository_provenance=repository_provenance,
        repository_workspace_execution=repository_workspace_execution,
        repository_event_ingress=repository_event_ingress,
        evaluation_project_id=evaluation_project.id,
    )
