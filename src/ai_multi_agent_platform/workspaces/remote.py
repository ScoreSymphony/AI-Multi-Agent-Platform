"""Provider-neutral remote workspace materialization hooks for later worker transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_multi_agent_platform.domain import validate_id

from .models import (
    MaterializationOutcome,
    RemoteMaterializationRequest,
    WorkspaceAccessMode,
    WorkspaceChange,
    validate_sha256,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _opaque_ref(value: str, field_name: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must not be blank or padded")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(f"{field_name} must be an opaque reference, not a filesystem path")
    return value


@dataclass(frozen=True, slots=True)
class RemoteMaterializationReceipt:
    """Worker acknowledgement that one exact canonical snapshot was materialized."""

    workspace_id: str
    snapshot_id: str
    expected_checksum: str
    observed_checksum: str
    access_mode: WorkspaceAccessMode
    worker_ref: str
    materialization_ref: str
    cache_hit: bool = False
    acknowledged_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.workspace_id, "workspace")
        validate_id(self.snapshot_id, "workspace_snapshot")
        expected = validate_sha256(self.expected_checksum)
        observed = validate_sha256(self.observed_checksum)
        object.__setattr__(self, "expected_checksum", expected)
        object.__setattr__(self, "observed_checksum", observed)
        if expected != observed:
            raise ValueError("remote materialization checksum does not match canonical snapshot")
        _opaque_ref(self.worker_ref, "worker_ref")
        _opaque_ref(self.materialization_ref, "materialization_ref")
        if self.acknowledged_at.tzinfo is None or self.acknowledged_at.utcoffset() is None:
            raise ValueError("remote materialization acknowledgement must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RemoteMaterializationResult:
    """Canonical result evidence returned from a remote materialization."""

    workspace_id: str
    snapshot_id: str
    materialization_ref: str
    content_checksum: str
    changes: tuple[WorkspaceChange, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    completed_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.workspace_id, "workspace")
        validate_id(self.snapshot_id, "workspace_snapshot")
        _opaque_ref(self.materialization_ref, "materialization_ref")
        object.__setattr__(self, "content_checksum", validate_sha256(self.content_checksum))
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")
        paths = [change.relative_path for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("remote materialization result changes must have unique paths")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("remote materialization result timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RemoteCleanupAcknowledgement:
    """Observable worker acknowledgement for cleanup of disposable local state."""

    workspace_id: str
    snapshot_id: str
    materialization_ref: str
    outcome: MaterializationOutcome
    succeeded: bool
    error_code: str | None = None
    acknowledged_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.workspace_id, "workspace")
        validate_id(self.snapshot_id, "workspace_snapshot")
        _opaque_ref(self.materialization_ref, "materialization_ref")
        if self.succeeded and self.error_code is not None:
            raise ValueError("successful remote cleanup must not carry an error_code")
        if not self.succeeded and (self.error_code is None or not self.error_code.strip()):
            raise ValueError("failed remote cleanup requires an error_code")
        if self.acknowledged_at.tzinfo is None or self.acknowledged_at.utcoffset() is None:
            raise ValueError("remote cleanup acknowledgement must be timezone-aware")


class RemoteWorkspaceMaterializer(ABC):
    """Transport-independent seam implemented later by #14 worker communication."""

    @abstractmethod
    async def materialize(
        self,
        request: RemoteMaterializationRequest,
    ) -> RemoteMaterializationReceipt: ...

    @abstractmethod
    async def collect_result(
        self,
        receipt: RemoteMaterializationReceipt,
    ) -> RemoteMaterializationResult: ...

    @abstractmethod
    async def cleanup(
        self,
        receipt: RemoteMaterializationReceipt,
        outcome: MaterializationOutcome,
    ) -> RemoteCleanupAcknowledgement: ...
