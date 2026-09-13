"""Canonical workspace lifecycle, snapshots, and local materialization support."""

from .async_sqlite import SqliteRunWorkspaceBindingRepository, SqliteWorkspaceProvider
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
    validate_sha256,
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
)
from .sources import (
    EmptyWorkspaceSourceResolver,
    ResolvedWorkspaceSource,
    SnapshotWorkspaceSourceResolver,
    WorkspaceSourceResolver,
    WorkspaceSourceResolverRegistry,
)

__all__ = [
    "CleanupReport",
    "EmptyWorkspaceSourceResolver",
    "InMemoryRunWorkspaceBindingRepository",
    "LocalWorkspaceProvider",
    "MaterializationOutcome",
    "RemoteCleanupAcknowledgement",
    "RemoteMaterializationReceipt",
    "RemoteMaterializationRequest",
    "RemoteMaterializationResult",
    "RemoteWorkspaceMaterializer",
    "ResolvedWorkspaceSource",
    "RetentionManagedWorkspaceProvider",
    "RunWorkspaceBinding",
    "RunWorkspaceBindingRepository",
    "SnapshotWorkspaceSourceResolver",
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
    "WorkspaceSourceResolver",
    "WorkspaceSourceResolverRegistry",
    "WorkspaceStatus",
    "WorkspaceType",
    "validate_relative_path",
    "validate_sha256",
]
