"""Repository-domain builders for staged single-node construction."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.repositories import (
    RepositoryDiscoveryResolver,
    RepositoryEventRuntimeIngress,
    RepositoryManagementService,
    RepositoryRunIntegration,
    RepositoryService,
    RepositoryWorkspaceExecutionCoordinator,
)

from ..config import SingleNodeConfig
from .foundation import SecurityBundle, StorageBundle

_REFERENCE_EXECUTION_WORKSPACE = "reference"


@dataclass(frozen=True, slots=True)
class RepositoryFoundationBundle:
    """Pre-kernel repository services and execution hooks."""

    repositories: RepositoryService
    repository_management: RepositoryManagementService
    repository_workspace_execution: RepositoryWorkspaceExecutionCoordinator
    repository_event_ingress: RepositoryEventRuntimeIngress


@dataclass(frozen=True, slots=True)
class RepositoryRuntimeBundle:
    """Kernel-bound repository runtime integration."""

    repository_run_integration: RepositoryRunIntegration


def build_repository_foundation(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    *,
    repository_discovery_resolver: RepositoryDiscoveryResolver | None = None,
) -> RepositoryFoundationBundle:
    """Build repository authorities that must exist before execution and the kernel."""

    repository_workspace_execution = RepositoryWorkspaceExecutionCoordinator(
        storage.run_workspace_bindings,
        storage.workspaces,
        storage.repository_provenance,
        fallback_workspace=_REFERENCE_EXECUTION_WORKSPACE,
    )
    repository_event_ingress = RepositoryEventRuntimeIngress(
        storage.repository_registry,
        storage.kernel_repository,
    )
    repositories = RepositoryService(
        storage.repository_registry,
        security.approval_gate,
    )
    repository_management = RepositoryManagementService(
        storage.repository_registry,
        storage.repository_catalog,
        security.approval_gate,
        managed_local_root=config.repositories_dir,
        discovery_resolver=repository_discovery_resolver,
    )
    return RepositoryFoundationBundle(
        repositories=repositories,
        repository_management=repository_management,
        repository_workspace_execution=repository_workspace_execution,
        repository_event_ingress=repository_event_ingress,
    )


def build_repository_runtime(
    storage: StorageBundle,
    foundation: RepositoryFoundationBundle,
    kernel: PlatformKernel,
) -> RepositoryRuntimeBundle:
    """Complete the named post-kernel repository binding phase exactly once."""

    integration = RepositoryRunIntegration(
        storage.repository_registry,
        storage.repository_provenance,
        storage.workspaces,
        storage.files,
        kernel,
    )
    foundation.repository_workspace_execution.configure_run_integration(integration)
    return RepositoryRuntimeBundle(repository_run_integration=integration)
