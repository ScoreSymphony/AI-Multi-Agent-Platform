"""Release, provenance and compatibility models for issue #42."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.upgrade.models import VersionSnapshot

RELEASE_MANIFEST_SCHEMA_VERSION = "1"


class ReleaseKind(StrEnum):
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    SECURITY_HOTFIX = "security_hotfix"
    PRERELEASE = "prerelease"


class CompatibilityStatus(StrEnum):
    SUPPORTED = "supported"
    TESTED = "tested"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"
    BLOCKED = "blocked"


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class UpstreamProvenance:
    component: str
    source_url: str
    revision: str
    revision_kind: str
    license: str
    modified: bool
    build_status: str
    test_status: str
    last_verified_at: str
    patches: tuple[str, ...] = ()
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    sbom_ref: str | None = None
    provenance_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_hashes", MappingProxyType(dict(self.artifact_hashes)))

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "source_url": self.source_url,
            "revision": self.revision,
            "revision_kind": self.revision_kind,
            "license": self.license,
            "modified": self.modified,
            "patches": list(self.patches),
            "build_status": self.build_status,
            "test_status": self.test_status,
            "artifact_hashes": dict(self.artifact_hashes),
            "sbom_ref": self.sbom_ref,
            "provenance_ref": self.provenance_ref,
            "last_verified_at": self.last_verified_at,
        }


@dataclass(frozen=True, slots=True)
class CompatibilityRecord:
    component: str
    upstream_revision: str
    status: CompatibilityStatus
    tested_at: str
    platform_constraint: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "upstream_revision": self.upstream_revision,
            "status": self.status.value,
            "tested_at": self.tested_at,
            "platform_constraint": self.platform_constraint,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ReleaseGate:
    name: str
    status: GateStatus
    evidence: str
    required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "evidence": self.evidence,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    release_version: str
    release_kind: ReleaseKind
    source_commit: str
    created_at: str
    release_notes_ref: str
    versions: VersionSnapshot
    upstreams: tuple[UpstreamProvenance, ...]
    compatibility: tuple[CompatibilityRecord, ...]
    gates: tuple[ReleaseGate, ...]
    sbom_ref: str
    provenance_ref: str
    schema_version: str = RELEASE_MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "release_version": self.release_version,
            "release_kind": self.release_kind.value,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "release_notes_ref": self.release_notes_ref,
            "versions": self.versions.to_dict(),
            "upstreams": [upstream.to_dict() for upstream in self.upstreams],
            "compatibility": [record.to_dict() for record in self.compatibility],
            "gates": [gate.to_dict() for gate in self.gates],
            "sbom_ref": self.sbom_ref,
            "provenance_ref": self.provenance_ref,
        }


@dataclass(frozen=True, slots=True)
class ReleaseReadinessReport:
    release_version: str
    ready: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "release_version": self.release_version,
            "ready": self.ready,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }
