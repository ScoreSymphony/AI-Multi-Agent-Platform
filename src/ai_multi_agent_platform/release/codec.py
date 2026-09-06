"""Strict JSON codec for release manifests."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from ai_multi_agent_platform.upgrade.versioning import version_snapshot_from_dict

from .models import (
    CompatibilityRecord,
    CompatibilityStatus,
    DependencySetKind,
    DependencySetProvenance,
    GateStatus,
    ReleaseEvidence,
    ReleaseEvidenceKind,
    ReleaseGate,
    ReleaseKind,
    ReleaseManifest,
    UpstreamProvenance,
)

_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST = re.compile(r"^(?:sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})$")
_REFERENCE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:.+$")
_FLOATING_REVISION = re.compile(r"(^|[:/@])latest$", re.IGNORECASE)


class ReleaseManifestError(ValueError):
    """Raised when a release manifest is malformed."""


def _schema() -> Mapping[str, object]:
    raw = json.loads(
        files("ai_multi_agent_platform.release")
        .joinpath("release-manifest.schema.json")
        .read_text(encoding="utf-8")
    )
    if not isinstance(raw, dict):
        raise ReleaseManifestError("release manifest schema must be a JSON object")
    return cast(dict[str, object], raw)


def load_release_manifest(path: str | Path) -> ReleaseManifest:
    try:
        raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"cannot read release manifest: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReleaseManifestError("release manifest must be a JSON object")
    document = cast(dict[str, object], raw)
    errors = sorted(
        Draft202012Validator(_schema(), format_checker=FormatChecker()).iter_errors(document),
        key=lambda item: list(item.path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "<root>"
        raise ReleaseManifestError(f"release manifest schema error at {location}: {first.message}")
    return release_manifest_from_dict(document)


def release_manifest_from_dict(value: Mapping[str, object]) -> ReleaseManifest:
    versions = _mapping(value, "versions")
    dependency_sets = tuple(
        _decode_dependency_set(item) for item in _object_list(value, "dependency_sets")
    )
    upstreams = tuple(_decode_upstream(item) for item in _object_list(value, "upstreams"))
    compatibility = tuple(
        _decode_compatibility(item) for item in _object_list(value, "compatibility")
    )
    gates = tuple(_decode_gate(item) for item in _object_list(value, "gates"))
    return ReleaseManifest(
        schema_version=_string(value, "schema_version"),
        release_version=_string(value, "release_version"),
        release_kind=ReleaseKind(_string(value, "release_kind")),
        source_commit=_commit(value, "source_commit"),
        created_at=_timestamp(value, "created_at"),
        release_notes_ref=_reference(value, "release_notes_ref"),
        versions=version_snapshot_from_dict(cast(Mapping[object, object], versions)),
        dependency_sets=dependency_sets,
        upstreams=upstreams,
        compatibility=compatibility,
        gates=gates,
        sbom_ref=_reference(value, "sbom_ref"),
        provenance_ref=_reference(value, "provenance_ref"),
        artifact_hashes=_digest_map(value, "artifact_hashes"),
    )


def _decode_dependency_set(value: Mapping[str, object]) -> DependencySetProvenance:
    return DependencySetProvenance(
        name=_string(value, "name"),
        ecosystem=_string(value, "ecosystem"),
        kind=DependencySetKind(_string(value, "kind")),
        source_ref=_reference(value, "source_ref"),
        digest=_digest(value, "digest"),
    )


def _decode_upstream(value: Mapping[str, object]) -> UpstreamProvenance:
    revision_kind = _string(value, "revision_kind")
    revision = _immutable_revision(value, "revision", revision_kind=revision_kind)
    return UpstreamProvenance(
        component=_string(value, "component"),
        source_url=_string(value, "source_url"),
        revision=revision,
        revision_kind=revision_kind,
        license=_string(value, "license"),
        modified=_bool(value, "modified"),
        patches=tuple(_string_list(value, "patches")),
        build_status=_string(value, "build_status"),
        test_status=_string(value, "test_status"),
        artifact_hashes=_digest_map(value, "artifact_hashes"),
        sbom_ref=_optional_reference(value, "sbom_ref"),
        provenance_ref=_optional_reference(value, "provenance_ref"),
        last_verified_at=_timestamp(value, "last_verified_at"),
    )


def _decode_compatibility(value: Mapping[str, object]) -> CompatibilityRecord:
    return CompatibilityRecord(
        component=_string(value, "component"),
        upstream_revision=_string(value, "upstream_revision"),
        status=CompatibilityStatus(_string(value, "status")),
        tested_at=_timestamp(value, "tested_at"),
        platform_constraint=_string(value, "platform_constraint"),
        notes=tuple(_string_list(value, "notes")),
    )


def _decode_gate(value: Mapping[str, object]) -> ReleaseGate:
    evidence_raw = _mapping(value, "evidence")
    return ReleaseGate(
        name=_string(value, "name"),
        status=GateStatus(_string(value, "status")),
        evidence=_decode_evidence(evidence_raw),
        required=_bool(value, "required"),
    )


def _decode_evidence(value: Mapping[str, object]) -> ReleaseEvidence:
    return ReleaseEvidence(
        kind=ReleaseEvidenceKind(_string(value, "kind")),
        ref=_reference(value, "ref"),
        source_commit=_optional_commit(value, "source_commit"),
        digest=_optional_digest(value, "digest"),
    )


def _digest_map(value: Mapping[str, object], name: str) -> dict[str, str]:
    raw = _mapping(value, name)
    result: dict[str, str] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not key or not isinstance(item, str):
            raise ReleaseManifestError(f"{name} must map non-empty strings to digests")
        if _DIGEST.fullmatch(item) is None:
            raise ReleaseManifestError(f"{name}.{key} must be a sha256 or sha512 digest")
        result[key] = item
    return result


def _mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    raw = value.get(name)
    if not isinstance(raw, dict):
        raise ReleaseManifestError(f"{name} must be an object")
    return cast(dict[str, object], raw)


def _object_list(value: Mapping[str, object], name: str) -> list[Mapping[str, object]]:
    raw = value.get(name)
    if not isinstance(raw, list):
        raise ReleaseManifestError(f"{name} must be an array")
    result: list[Mapping[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ReleaseManifestError(f"{name} entries must be objects")
        result.append(cast(dict[str, object], item))
    return result


def _string(value: Mapping[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ReleaseManifestError(f"{name} must be a non-empty string")
    return raw


def _commit(value: Mapping[str, object], name: str) -> str:
    raw = _string(value, name)
    if _GIT_COMMIT.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be a full 40- or 64-character Git commit SHA")
    return raw


def _optional_commit(value: Mapping[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or _GIT_COMMIT.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be null or a full Git commit SHA")
    return raw


def _digest(value: Mapping[str, object], name: str) -> str:
    raw = _string(value, name)
    if _DIGEST.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be a sha256 or sha512 digest")
    return raw


def _optional_digest(value: Mapping[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or _DIGEST.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be null or a sha256/sha512 digest")
    return raw


def _reference(value: Mapping[str, object], name: str) -> str:
    raw = _string(value, name)
    if _REFERENCE.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be a scheme-qualified immutable reference")
    return raw


def _optional_reference(value: Mapping[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or _REFERENCE.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be null or a scheme-qualified reference")
    return raw


def _immutable_revision(
    value: Mapping[str, object],
    name: str,
    *,
    revision_kind: str,
) -> str:
    raw = _string(value, name)
    if _FLOATING_REVISION.search(raw) is not None or "*" in raw:
        raise ReleaseManifestError(f"{name} must not use a floating revision")
    if revision_kind == "commit" and _GIT_COMMIT.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be a full Git commit SHA for commit revisions")
    if revision_kind == "digest" and _DIGEST.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be a digest for digest revisions")
    return raw


def _timestamp(value: Mapping[str, object], name: str) -> str:
    raw = _string(value, name)
    if _RFC3339_TIMESTAMP.fullmatch(raw) is None:
        raise ReleaseManifestError(f"{name} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseManifestError(f"{name} must be an RFC3339 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ReleaseManifestError(f"{name} must include a timezone offset")
    return raw


def _bool(value: Mapping[str, object], name: str) -> bool:
    raw = value.get(name)
    if not isinstance(raw, bool):
        raise ReleaseManifestError(f"{name} must be a boolean")
    return raw


def _string_list(value: Mapping[str, object], name: str) -> list[str]:
    raw = value.get(name)
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise ReleaseManifestError(f"{name} must be an array of non-empty strings")
    return cast(list[str], raw)
