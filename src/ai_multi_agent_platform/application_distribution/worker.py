"""Worker-side composition helpers for distributed application builds."""

from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.contracts import LifecycleBackend
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor

from .distributed_execution import ApplicationBuildWorkerLifecycleBackend
from .execution import ApplicationCommandExecutor


def application_workspace_lifecycle(
    workspace_root: str | Path,
    reference_executor: ReferenceExecutor,
    execution_workspace: str,
) -> LifecycleBackend:
    """Route a materialized Worker Workspace to app-build or ordinary execution.

    The physical Worker-local materialization token stays opaque. Canonical Workspace/Snapshot
    identity is carried only as in-process metadata by the Worker binding layer and is never derived
    from or serialized as a filesystem path.
    """

    workspace_id = getattr(execution_workspace, "workspace_id", None)
    snapshot_id = getattr(execution_workspace, "snapshot_id", None)
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise ValueError("remote application Workspace token is missing canonical workspace_id")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError("remote application Workspace token is missing canonical snapshot_id")
    if (
        not execution_workspace.strip()
        or execution_workspace in {".", ".."}
        or "/" in execution_workspace
        or "\\" in execution_workspace
    ):
        raise ValueError("remote application Workspace token must be opaque")

    fallback = ExecutorLifecycleBackend(
        reference_executor,
        workspace=execution_workspace,
    )
    return ApplicationBuildWorkerLifecycleBackend(
        ApplicationCommandExecutor(Path(workspace_root)),
        workspace=execution_workspace,
        workspace_id=workspace_id,
        snapshot_id=snapshot_id,
        fallback=fallback,
    )


__all__ = ["application_workspace_lifecycle"]
