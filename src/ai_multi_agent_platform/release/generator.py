"""Deterministic release-manifest generation for issue #42."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote

from ai_multi_agent_platform.upgrade.versioning import current_release_versions

from .codec import ReleaseManifestError, release_manifest_from_dict
from .discovery import CompatibilityInventory, load_compatibility_inventory
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
from .service import evaluate_release

RELEASE_GENERATION_INPUT_SCHEMA_VERSION = "1"
_SOURCE_COMMIT_TOKEN = "${SOURCE_COMMIT}"
_GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST = re.compile(r"^(?:sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})$")
_TAG_COMMIT = re.compile(
    r"^(?P<tag>[^/\s][^/]*)\s*/\s*(?P<commit>(?:[0-9a-f]{40}|[0-9a-f]{64}))$",
    re.IGNORECASE,
)
_TAG = re.compile(r"^v\d+(?:\.\d+)+(?:[-+][0-9a-z.-]+)?$", re.IGNORECASE)
_VERSION = re.compile(r"^\d+(?:\.\d+)+(?:[-+][0-9a-z.-]+)?$", re.IGNORECASE)


class ReleaseGenerationError(ValueError):
    """Raised when deterministic release-manifest generation cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class DependencySetSource:
    name: str
    ecosystem: str
    kind: DependencySetKind
    path: Path
    source_ref: str


@dataclass(frozen=True, slots=True)
class ArtifactSource:
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class ReleaseGenerationInputs:
    release_kind: ReleaseKind
    created_at: str
    release_notes_ref: str
    sbom_ref: str
    provenance_ref: str
    dependency_sets: tuple[DependencySetSource, ...]
    artifacts: tuple[ArtifactSource, ...]
    gates: tuple[ReleaseGate, ...]


def load_release_generation_inputs(
    path: str | Path,
    *,
    source_commit: str,
) -> ReleaseGenerationInputs:
    source = Path(path)
    try:
        raw: object = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGenerationError(f"cannot read release generation input: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReleaseGenerationError("release generation input must be a JSON object")
    document = cast(dict[str, object], raw)
    if _string(document, "schema_version") != RELEASE_GENERATION_INPUT_SCHEMA_VERSION:
        raise ReleaseGenerationError("unsupported release generation input schema_version")
    base_dir = source.parent
    try:
        release_kind = ReleaseKind(_string(document, "release_kind"))
    except ValueError as exc:
        raise ReleaseGenerationError("release_kind is invalid") from exc

    dependency_sets = tuple(
        _decode_dependency_source(item, base_dir=base_dir, source_commit=source_commit)
        for item in _object_list(document, "dependency_sets")
    )
    artifacts = tuple(
        _decode_artifact_source(item, base_dir=base_dir)
        for item in _object_list(document, "artifacts")
    )
    gates = tuple(
        _decode_gate(item, source_commit=source_commit) for item in _object_list(document, "gates")
    )
    return ReleaseGenerationInputs(
        release_kind=release_kind,
        created_at=_expand(_string(document, "created_at"), source_commit),
        release_notes_ref=_expand(_string(document, "release_notes_ref"), source_commit),
        sbom_ref=_expand(_string(document, "sbom_ref"), source_commit),
        provenance_ref=_expand(_string(document, "provenance_ref"), source_commit),
        dependency_sets=dependency_sets,
        artifacts=artifacts,
        gates=gates,
    )


def generate_release_manifest(
    *,
    source_commit: str,
    inputs: ReleaseGenerationInputs,
    inventory: CompatibilityInventory,
) -> ReleaseManifest:
    if _GIT_COMMIT.fullmatch(source_commit) is None:
        raise ReleaseGenerationError("source_commit must be a full 40- or 64-character Git SHA")

    versions = current_release_versions()
    if inventory.versions.to_dict() != versions.to_dict():
        raise ReleaseGenerationError(
            "compatibility inventory VersionSnapshot does not match the canonical release vector"
        )

    dependency_sets = tuple(
        DependencySetProvenance(
            name=item.name,
            ecosystem=item.ecosystem,
            kind=item.kind,
            source_ref=item.source_ref,
            digest=_sha256_file(item.path),
        )
        for item in inputs.dependency_sets
    )
    artifact_hashes = {item.name: _sha256_file(item.path) for item in inputs.artifacts}
    if len(artifact_hashes) != len(inputs.artifacts):
        raise ReleaseGenerationError("artifact names must be unique")

    upstreams: list[UpstreamProvenance] = []
    compatibility: list[CompatibilityRecord] = []
    for entry in inventory.entries:
        try:
            status = CompatibilityStatus(entry.compatibility_status)
        except ValueError as exc:
            raise ReleaseGenerationError(
                f"upstream {entry.component!r} has unsupported compatibility status "
                f"{entry.compatibility_status!r}"
            ) from exc
        upstreams.append(
            UpstreamProvenance(
                component=entry.component,
                source_url=entry.source_url,
                revision=entry.revision,
                revision_kind=_revision_kind(entry.revision),
                license=entry.license,
                modified=entry.local_modifications,
                patches=entry.patches,
                build_status="not_recorded_by_generator",
                test_status=f"compatibility:{entry.compatibility_status}",
                last_verified_at=entry.last_checked_at,
                provenance_ref=(
                    f"git:{source_commit}:release/compatibility.json"
                    f"#component={quote(entry.component, safe='')}"
                ),
            )
        )
        compatibility.append(
            CompatibilityRecord(
                component=entry.component,
                upstream_revision=entry.revision,
                status=status,
                tested_at=entry.last_checked_at,
                platform_constraint=f"=={versions.platform_release}",
                notes=entry.notes,
            )
        )

    manifest = ReleaseManifest(
        release_version=versions.platform_release,
        release_kind=inputs.release_kind,
        source_commit=source_commit,
        created_at=inputs.created_at,
        release_notes_ref=inputs.release_notes_ref,
        versions=versions,
        dependency_sets=dependency_sets,
        upstreams=tuple(upstreams),
        compatibility=tuple(compatibility),
        gates=inputs.gates,
        sbom_ref=inputs.sbom_ref,
        provenance_ref=inputs.provenance_ref,
        artifact_hashes=artifact_hashes,
    )
    try:
        manifest = release_manifest_from_dict(manifest.to_dict())
    except ReleaseManifestError as exc:
        raise ReleaseGenerationError(f"generated release manifest is invalid: {exc}") from exc
    report = evaluate_release(manifest)
    if not report.ready:
        raise ReleaseGenerationError(
            "generated release manifest is blocked: " + "; ".join(report.blockers)
        )
    return manifest


def generate_release_manifest_from_file(
    *,
    source_commit: str,
    input_path: str | Path,
    inventory_path: str | Path | None = None,
) -> ReleaseManifest:
    inputs = load_release_generation_inputs(input_path, source_commit=source_commit)
    inventory = load_compatibility_inventory(inventory_path)
    return generate_release_manifest(
        source_commit=source_commit,
        inputs=inputs,
        inventory=inventory,
    )


def write_release_manifest(manifest: ReleaseManifest, path: str | Path) -> None:
    destination = Path(path)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError as exc:
        raise ReleaseGenerationError(f"cannot write release manifest: {exc}") from exc


def _decode_dependency_source(
    value: dict[str, object],
    *,
    base_dir: Path,
    source_commit: str,
) -> DependencySetSource:
    try:
        kind = DependencySetKind(_string(value, "kind"))
    except ValueError as exc:
        raise ReleaseGenerationError("dependency set kind is invalid") from exc
    return DependencySetSource(
        name=_string(value, "name"),
        ecosystem=_string(value, "ecosystem"),
        kind=kind,
        path=_resolve_path(base_dir, _string(value, "path")),
        source_ref=_expand(_string(value, "source_ref"), source_commit),
    )


def _decode_artifact_source(value: dict[str, object], *, base_dir: Path) -> ArtifactSource:
    return ArtifactSource(
        name=_string(value, "name"),
        path=_resolve_path(base_dir, _string(value, "path")),
    )


def _decode_gate(value: dict[str, object], *, source_commit: str) -> ReleaseGate:
    evidence = _mapping(value, "evidence")
    try:
        status = GateStatus(_string(value, "status"))
        kind = ReleaseEvidenceKind(_string(evidence, "kind"))
    except ValueError as exc:
        raise ReleaseGenerationError("release gate status/evidence kind is invalid") from exc
    source_commit_value = _optional_string(evidence, "source_commit")
    digest = _optional_string(evidence, "digest")
    required = value.get("required")
    if not isinstance(required, bool):
        raise ReleaseGenerationError("release gate required must be a boolean")
    return ReleaseGate(
        name=_string(value, "name"),
        status=status,
        evidence=ReleaseEvidence(
            kind=kind,
            ref=_expand(_string(evidence, "ref"), source_commit),
            source_commit=None
            if source_commit_value is None
            else _expand(source_commit_value, source_commit),
            digest=digest,
        ),
        required=required,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseGenerationError(f"cannot hash release input {path}: {exc}") from exc
    return f"sha256:{digest.hexdigest()}"


def _revision_kind(revision: str) -> str:
    normalized = revision.strip()
    lowered = normalized.lower()
    if _GIT_COMMIT.fullmatch(lowered) is not None:
        return "commit"
    if _DIGEST.fullmatch(lowered) is not None:
        return "digest"
    if _TAG_COMMIT.fullmatch(normalized) is not None:
        return "tag"
    if _TAG.fullmatch(normalized) is not None:
        return "tag"
    if _VERSION.fullmatch(normalized) is not None:
        return "version"
    raise ReleaseGenerationError(f"unsupported immutable upstream revision format: {revision!r}")


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def _expand(value: str, source_commit: str) -> str:
    return value.replace(_SOURCE_COMMIT_TOKEN, source_commit)


def _mapping(value: dict[str, object], name: str) -> dict[str, object]:
    raw = value.get(name)
    if not isinstance(raw, dict):
        raise ReleaseGenerationError(f"{name} must be an object")
    return cast(dict[str, object], raw)


def _object_list(value: dict[str, object], name: str) -> list[dict[str, object]]:
    raw = value.get(name)
    if not isinstance(raw, list):
        raise ReleaseGenerationError(f"{name} must be an array")
    result: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ReleaseGenerationError(f"{name} entries must be objects")
        result.append(cast(dict[str, object], item))
    return result


def _string(value: dict[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise ReleaseGenerationError(f"{name} must be a non-empty string")
    return raw


def _optional_string(value: dict[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ReleaseGenerationError(f"{name} must be null or a non-empty string")
    return raw
