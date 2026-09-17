"""Application Adapter composition for the normal single-node runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.applications import (
    ApplicationLifecycleService,
    ApplicationRuntimeRegistry,
    LocalApplicationWorkspaceBinder,
    LocalProcessApplicationRuntime,
    SqliteApplicationRepository,
    register_application_control_plane,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from ..config import SingleNodeConfig


@dataclass(frozen=True, slots=True)
class ApplicationRuntimeBundle:
    """Durable Application state plus the normal single-node runtime registry."""

    repository: SqliteApplicationRepository
    runtimes: ApplicationRuntimeRegistry
    lifecycle: ApplicationLifecycleService


def build_application_runtime(
    config: SingleNodeConfig,
    *,
    workspace_provider: WorkspaceProvider,
    workspace_local_path: Callable[[str], Path],
    secret_provider: SecretProvider | None,
) -> ApplicationRuntimeBundle:
    """Build the durable local Application runtime without exposing host-private paths."""

    repository = SqliteApplicationRepository(config.database_dir / "applications.sqlite3")
    workspace_binder = LocalApplicationWorkspaceBinder(
        workspace_provider,
        workspace_local_path,
    )
    local_process = LocalProcessApplicationRuntime(
        secret_provider=secret_provider,
        workspace_binder=workspace_binder,
    )
    runtimes = ApplicationRuntimeRegistry((local_process,))
    lifecycle = ApplicationLifecycleService(repository, runtimes)
    return ApplicationRuntimeBundle(
        repository=repository,
        runtimes=runtimes,
        lifecycle=lifecycle,
    )


def register_application_runtime(
    control_plane: ControlPlane,
    applications: ApplicationRuntimeBundle,
) -> None:
    """Expose the composed Application lifecycle through the canonical Control Plane."""

    register_application_control_plane(
        control_plane,
        applications.lifecycle,
        applications.repository,
    )


__all__ = [
    "ApplicationRuntimeBundle",
    "build_application_runtime",
    "register_application_runtime",
]
