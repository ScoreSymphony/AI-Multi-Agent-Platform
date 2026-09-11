"""Deterministic application release manifest generation and validation."""

from __future__ import annotations

import hashlib
import json

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import ApplicationRelease

MANIFEST_SCHEMA_VERSION = 1

APPLICATION_RELEASE_MANIFEST_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "release_id",
        "application",
        "display_name",
        "version",
        "channel",
        "visibility",
        "project_id",
        "workspace_id",
        "workspace_snapshot_id",
        "workspace_content_checksum",
        "source_revision",
        "build_specification",
        "targets",
        "artifacts",
        "gates",
        "release_notes",
        "release_url",
        "latest_url",
    ],
    "properties": {
        "schema_version": {"const": MANIFEST_SCHEMA_VERSION},
        "release_id": {"type": "string", "minLength": 1},
        "application": {"type": "string", "minLength": 1},
        "display_name": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "channel": {"type": "string", "minLength": 1},
        "visibility": {"type": "string", "minLength": 1},
        "project_id": {"type": "string", "minLength": 1},
        "workspace_id": {"type": "string", "minLength": 1},
        "workspace_snapshot_id": {"type": "string", "minLength": 1},
        "workspace_content_checksum": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "source_revision": {"type": "string", "minLength": 1},
        "build_specification": {
            "type": "object",
            "additionalProperties": False,
            "required": ["spec_id", "revision"],
            "properties": {
                "spec_id": {"type": "string", "minLength": 1},
                "revision": {"type": "integer", "minimum": 1},
            },
        },
        "targets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "target",
                    "os",
                    "architecture",
                    "package_type",
                    "status",
                    "task_id",
                    "run_id",
                    "failure_reason",
                ],
                "properties": {
                    "target": {"type": "string", "minLength": 1},
                    "os": {"type": "string", "minLength": 1},
                    "architecture": {"type": "string", "minLength": 1},
                    "package_type": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "minLength": 1},
                    "task_id": {"type": ["string", "null"]},
                    "run_id": {"type": ["string", "null"]},
                    "failure_reason": {"type": ["string", "null"]},
                },
            },
        },
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "artifact_id",
                    "file_id",
                    "target",
                    "filename",
                    "package_type",
                    "media_type",
                    "sha256",
                    "build_task_id",
                    "build_run_id",
                    "evidence_refs",
                    "download_url",
                ],
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1},
                    "file_id": {"type": "string", "minLength": 1},
                    "target": {"type": "string", "minLength": 1},
                    "filename": {"type": "string", "minLength": 1},
                    "package_type": {"type": "string", "minLength": 1},
                    "media_type": {"type": "string", "minLength": 1},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "build_task_id": {"type": "string", "minLength": 1},
                    "build_run_id": {"type": "string", "minLength": 1},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "download_url": {"type": ["string", "null"]},
                },
            },
        },
        "gates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "status", "evidence_refs", "details"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "minLength": 1},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "details": {"type": "object"},
                },
            },
        },
        "release_notes": {"type": ["string", "null"]},
        "release_url": {"type": ["string", "null"]},
        "latest_url": {"type": ["string", "null"]},
    },
}

_MANIFEST_VALIDATOR = Draft202012Validator(APPLICATION_RELEASE_MANIFEST_SCHEMA)


def release_manifest(
    release: ApplicationRelease,
    *,
    exclude_gate_names: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    excluded = frozenset(exclude_gate_names)
    artifacts: list[JsonValue] = []
    for artifact in sorted(
        release.artifacts,
        key=lambda item: (item.target_id, item.filename),
    ):
        artifacts.append(
            {
                "artifact_id": artifact.artifact_id,
                "file_id": artifact.file_id,
                "target": artifact.target_id,
                "filename": artifact.filename,
                "package_type": artifact.package_type.value,
                "media_type": artifact.media_type,
                "sha256": artifact.sha256,
                "build_task_id": artifact.build_task_id,
                "build_run_id": artifact.build_run_id,
                "evidence_refs": list(artifact.evidence_refs),
                "download_url": artifact.download_url,
            }
        )
    gates: list[JsonValue] = [
        {
            "name": gate.name,
            "status": gate.status.value,
            "evidence_refs": list(gate.evidence_refs),
            "details": dict(gate.details),
        }
        for gate in sorted(release.gates, key=lambda item: item.name)
        if gate.name not in excluded
    ]
    targets: list[JsonValue] = [
        {
            "target": state.target.target_id,
            "os": state.target.os_name,
            "architecture": state.target.architecture,
            "package_type": state.target.package_type.value,
            "status": state.status.value,
            "task_id": state.task_id,
            "run_id": state.run_id,
            "failure_reason": state.failure_reason,
        }
        for state in sorted(release.targets, key=lambda item: item.target.target_id)
    ]
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release_id": release.release_id,
        "application": release.application_id,
        "display_name": release.display_name,
        "version": release.version,
        "channel": release.channel.value,
        "visibility": release.visibility.value,
        "project_id": release.project_id,
        "workspace_id": release.workspace_id,
        "workspace_snapshot_id": release.workspace_snapshot_id,
        "workspace_content_checksum": release.workspace_content_checksum,
        "source_revision": release.source_revision,
        "build_specification": {
            "spec_id": release.build_specification.spec_id,
            "revision": release.build_specification.revision,
        },
        "targets": targets,
        "artifacts": artifacts,
        "gates": gates,
        "release_notes": release.release_notes,
        "release_url": release.release_url,
        "latest_url": release.latest_url,
    }


def canonical_manifest_bytes(
    release: ApplicationRelease,
    *,
    exclude_gate_names: tuple[str, ...] = (),
) -> bytes:
    return json.dumps(
        release_manifest(release, exclude_gate_names=exclude_gate_names),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_sha256(
    release: ApplicationRelease,
    *,
    exclude_gate_names: tuple[str, ...] = (),
) -> str:
    return hashlib.sha256(
        canonical_manifest_bytes(release, exclude_gate_names=exclude_gate_names)
    ).hexdigest()


def manifest_validation_errors(
    release: ApplicationRelease,
    *,
    exclude_gate_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    manifest = release_manifest(release, exclude_gate_names=exclude_gate_names)
    errors = sorted(
        _MANIFEST_VALIDATOR.iter_errors(manifest),
        key=lambda item: tuple(str(value) for value in item.absolute_path),
    )
    return tuple(error.message for error in errors)
