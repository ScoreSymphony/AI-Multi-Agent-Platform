"""Worker-side composition helpers for distributed application builds."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

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

    #433 exposes ``workspace_id/snapshot_id`` only as a Worker-local opaque execution token.
    The application command executor is rooted one level lower so its own strict opaque-token
    boundary can continue to accept just ``snapshot_id``. No Worker-local filesystem path is
    serialized into canonical WorkerJob or Run state.
    """

    parts = PurePosixPath(execution_workspace).parts
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(
            "remote application Workspace token must contain workspace_id/snapshot_id"
        )
    workspace_id, snapshot_id = parts
    fallback = ExecutorLifecycleBackend(
        reference_executor,
        workspace=execution_workspace,
    )
    return ApplicationBuildWorkerLifecycleBackend(
        ApplicationCommandExecutor(Path(workspace_root) / workspace_id),
        workspace=snapshot_id,
        workspace_id=workspace_id,
        snapshot_id=snapshot_id,
        fallback=fallback,
    )


__all__ = ["application_workspace_lifecycle"]
