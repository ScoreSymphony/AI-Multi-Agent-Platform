"""Replaceable workspace lifecycle and materialization contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    CleanupReport,
    MaterializationOutcome,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceChangeSet,
    WorkspaceFile,
    WorkspaceMaterialization,
    WorkspaceRetention,
    WorkspaceSnapshot,
    WorkspaceSourceRef,
    WorkspaceType,
)


class WorkspaceProvider(ABC):
    """Platform-owned workspace seam; implementations must keep host paths private."""

    @abstractmethod
    async def create_workspace(
        self,
        *,
        project_id: str,
        owner_ref: OwnerRef,
        workspace_type: WorkspaceType,
        context: DataAccessContext,
        access_mode: WorkspaceAccessMode = WorkspaceAccessMode.READ_WRITE,
        retention: WorkspaceRetention = WorkspaceRetention.PERSISTENT,
        source_refs: tuple[WorkspaceSourceRef, ...] = (),
        files: tuple[WorkspaceFile, ...] = (),
        workspace_id: str | None = None,
    ) -> Workspace: ...

    @abstractmethod
    async def get_workspace(self, workspace_id: str) -> Workspace: ...

    @abstractmethod
    async def list_workspaces(self, *, project_id: str | None = None) -> tuple[Workspace, ...]: ...

    @abstractmethod
    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot: ...

    @abstractmethod
    async def create_snapshot(self, workspace_id: str) -> WorkspaceSnapshot: ...

    @abstractmethod
    async def materialize(
        self,
        workspace_id: str,
        context: DataAccessContext,
        *,
        snapshot_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> WorkspaceMaterialization: ...

    @abstractmethod
    async def capture_changes(
        self,
        materialization_id: str,
        context: DataAccessContext,
    ) -> WorkspaceChangeSet: ...

    @abstractmethod
    async def commit_changes(
        self,
        materialization_id: str,
        context: DataAccessContext,
        *,
        expected_revision: int,
    ) -> WorkspaceSnapshot: ...

    @abstractmethod
    async def release_materialization(
        self,
        materialization_id: str,
        outcome: MaterializationOutcome,
    ) -> None: ...

    @abstractmethod
    async def cleanup(self) -> CleanupReport: ...
