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
    GateStatus,
    ReleaseGate,
    ReleaseKind,
    ReleaseManifest,
    UpstreamProvenance,
)

_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


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
    upstreams = tuple(_decode_upstream(item) for item in _object_list(value, "upstreams"))
    compatibility = tuple(
        _decode_compatibility(item) for item in _object_list(value, "compatibility")
    )
    gates = tuple(_decode_gate(item) for item in _object_list(value, "gates"))
    return ReleaseManifest(
        schema_version=_string(value, "schema_version"),
        release_version=_string(value, "release_version"),
        release_kind=ReleaseKind(_string(value, "release_kind")),
        source_commit=_string(value, "source_commit"),
        created_at=_timestamp(value, "created_at"),
        release_notes_ref=_string(value, "release_notes_ref"),
        versions=version_snapshot_from_dict(cast(Mapping[object, object], versions)),
        upstreams=upstreams,
        compatibility=compatibility,
        gates=gates,
        sbom_ref=_string(value, "sbom_ref"),
        provenance_ref=_string(value, "provenance_ref"),
        artifact_hashes=_string_map(value, "artifact_hashes"),
    )


def _decode_upstream(value: Mapping[str, object]) -> UpstreamProvenance:
    hashes_raw = _mapping(value, "artifact_hashes")
    hashes: dict[str, str] = {}
    for key, item in hashes_raw.items():
        if not isinstance(key, str) or not isinstance(item, str) or not item:
            raise ReleaseManifestError("artifact_hashes must map non-empty strings to strings")
        hashes[key] = item
    return UpstreamProvenance(
        component=_string(value, "component"),
        source_url=_string(value, "source_url"),
        revision=_string(value, "revision"),
        revision_kind=_string(value, "revision_kind"),
        license=_string(value, "license"),
        modified=_bool(value, "modified"),
        patches=tuple(_string_list(value, "patches")),
        build_status=_string(value, "build_status"),
        test_status=_string(value, "test_status"),
        artifact_hashes=hashes,
        sbom_ref=_optional_string(value, "sbom_ref"),
        provenance_ref=_optional_string(value, "provenance_ref"),
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
    return ReleaseGate(
        name=_string(value, "name"),
        status=GateStatus(_string(value, "status")),
        evidence=_string(value, "evidence"),
        required=_bool(value, "required"),
    )


def _string_map(value: Mapping[str, object], name: str) -> dict[str, str]:
    raw = _mapping(value, name)
    result: dict[str, str] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise ReleaseManifestError(f"{name} must map non-empty strings to non-empty strings")
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


def _optional_string(value: Mapping[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ReleaseManifestError(f"{name} must be null or a non-empty string")
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
