"""Canonical provider-neutral application build and release value types."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id
from ai_multi_agent_platform.security import SecretReference, redact_sensitive, redact_text
from ai_multi_agent_platform.workspaces import validate_relative_path, validate_sha256

APPLICATION_RELEASE_SCHEMA_VERSION = "1.0"
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _nonblank_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain blank values")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(values)


def _command_tokens(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(not value.strip() for value in values):
        raise ValueError("command must not contain blank values")
    if any(redact_text(value) != value for value in values):
        raise ValueError(
            "command must not embed sensitive environment assignments; use secret_environment"
        )
    return tuple(values)


def _safe_mapping(
    values: Mapping[str, JsonValue],
    field_name: str,
) -> MappingProxyType[str, JsonValue]:
    copied = dict(values)
    if redact_sensitive(copied) != copied:
        raise ValueError(f"sensitive-looking {field_name} entries are not allowed")
    return MappingProxyType(copied)


def _environment(values: Mapping[str, str], field_name: str) -> MappingProxyType[str, str]:
    copied: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(name, str) or _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise ValueError(f"{field_name} contains an invalid environment variable name")
        if not isinstance(value, str):
            raise ValueError(f"{field_name} values must be strings")
        copied[name] = value
    return MappingProxyType(dict(sorted(copied.items())))


def _secret_environment(
    values: Mapping[str, SecretReference],
) -> MappingProxyType[str, SecretReference]:
    copied: dict[str, SecretReference] = {}
    for name, reference in values.items():
        if not isinstance(name, str) or _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise ValueError("secret_environment contains an invalid environment variable name")
        if not isinstance(reference, SecretReference):
            raise ValueError("secret_environment values must be SecretReference objects")
        copied[name] = reference
    return MappingProxyType(dict(sorted(copied.items())))


class ReleaseChannel(StrEnum):
    STABLE = "stable"
    BETA = "beta"
    NIGHTLY = "nightly"


class ReleaseVisibility(StrEnum):
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    PRIVATE = "private"


class ReleaseStatus(StrEnum):
    DRAFT = "draft"
    BUILDING = "building"
    PARTIAL = "partial"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"


class BuildTargetStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class GateStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class PackageType(StrEnum):
    EXECUTABLE = "executable"
    ARCHIVE = "archive"
    INSTALLER = "installer"
    LINUX_PACKAGE = "linux_package"
    MACOS_BUNDLE = "macos_bundle"
    OCI_IMAGE = "oci_image"
    SOURCE_ARCHIVE = "source_archive"
    STATIC_WEB = "static_web"


@dataclass(frozen=True, slots=True)
class BuildTarget:
    target_id: str
    os_name: str
    architecture: str
    package_type: PackageType
    output_path: str
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonblank(self.target_id, "target_id")
        _nonblank(self.os_name, "os_name")
        _nonblank(self.architecture, "architecture")
        validate_relative_path(self.output_path)
        object.__setattr__(
            self,
            "required_capabilities",
            _nonblank_tuple(self.required_capabilities, "required_capabilities"),
        )


@dataclass(frozen=True, slots=True)
class BuildSpecification:
    command: tuple[str, ...]
    targets: tuple[BuildTarget, ...]
    spec_id: str = field(default_factory=lambda: new_id("build_spec"))
    revision: int = 1
    source_path: str | None = None
    workflow_ref: str | None = None
    pre_build_checks: tuple[str, ...] = ()
    test_gates: tuple[str, ...] = ()
    post_build_checks: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    resource_hints: Mapping[str, JsonValue] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    secret_environment: Mapping[str, SecretReference] = field(default_factory=dict)
    secret_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.spec_id, "build_spec")
        if self.revision < 1:
            raise ValueError("build specification revision must be at least 1")
        if not self.command and self.workflow_ref is None:
            raise ValueError("build specification requires command or workflow_ref")
        object.__setattr__(self, "command", _command_tokens(self.command))
        if not self.targets:
            raise ValueError("build specification requires at least one target")
        target_ids = [target.target_id for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("build target ids must be unique")
        if self.source_path is not None:
            validate_relative_path(self.source_path)
        if self.workflow_ref is not None:
            _nonblank(self.workflow_ref, "workflow_ref")
        for field_name in (
            "pre_build_checks",
            "test_gates",
            "post_build_checks",
            "required_capabilities",
            "secret_references",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonblank_tuple(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "resource_hints",
            _safe_mapping(self.resource_hints, "resource_hints"),
        )
        environment = _environment(self.environment, "environment")
        if redact_sensitive(dict(environment)) != dict(environment):
            raise ValueError("sensitive-looking environment variables must use secret_environment")
        secret_environment = _secret_environment(self.secret_environment)
        overlap = set(environment) & set(secret_environment)
        if overlap:
            raise ValueError("environment and secret_environment must not define the same variable")
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "secret_environment", secret_environment)


@dataclass(frozen=True, slots=True)
class BuildTargetState:
    target: BuildTarget
    status: BuildTargetStatus = BuildTargetStatus.PENDING
    task_id: str | None = None
    run_id: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.run_id is not None:
            validate_id(self.run_id, "run")
        if self.failure_reason is not None:
            _nonblank(self.failure_reason, "failure_reason")


@dataclass(frozen=True, slots=True)
class GateEvidence:
    name: str
    status: GateStatus
    evidence_refs: tuple[str, ...] = ()
    details: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _nonblank(self.name, "gate name")
        object.__setattr__(
            self,
            "evidence_refs",
            _nonblank_tuple(self.evidence_refs, "evidence_refs"),
        )
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True, slots=True)
class ApplicationArtifact:
    artifact_id: str
    file_id: str
    target_id: str
    filename: str
    package_type: PackageType
    media_type: str
    sha256: str
    build_task_id: str
    build_run_id: str
    evidence_refs: tuple[str, ...] = ()
    download_url: str | None = None
    external_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.artifact_id, "artifact")
        validate_id(self.file_id, "file")
        validate_id(self.build_task_id, "task")
        validate_id(self.build_run_id, "run")
        _nonblank(self.target_id, "target_id")
        validate_relative_path(self.filename)
        _nonblank(self.media_type, "media_type")
        object.__setattr__(self, "sha256", validate_sha256(self.sha256))
        object.__setattr__(
            self,
            "evidence_refs",
            _nonblank_tuple(self.evidence_refs, "evidence_refs"),
        )
        if self.download_url is not None:
            _nonblank(self.download_url, "download_url")
        object.__setattr__(
            self,
            "external_metadata",
            MappingProxyType(dict(self.external_metadata)),
        )


@dataclass(frozen=True, slots=True)
class ApplicationRelease:
    application_id: str
    display_name: str
    version: str
    channel: ReleaseChannel
    visibility: ReleaseVisibility
    project_id: str
    workspace_id: str
    workspace_snapshot_id: str
    workspace_content_checksum: str
    source_revision: str
    build_specification: BuildSpecification
    creator_ref: str
    release_id: str = field(default_factory=lambda: new_id("application_release"))
    status: ReleaseStatus = ReleaseStatus.DRAFT
    targets: tuple[BuildTargetState, ...] = ()
    artifacts: tuple[ApplicationArtifact, ...] = ()
    gates: tuple[GateEvidence, ...] = ()
    release_notes: str | None = None
    publisher_id: str | None = None
    release_url: str | None = None
    latest_url: str | None = None
    external_metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    previous_release_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    published_at: datetime | None = None
    revision: int = 1
    schema_version: str = APPLICATION_RELEASE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.release_id, "application_release")
        validate_id(self.project_id, "project")
        validate_id(self.workspace_id, "workspace")
        validate_id(self.workspace_snapshot_id, "workspace_snapshot")
        object.__setattr__(
            self,
            "workspace_content_checksum",
            validate_sha256(self.workspace_content_checksum),
        )
        _nonblank(self.application_id, "application_id")
        _nonblank(self.display_name, "display_name")
        _nonblank(self.version, "version")
        _nonblank(self.source_revision, "source_revision")
        _nonblank(self.creator_ref, "creator_ref")
        if self.previous_release_id is not None:
            validate_id(self.previous_release_id, "application_release")
        if self.revision < 1:
            raise ValueError("release revision must be at least 1")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.published_at is not None:
            if self.published_at.tzinfo is None or self.published_at.utcoffset() is None:
                raise ValueError("published_at must be timezone-aware")
            if self.published_at < self.created_at:
                raise ValueError("published_at must not precede created_at")
        if self.release_url is not None:
            _nonblank(self.release_url, "release_url")
        if self.latest_url is not None:
            _nonblank(self.latest_url, "latest_url")
        if self.publisher_id is not None:
            _nonblank(self.publisher_id, "publisher_id")
        if self.release_notes is not None and not self.release_notes.strip():
            raise ValueError("release_notes must not be blank when provided")
        if self.schema_version != APPLICATION_RELEASE_SCHEMA_VERSION:
            raise ValueError("unsupported application release schema version")
        target_ids = {state.target.target_id for state in self.targets}
        if self.targets and target_ids != {
            target.target_id for target in self.build_specification.targets
        }:
            raise ValueError(
                "release target state must cover the build specification targets exactly"
            )
        if any(artifact.target_id not in target_ids for artifact in self.artifacts):
            raise ValueError("release artifact references an unknown target")
        gate_names = [gate.name for gate in self.gates]
        if len(gate_names) != len(set(gate_names)):
            raise ValueError("release gates must be unique by name")
        object.__setattr__(
            self,
            "external_metadata",
            MappingProxyType(dict(self.external_metadata)),
        )
