"""Outer product composition for the managed Application runtime.

Concrete runtime selection belongs at the adapter boundary.  The canonical
``applications`` package owns manifests, persistence, lifecycle and runtime
contracts; ``deployment`` only sees the provider-neutral startup-recovery seam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.applications import (
    ApplicationAuditLog,
    ApplicationLifecycleService,
    ApplicationRuntimeRegistry,
    LocalApplicationWorkspaceBinder,
    LocalProcessApplicationRuntime,
    SqliteApplicationAuditStore,
    SqliteApplicationRepository,
    register_application_audit_control_plane,
    register_application_control_plane,
    register_application_log_control_plane,
    register_application_resource_handlers,
)
from ai_multi_agent_platform.configuration import SecretProvider
from ai_multi_agent_platform.deployment import SingleNodeDeployment
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.deployment.startup_recovery import StartupRecoveryExtensionReport
from ai_multi_agent_platform.workspaces import WorkspaceProvider


@dataclass(slots=True)
class ApplicationRuntimeComposition:
    """Application-owned services exposed by the shipped single-node adapter."""

    repository: SqliteApplicationRepository
    runtimes: ApplicationRuntimeRegistry
    lifecycle: ApplicationLifecycleService
    audit: ApplicationAuditLog
    audit_store: SqliteApplicationAuditStore

    async def reconcile_startup(self) -> StartupRecoveryExtensionReport:
        """Reconcile durable desired state without leaking backend diagnostics."""

        report = await self.lifecycle.recover_all()
        return StartupRecoveryExtensionReport(
            name="applications",
            items_checked=len(report.instances),
            failures=tuple(
                {
                    "instance_id": failure.instance_id,
                    "runtime_id": failure.runtime_id,
                }
                for failure in report.failures
            ),
            # A managed Application may fail independently without making the
            # canonical Control Plane unavailable. Its failed observation stays
            # persisted and visible through the Application resources.
            ready_for_service=True,
        )


def compose_application_runtime(
    config: SingleNodeConfig,
    deployment: SingleNodeDeployment,
    *,
    secret_provider: SecretProvider | None,
    workspace_provider: WorkspaceProvider | None = None,
    workspace_local_path: Callable[[str], Path] | None = None,
) -> ApplicationRuntimeComposition:
    """Attach the reference local-process Application runtime to the shipped profile."""

    effective_workspaces = workspace_provider or deployment.workspaces
    effective_local_path = workspace_local_path or deployment.workspaces.local_path
    database_path = config.database_dir / "applications.sqlite3"
    repository = SqliteApplicationRepository(database_path)
    audit_store = SqliteApplicationAuditStore(database_path)
    audit = ApplicationAuditLog(audit_store, telemetry=deployment.telemetry)
    workspace_binder = LocalApplicationWorkspaceBinder(
        effective_workspaces,
        effective_local_path,
    )
    local_process = LocalProcessApplicationRuntime(
        secret_provider=secret_provider,
        workspace_binder=workspace_binder,
        resource_probe_path=config.data_dir,
    )
    runtimes = ApplicationRuntimeRegistry((local_process,))
    lifecycle = ApplicationLifecycleService(repository, runtimes)
    register_application_control_plane(
        deployment.control_plane,
        lifecycle,
        repository,
    )
    register_application_log_control_plane(
        deployment.control_plane,
        lifecycle,
        repository,
    )
    register_application_resource_handlers(
        deployment.control_plane,
        repository,
    )
    register_application_audit_control_plane(
        deployment.control_plane,
        audit,
        audit_store,
    )
    composition = ApplicationRuntimeComposition(
        repository=repository,
        runtimes=runtimes,
        lifecycle=lifecycle,
        audit=audit,
        audit_store=audit_store,
    )
    deployment.startup_recovery_extensions = (
        *deployment.startup_recovery_extensions,
        composition,
    )
    return composition


__all__ = ["ApplicationRuntimeComposition", "compose_application_runtime"]
