"""Provider-neutral repository identities, revisions, status and provenance models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.connectors import Connection, ExternalResourceReference
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces.models import validate_relative_path

from .capabilities import RepositoryCapability as RepositoryCapability
from .capabilities import RepositoryOperation as RepositoryOperation


def utc_now() -> datetime:
    return datetime.now(UTC)


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


def _nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def validate_git_revision(value: str) -> str:
    """Validate an immutable Git object identifier without assuming SHA-1 forever."""

    revision = value.strip().lower()
    if len(revision) not in {40, 64}:
        raise ValueError("resolved Git revision must contain 40 or 64 hexadecimal characters")
    try:
        int(revision, 16)
    except ValueError as exc:
        raise ValueError("resolved Git revision must be hexadecimal") from exc
    return revision


class RepositoryVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    INTERNAL = "internal"
    LOCAL = "local"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RepositoryConnection:
    """Repository-specific view over a canonical Connector Connection.

    Credential values never cross this boundary: only the connection-owned SecretReferences do.
    """

    connection: Connection
    provider_id: str
    local: bool = False
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonblank(self.provider_id, "repository provider_id")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def id(self) -> str:
        return self.connection.id

    @property
    def secret_references(self) -> tuple[SecretReference, ...]:
        return self.connection.secret_references


@dataclass(frozen=True, slots=True)
class RepositoryReference:
    """Canonical wrapper for an externally/local-provider owned repository object."""

    external_resource: ExternalResourceReference
    default_branch: str | None = None
    target_revision: str | None = None
    resolved_revision: str | None = None
    visibility: RepositoryVisibility = RepositoryVisibility.UNKNOWN
    capabilities: tuple[RepositoryCapability, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.external_resource.resource_type != "repository":
            raise ValueError("repository reference must wrap resource_type='repository'")
        for name in ("default_branch", "target_revision"):
            value = getattr(self, name)
            if value is not None:
                _nonblank(value, name)
        if self.resolved_revision is not None:
            object.__setattr__(
                self,
                "resolved_revision",
                validate_git_revision(self.resolved_revision),
            )
        operations = [capability.operation for capability in self.capabilities]
        if len(operations) != len(set(operations)):
            raise ValueError("repository capabilities must not contain duplicate operations")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def id(self) -> str:
        return self.external_resource.id

    @property
    def connection_id(self) -> str:
        return self.external_resource.connection_id

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "connection_id": self.connection_id,
            "external_resource": self.external_resource.to_dict(),
            "default_branch": self.default_branch,
            "target_revision": self.target_revision,
            "resolved_revision": self.resolved_revision,
            "visibility": self.visibility.value,
            "capabilities": [
                {
                    "operation": capability.operation.value,
                    "side_effects": capability.side_effects.value,
                    "requires_credentials": capability.requires_credentials,
                    "supported": capability.supported,
                }
                for capability in self.capabilities
            ],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class RepositoryRevision:
    repository_id: str
    requested_ref: str
    commit_sha: str

    def __post_init__(self) -> None:
        validate_id(self.repository_id, "external_resource")
        _nonblank(self.requested_ref, "requested_ref")
        object.__setattr__(self, "commit_sha", validate_git_revision(self.commit_sha))


@dataclass(frozen=True, slots=True)
class RepositoryStatus:
    repository_id: str
    head_revision: str | None
    branch: str | None
    staged_paths: tuple[str, ...] = ()
    modified_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.repository_id, "external_resource")
        if self.head_revision is not None:
            object.__setattr__(self, "head_revision", validate_git_revision(self.head_revision))
        if self.branch is not None:
            _nonblank(self.branch, "branch")
        for path in (
            *self.staged_paths,
            *self.modified_paths,
            *self.deleted_paths,
            *self.untracked_paths,
        ):
            validate_relative_path(path)

    @property
    def clean(self) -> bool:
        return not (
            self.staged_paths or self.modified_paths or self.deleted_paths or self.untracked_paths
        )


@dataclass(frozen=True, slots=True)
class RepositoryDiff:
    repository_id: str
    base_revision: str | None
    patch: str
    changed_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.repository_id, "external_resource")
        if self.base_revision is not None:
            object.__setattr__(self, "base_revision", validate_git_revision(self.base_revision))
        for path in self.changed_paths:
            validate_relative_path(path)


@dataclass(frozen=True, slots=True)
class RepositoryCommit:
    repository_id: str
    revision: str
    message: str
    parent_revisions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.repository_id, "external_resource")
        object.__setattr__(self, "revision", validate_git_revision(self.revision))
        _nonblank(self.message, "commit message")
        object.__setattr__(
            self,
            "parent_revisions",
            tuple(validate_git_revision(value) for value in self.parent_revisions),
        )


@dataclass(frozen=True, slots=True)
class RepositoryCommitInfo:
    """Provider-neutral read model for an existing commit in repository history."""

    repository_id: str
    revision: str
    message: str
    parent_revisions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.repository_id, "external_resource")
        object.__setattr__(self, "revision", validate_git_revision(self.revision))
        object.__setattr__(
            self,
            "parent_revisions",
            tuple(validate_git_revision(value) for value in self.parent_revisions),
        )


@dataclass(frozen=True, slots=True)
class RepositoryTreeEntry:
    relative_path: str
    data: bytes

    def __post_init__(self) -> None:
        validate_relative_path(self.relative_path)


@dataclass(frozen=True, slots=True)
class RepositoryTree:
    repository_id: str
    requested_ref: str
    resolved_revision: str
    entries: tuple[RepositoryTreeEntry, ...]

    def __post_init__(self) -> None:
        validate_id(self.repository_id, "external_resource")
        _nonblank(self.requested_ref, "requested_ref")
        object.__setattr__(
            self,
            "resolved_revision",
            validate_git_revision(self.resolved_revision),
        )
        paths = [entry.relative_path for entry in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("repository tree paths must be unique")


@dataclass(frozen=True, slots=True)
class RepositoryRunProvenance:
    run_id: str
    repository_id: str
    input_revision: str
    actor_ref: str
    agent_id: str | None = None
    branch_ref: str | None = None
    output_revision: str | None = None
    task_id: str | None = None
    diff_artifact_ids: tuple[str, ...] = ()
    provider_resource_ids: tuple[str, ...] = ()
    recorded_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.run_id, "run")
        validate_id(self.repository_id, "external_resource")
        object.__setattr__(self, "input_revision", validate_git_revision(self.input_revision))
        _nonblank(self.actor_ref, "actor_ref")
        if self.agent_id is not None:
            validate_id(self.agent_id, "agent")
        if self.branch_ref is not None:
            _nonblank(self.branch_ref, "branch_ref")
        if self.output_revision is not None:
            object.__setattr__(self, "output_revision", validate_git_revision(self.output_revision))
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        for artifact_id in self.diff_artifact_ids:
            validate_id(artifact_id, "artifact")
        for resource_id in self.provider_resource_ids:
            validate_id(resource_id, "external_resource")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("repository provenance timestamp must be timezone-aware")
