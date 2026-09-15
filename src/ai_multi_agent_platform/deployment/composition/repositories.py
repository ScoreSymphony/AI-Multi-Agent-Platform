"""Explicit pre-kernel and post-kernel repository composition stages."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.repositories import (
    RepositoryDiscoveryResolver,
    RepositoryEventRuntimeIngress,
    RepositoryManagementService,
    RepositoryRegistry,
    RepositoryRunIntegration,
    RepositoryService,
    RepositoryWorkspaceExecutionCoordinator,
    SqliteRepositoryBindingCatalog,
    SqliteRepositoryProvenanceStore,
    restore_managed_local_repositories,
)

from ..config import SingleNodeConfig
from .security import SecurityBundle
from .storage import StorageBundle

_REFERENCE_EXECUTION_WORKSPACE = "reference"


@dataclass(frozen=True, slots=True)
class RepositoryFoundationBundle:
    """Repository authorities that must exist before the canonical kernel."""

    registry: RepositoryRegistry
    catalog: SqliteRepositoryBindingCatalog
    provenance: SqliteRepositoryProvenanceStore
    services: RepositoryService
    management: RepositoryManagementService
    workspace_execution: RepositoryWorkspaceExecutionCoordinator
    event_ingress: RepositoryEventRuntimeIngress


@dataclass(frozen=True, slots=True)
class RepositoryRuntimeBundle:
    """Kernel-bound repository runtime assembled after kernel construction."""

    run_integration: RepositoryRunIntegration


def build_repository_foundation(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    *,
    discovery_resolver: RepositoryDiscoveryResolver | None = None,
) -> RepositoryFoundationBundle:
    """Build repository persistence, management and execution hooks without a kernel dependency."""

    database_dir = config.database_dir
    catalog = SqliteRepositoryBindingCatalog(database_dir / "repository-bindings.sqlite3")
    registry = RepositoryRegistry()
    restore_managed_local_repositories(catalog, registry)
    provenance = SqliteRepositoryProvenanceStore(database_dir / "repository-provenance.sqlite3")
    workspace_execution = RepositoryWorkspaceExecutionCoordinator(
        storage.run_workspace_bindings,
        storage.workspaces,
        provenance,
        fallback_workspace=_REFERENCE_EXECUTION_WORKSPACE,
    )
    event_ingress = RepositoryEventRuntimeIngress(registry, storage.kernel_repository)
    services = RepositoryService(registry, security.approval_gate)
    management = RepositoryManagementService(
        registry,
        catalog,
        security.approval_gate,
        managed_local_root=config.repositories_dir,
        discovery_resolver=discovery_resolver,
    )
    return RepositoryFoundationBundle(
        registry=registry,
        catalog=catalog,
        provenance=provenance,
        services=services,
        management=management,
        workspace_execution=workspace_execution,
        event_ingress=event_ingress,
    )


def build_repository_runtime(
    storage: StorageBundle,
    foundation: RepositoryFoundationBundle,
    kernel: PlatformKernel,
) -> RepositoryRuntimeBundle:
    """Complete repository execution wiring once the canonical kernel exists."""

    run_integration = RepositoryRunIntegration(
        foundation.registry,
        foundation.provenance,
        storage.workspaces,
        storage.files,
        kernel,
    )
    foundation.workspace_execution.configure_run_integration(run_integration)
    return RepositoryRuntimeBundle(run_integration=run_integration)
