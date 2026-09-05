"""Guarded compensation-capable reference Workspace providers."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .models import Workspace, WorkspaceStatus
from .reference import LocalWorkspaceProvider
from .sqlite import SqliteWorkspaceProvider


class CompensatingLocalWorkspaceProvider(LocalWorkspaceProvider):
    """Reference provider that can remove only a pristine, unused Workspace creation."""

    async def compensate_workspace(self, workspace_id: str) -> Workspace:
        validate_id(workspace_id, "workspace")
        async with self._lock:
            workspace = self._workspaces.get(workspace_id)
            if workspace is None or workspace.status is WorkspaceStatus.DELETED:
                raise ContractError(ErrorCode.NOT_FOUND, f"workspace not found: {workspace_id}")
            if workspace.revision != 0:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "workspace compensation refused after the Workspace revision changed",
                    details={"workspace_id": workspace_id, "revision": workspace.revision},
                )
            if workspace.active_task_ids or workspace.active_run_ids:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "workspace compensation refused while canonical Task/Run references exist",
                    details={"workspace_id": workspace_id},
                )
            if any(
                item.workspace_id == workspace_id for item in self._materializations.values()
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "workspace compensation refused while a materialization exists",
                    details={"workspace_id": workspace_id},
                )
            snapshots = tuple(
                item for item in self._snapshots.values() if item.workspace_id == workspace_id
            )
            if len(snapshots) != 1:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "workspace compensation requires exactly the pristine creation snapshot",
                    details={"workspace_id": workspace_id, "snapshot_count": len(snapshots)},
                )
            snapshot = snapshots[0]
            if (
                snapshot.id != workspace.base_snapshot_id
                or snapshot.parent_snapshot_id is not None
                or snapshot.revision != workspace.revision
                or snapshot.files
                or snapshot.artifact_ids
                or snapshot.source_refs
                or workspace.source_refs
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "workspace compensation refused because reusable creation state was modified",
                    details={"workspace_id": workspace_id},
                )
            self._heads.pop(workspace_id, None)
            self._snapshots.pop(snapshot.id, None)
            del self._workspaces[workspace_id]
            return workspace


class CompensatingSqliteWorkspaceProvider(
    SqliteWorkspaceProvider,
    CompensatingLocalWorkspaceProvider,
):
    """Restart-safe reference provider with durable guarded compensation."""

    async def compensate_workspace(self, workspace_id: str) -> Workspace:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            workspace = await CompensatingLocalWorkspaceProvider.compensate_workspace(
                self,
                workspace_id,
            )
            self._persist_or_restore(checkpoint)
            return workspace


__all__ = [
    "CompensatingLocalWorkspaceProvider",
    "CompensatingSqliteWorkspaceProvider",
]
