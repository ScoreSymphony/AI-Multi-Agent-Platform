"""Canonical workspace, snapshot, materialization, and change-set models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id


class WorkspaceType(StrEnum):
    PERSISTENT_PROJECT = "persistent_project"
    EPHEMERAL_TASK = "ephemeral_task"
    ISOLATED_RUN = "isolated_run"
    READ_ONLY_SOURCE = "read_only_source"
    CLONED = "cloned"
    REMOTE = "remote"


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class WorkspaceAccessMode(StrEnum):
    READ_WRITE = "read_write"
    READ_ONLY = "read_only"


class WorkspaceRetention(StrEnum):
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"
    UNTIL = "until"


class WorkspaceSourceKind(StrEnum):
    EMPTY = "empty"
    FILES = "files"
    SNAPSHOT = "snapshot"
    ARTIFACT = "artifact"
    REPOSITORY = "repository"
    TEMPLATE = "template"


class WorkspaceChangeKind(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


class MaterializationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def utc_now() -> datetime:
    return datetime.now(UTC)


def validate_relative_path(value: str) -> str:
    """Validate a portable workspace-relative POSIX path without normalizing escapes."""

    if not value or value != value.strip():
        raise ValueError("workspace relative path must not be blank or padded")
    if "\\" in value:
        raise ValueError("workspace relative paths must use '/' separators")
    if value.startswith("/"):
        raise ValueError("workspace path must be relative")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("workspace path contains an unsafe segment")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("workspace path escapes its root")
    if path.parts and path.parts[0].endswith(":"):
        raise ValueError("workspace path must not contain a drive prefix")
    return value


def validate_sha256(value: str) -> str:
    if len(value) != 64:
        raise ValueError("sha256 must contain 64 hexadecimal characters")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("sha256 must be hexadecimal") from exc
    return value.lower()


@dataclass(frozen=True, slots=True)
class WorkspaceSourceRef:
    kind: WorkspaceSourceKind
    ref: str
    revision: str | None = None
    checksum: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise ValueError("workspace source ref must not be blank")
        if self.revision is not None and not self.revision.strip():
            raise ValueError("workspace source revision must not be blank")
        if self.checksum is not None:
            object.__setattr__(self, "checksum", validate_sha256(self.checksum))


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    relative_path: str
    file_id: str
    sha256: str

    def __post_init__(self) -> None:
        validate_relative_path(self.relative_path)
        validate_id(self.file_id, "file")
        object.__setattr__(self, "sha256", validate_sha256(self.sha256))


@dataclass(frozen=True, slots=True)
class Workspace:
    project_id: str
    owner_ref: OwnerRef
    workspace_type: WorkspaceType
    access_mode: WorkspaceAccessMode = WorkspaceAccessMode.READ_WRITE
    id: str = field(default_factory=lambda: new_id("workspace"))
    status: WorkspaceStatus = WorkspaceStatus.ACTIVE
    revision: int = 0
    source_refs: tuple[WorkspaceSourceRef, ...] = ()
    base_snapshot_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_used_at: datetime = field(default_factory=utc_now)
    retention: WorkspaceRetention = WorkspaceRetention.PERSISTENT
    expires_at: datetime | None = None
    policy_labels: tuple[str, ...] = ()
    active_task_ids: tuple[str, ...] = ()
    active_run_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "workspace")
        validate_id(self.project_id, "project")
        if self.base_snapshot_id is not None:
            validate_id(self.base_snapshot_id, "workspace_snapshot")
        if self.revision < 0:
            raise ValueError("workspace revision must be non-negative")
        if self.workspace_type is WorkspaceType.READ_ONLY_SOURCE:
            if self.access_mode is not WorkspaceAccessMode.READ_ONLY:
                raise ValueError("read-only source workspaces must use read-only access")
        if self.retention is WorkspaceRetention.UNTIL and self.expires_at is None:
            raise ValueError("retention=until requires expires_at")
        for label in self.policy_labels:
            if not label.strip():
                raise ValueError("workspace policy labels must not be blank")
        for task_id in self.active_task_ids:
            validate_id(task_id, "task")
        for run_id in self.active_run_ids:
            validate_id(run_id, "run")


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    workspace_id: str
    revision: int
    files: tuple[WorkspaceFile, ...]
    content_checksum: str
    id: str = field(default_factory=lambda: new_id("workspace_snapshot"))
    source_refs: tuple[WorkspaceSourceRef, ...] = ()
    parent_snapshot_id: str | None = None
    source_revision: str | None = None
    artifact_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.id, "workspace_snapshot")
        validate_id(self.workspace_id, "workspace")
        if self.revision < 0:
            raise ValueError("workspace snapshot revision must be non-negative")
        object.__setattr__(self, "content_checksum", validate_sha256(self.content_checksum))
        if self.parent_snapshot_id is not None:
            validate_id(self.parent_snapshot_id, "workspace_snapshot")
        if self.source_revision is not None and not self.source_revision.strip():
            raise ValueError("source_revision must not be blank")
        paths = [entry.relative_path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("workspace snapshot paths must be unique")
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")


@dataclass(frozen=True, slots=True)
class WorkspaceMaterialization:
    workspace_id: str
    snapshot_id: str
    base_revision: int
    access_mode: WorkspaceAccessMode
    id: str = field(default_factory=lambda: new_id("materialization"))
    execution_workspace: str = ""
    task_id: str | None = None
    run_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.id, "materialization")
        validate_id(self.workspace_id, "workspace")
        validate_id(self.snapshot_id, "workspace_snapshot")
        if self.base_revision < 0:
            raise ValueError("materialization base revision must be non-negative")
        token = self.execution_workspace or self.id
        if not token.strip() or token in {".", ".."} or "/" in token or "\\" in token:
            raise ValueError("execution workspace must be an opaque local token, not a path")
        object.__setattr__(self, "execution_workspace", token)
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.run_id is not None:
            validate_id(self.run_id, "run")


@dataclass(frozen=True, slots=True)
class WorkspaceChange:
    relative_path: str
    kind: WorkspaceChangeKind
    file_id: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        validate_relative_path(self.relative_path)
        if self.kind is WorkspaceChangeKind.DELETED:
            if self.file_id is not None or self.sha256 is not None:
                raise ValueError("deleted workspace changes must not carry file content")
            return
        if self.file_id is None or self.sha256 is None:
            raise ValueError("created/modified workspace changes require canonical file evidence")
        validate_id(self.file_id, "file")
        object.__setattr__(self, "sha256", validate_sha256(self.sha256))


@dataclass(frozen=True, slots=True)
class WorkspaceChangeSet:
    workspace_id: str
    snapshot_id: str
    materialization_id: str
    base_revision: int
    changes: tuple[WorkspaceChange, ...]

    def __post_init__(self) -> None:
        validate_id(self.workspace_id, "workspace")
        validate_id(self.snapshot_id, "workspace_snapshot")
        validate_id(self.materialization_id, "materialization")
        if self.base_revision < 0:
            raise ValueError("change-set base revision must be non-negative")
        paths = [change.relative_path for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("workspace change-set paths must be unique")


@dataclass(frozen=True, slots=True)
class RemoteMaterializationRequest:
    workspace_id: str
    snapshot_id: str
    expected_checksum: str
    access_mode: WorkspaceAccessMode
    cache_key: str

    def __post_init__(self) -> None:
        validate_id(self.workspace_id, "workspace")
        validate_id(self.snapshot_id, "workspace_snapshot")
        object.__setattr__(self, "expected_checksum", validate_sha256(self.expected_checksum))
        if not self.cache_key.strip():
            raise ValueError("remote materialization cache key must not be blank")


@dataclass(frozen=True, slots=True)
class CleanupReport:
    removed_materialization_ids: tuple[str, ...] = ()
    missing_materialization_ids: tuple[str, ...] = ()
    failed_materialization_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for materialization_id in (
            *self.removed_materialization_ids,
            *self.missing_materialization_ids,
            *self.failed_materialization_ids,
        ):
            validate_id(materialization_id, "materialization")
