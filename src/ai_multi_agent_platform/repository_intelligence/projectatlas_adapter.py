"""Prepared ProjectAtlas source-adapter contracts behind explicit containment gates.

Nothing in this module grants ProjectAtlas repository/Workspace lifecycle ownership. Production
composition must resolve an already-authorized source binding from #82/#37/#15, execute the pinned
runtime through a deployment-owned process boundary, and normalize only version-pinned provider
output. Until those prerequisites are supplied, source capabilities remain unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, ToolInvocation
from ai_multi_agent_platform.repositories.models import validate_git_revision

from .models import RepositoryIntelligenceFreshness
from .projectatlas import (
    PROJECTATLAS_ARCHIVE_SHA256,
    PROJECTATLAS_RUNTIME_VERSION,
)


@dataclass(frozen=True, slots=True)
class ProjectAtlasContainmentEvidence:
    """Deployment/evaluation evidence required before untrusted source processing is enabled."""

    runtime_version: str
    artifact_sha256: str
    source_read_only: bool
    provider_state_outside_source: bool
    secrets_stripped: bool
    network_egress_denied: bool
    no_new_privileges: bool
    process_boundary: str
    platform: str
    evidence_ref: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.runtime_version, "runtime_version"),
            (self.artifact_sha256, "artifact_sha256"),
            (self.process_boundary, "process_boundary"),
            (self.platform, "platform"),
            (self.evidence_ref, "evidence_ref"),
        ):
            if not value.strip():
                raise ValueError(f"ProjectAtlas containment {name} must not be blank")

    @property
    def source_operations_ready(self) -> bool:
        return (
            self.runtime_version == PROJECTATLAS_RUNTIME_VERSION
            and self.artifact_sha256 == PROJECTATLAS_ARCHIVE_SHA256
            and self.source_read_only
            and self.provider_state_outside_source
            and self.secrets_stripped
            and self.network_egress_denied
            and self.no_new_privileges
        )

    def require_source_operations_ready(self) -> None:
        if self.source_operations_ready:
            return
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "ProjectAtlas source operations require complete verified containment evidence",
            details={
                "runtime_version_match": self.runtime_version == PROJECTATLAS_RUNTIME_VERSION,
                "artifact_checksum_match": self.artifact_sha256 == PROJECTATLAS_ARCHIVE_SHA256,
                "source_read_only": self.source_read_only,
                "provider_state_outside_source": self.provider_state_outside_source,
                "secrets_stripped": self.secrets_stripped,
                "network_egress_denied": self.network_egress_denied,
                "no_new_privileges": self.no_new_privileges,
                "process_boundary": self.process_boundary,
                "platform": self.platform,
                "evidence_ref": self.evidence_ref,
            },
        )


@dataclass(frozen=True, slots=True)
class ProjectAtlasSourceBinding:
    """Exact authorized source/state binding supplied by canonical platform composition."""

    repository_id: str
    requested_revision: str
    resolved_revision: str
    source_root: Path
    database_path: Path
    freshness: RepositoryIntelligenceFreshness
    source_content_checksum: str | None = None
    workspace_id: str | None = None
    workspace_snapshot_id: str | None = None
    materialization_id: str | None = None
    dirty: bool = False

    def __post_init__(self) -> None:
        if not self.repository_id.strip():
            raise ValueError("ProjectAtlas source binding repository_id must not be blank")
        if not self.requested_revision.strip():
            raise ValueError("ProjectAtlas source binding requested_revision must not be blank")
        object.__setattr__(self, "resolved_revision", validate_git_revision(self.resolved_revision))
        if not self.source_root.is_absolute() or not self.database_path.is_absolute():
            raise ValueError("ProjectAtlas source/database bindings must use absolute paths")
        if self.database_path == self.source_root or self.source_root in self.database_path.parents:
            raise ValueError("ProjectAtlas provider database must remain outside canonical source")
        for value, name in (
            (self.source_content_checksum, "source_content_checksum"),
            (self.workspace_id, "workspace_id"),
            (self.workspace_snapshot_id, "workspace_snapshot_id"),
            (self.materialization_id, "materialization_id"),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"ProjectAtlas source binding {name} must not be blank")
        if self.dirty and self.freshness is not RepositoryIntelligenceFreshness.LIVE_WORKSPACE:
            raise ValueError("dirty ProjectAtlas binding requires live_workspace freshness")
        if self.materialization_id is not None and self.workspace_id is None:
            raise ValueError("ProjectAtlas materialization binding requires workspace_id")

    def provenance_details(self) -> dict[str, JsonValue]:
        details: dict[str, JsonValue] = {
            "repository_id": self.repository_id,
            "requested_revision": self.requested_revision,
            "resolved_revision": self.resolved_revision,
            "freshness": self.freshness.value,
            "dirty": self.dirty,
            "source_content_checksum": self.source_content_checksum,
        }
        if self.workspace_id is not None:
            details["workspace"] = {
                "workspace_id": self.workspace_id,
                "workspace_snapshot_id": self.workspace_snapshot_id,
                "materialization_id": self.materialization_id,
                "source_content_checksum": self.source_content_checksum,
                "dirty": self.dirty,
            }
        return details


class ProjectAtlasSourceBindingResolver(Protocol):
    """Resolve #82/#37/#15-authorized source without giving the plugin lifecycle authority."""

    async def resolve(self, invocation: ToolInvocation) -> ProjectAtlasSourceBinding: ...


@dataclass(frozen=True, slots=True)
class ProjectAtlasCommandRequest:
    """One allow-listed provider command after source and containment have been resolved."""

    action: str
    arguments: tuple[str, ...]
    source: ProjectAtlasSourceBinding
    timeout_seconds: float
    max_stdout_bytes: int

    def __post_init__(self) -> None:
        if self.action not in {"scan", "search", "slice", "health-check", "settings"}:
            raise ValueError("ProjectAtlas command action is not allow-listed")
        if self.timeout_seconds <= 0:
            raise ValueError("ProjectAtlas command timeout must be positive")
        if self.max_stdout_bytes < 1:
            raise ValueError("ProjectAtlas command output bound must be positive")


@dataclass(frozen=True, slots=True)
class ProjectAtlasCommandResult:
    payload: JsonValue
    elapsed_ms: float
    stdout_bytes: int
    provider_state_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.elapsed_ms < 0 or self.stdout_bytes < 0:
            raise ValueError("ProjectAtlas command measurements must be non-negative")
        if self.provider_state_bytes is not None and self.provider_state_bytes < 0:
            raise ValueError("ProjectAtlas state bytes must be non-negative")


class ProjectAtlasCommandRunner(Protocol):
    """Deployment-owned process boundary; implementations must enforce containment evidence."""

    async def run(
        self,
        request: ProjectAtlasCommandRequest,
        containment: ProjectAtlasContainmentEvidence,
    ) -> ProjectAtlasCommandResult: ...


class ProjectAtlasV045Normalizer(Protocol):
    """Strict version-pinned output mapping into canonical #502 schemas.

    This remains a protocol until golden v0.4.5 command payloads are captured in the aggregate
    integration campaign. Guessing fields from marketing/docs would make the adapter unsound.
    """

    def normalize_search(
        self,
        payload: JsonValue,
        *,
        source: ProjectAtlasSourceBinding,
    ) -> dict[str, JsonValue]: ...

    def normalize_slice(
        self,
        payload: JsonValue,
        *,
        source: ProjectAtlasSourceBinding,
    ) -> dict[str, JsonValue]: ...

    def normalize_map(
        self,
        payload: JsonValue,
        *,
        source: ProjectAtlasSourceBinding,
    ) -> dict[str, JsonValue]: ...


__all__ = [
    "ProjectAtlasCommandRequest",
    "ProjectAtlasCommandResult",
    "ProjectAtlasCommandRunner",
    "ProjectAtlasContainmentEvidence",
    "ProjectAtlasSourceBinding",
    "ProjectAtlasSourceBindingResolver",
    "ProjectAtlasV045Normalizer",
]
