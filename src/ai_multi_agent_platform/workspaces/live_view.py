"""Read-only live Workspace materialization views for derived consumers.

The canonical Workspace/Snapshot lifecycle remains owned by :mod:`workspaces`.  These value
objects expose bounded content evidence without leaking provider-private filesystem paths and
without persisting dirty files as new canonical File records.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from ai_multi_agent_platform.domain import validate_id

from .models import WorkspaceFile, validate_relative_path, validate_sha256


@dataclass(frozen=True, slots=True)
class WorkspaceLiveFile:
    """One immutable file observation from a live materialization."""

    relative_path: str
    data: bytes
    sha256: str

    def __post_init__(self) -> None:
        validate_relative_path(self.relative_path)
        object.__setattr__(self, "sha256", validate_sha256(self.sha256))
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise ValueError("workspace live file checksum does not match data")


@dataclass(frozen=True, slots=True)
class WorkspaceLiveSnapshot:
    """Exact bounded read of one active Run materialization.

    ``base_snapshot_checksum`` is the canonical #37 snapshot-manifest checksum (which also
    commits to File identities). ``base_content_checksum`` and ``content_checksum`` are
    content-only digests over path + file SHA-256 so dirty state can be compared without
    creating canonical File records for uncommitted bytes.
    """

    workspace_id: str
    workspace_snapshot_id: str
    materialization_id: str
    run_id: str
    base_revision: int
    base_snapshot_checksum: str
    base_content_checksum: str
    content_checksum: str
    dirty: bool
    files: tuple[WorkspaceLiveFile, ...]

    def __post_init__(self) -> None:
        validate_id(self.workspace_id, "workspace")
        validate_id(self.workspace_snapshot_id, "workspace_snapshot")
        validate_id(self.materialization_id, "materialization")
        validate_id(self.run_id, "run")
        if self.base_revision < 0:
            raise ValueError("workspace live snapshot base_revision must be non-negative")
        object.__setattr__(
            self,
            "base_snapshot_checksum",
            validate_sha256(self.base_snapshot_checksum),
        )
        object.__setattr__(
            self,
            "base_content_checksum",
            validate_sha256(self.base_content_checksum),
        )
        object.__setattr__(self, "content_checksum", validate_sha256(self.content_checksum))
        paths = tuple(file.relative_path for file in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("workspace live snapshot contains duplicate file paths")
        expected = workspace_content_checksum(self.files)
        if expected != self.content_checksum:
            raise ValueError("workspace live snapshot content checksum mismatch")
        if self.dirty != (self.content_checksum != self.base_content_checksum):
            raise ValueError("workspace live snapshot dirty flag disagrees with content checksum")


def workspace_content_checksum(
    files: Iterable[WorkspaceLiveFile | WorkspaceFile],
) -> str:
    """Return a deterministic content-only digest over relative paths and file digests."""

    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda item: item.relative_path):
        digest.update(entry.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


__all__ = [
    "WorkspaceLiveFile",
    "WorkspaceLiveSnapshot",
    "workspace_content_checksum",
]
