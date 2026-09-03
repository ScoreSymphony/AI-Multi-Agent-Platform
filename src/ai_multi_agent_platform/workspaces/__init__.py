"""Canonical workspace lifecycle, snapshots, and local materialization support."""

from .contracts import WorkspaceProvider
from .models import (
    CleanupReport,
    MaterializationOutcome,
    RemoteMaterializationRequest,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceChange,
    WorkspaceChangeKind,
    WorkspaceChangeSet,
    WorkspaceFile,
    WorkspaceMaterialization,
    WorkspaceRetention,
    WorkspaceSnapshot,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceStatus,
    WorkspaceType,
    validate_relative_path,
)
from .reference import LocalWorkspaceProvider
from .remote import (
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationResult,
    RemoteWorkspaceMaterializer,
)
from .retention import (
    RetentionManagedWorkspaceProvider,
    WorkspaceRetentionController,
    WorkspaceRetentionGuard,
    WorkspaceRetentionReport,
)
from .run_bindings import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
    RunWorkspaceBindingRepository,
    SqliteRunWorkspaceBindingRepository,
)
from .sqlite import SqliteWorkspaceProvider

__all__ = [
    "CleanupReport",
    "InMemoryRunWorkspaceBindingRepository",
    "LocalWorkspaceProvider",
    "MaterializationOutcome",
    "RemoteCleanupAcknowledgement",
    "RemoteMaterializationReceipt",
    "RemoteMaterializationRequest",
    "RemoteMaterializationResult",
    "RemoteWorkspaceMaterializer",
    "RetentionManagedWorkspaceProvider",
    "RunWorkspaceBinding",
    "RunWorkspaceBindingRepository",
    "SqliteRunWorkspaceBindingRepository",
    "SqliteWorkspaceProvider",
    "Workspace",
    "WorkspaceAccessMode",
    "WorkspaceChange",
    "WorkspaceChangeKind",
    "WorkspaceChangeSet",
    "WorkspaceFile",
    "WorkspaceMaterialization",
    "WorkspaceProvider",
    "WorkspaceRetention",
    "WorkspaceRetentionController",
    "WorkspaceRetentionGuard",
    "WorkspaceRetentionReport",
    "WorkspaceSnapshot",
    "WorkspaceSourceKind",
    "WorkspaceSourceRef",
    "WorkspaceStatus",
    "WorkspaceType",
    "validate_relative_path",
]
