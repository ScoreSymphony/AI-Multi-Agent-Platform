"""Deterministic application release manifest generation."""

from __future__ import annotations

import hashlib
import json

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import ApplicationRelease

MANIFEST_SCHEMA_VERSION = 1


def release_manifest(release: ApplicationRelease) -> dict[str, JsonValue]:
    artifacts: list[JsonValue] = []
    for artifact in sorted(release.artifacts, key=lambda item: (item.target_id, item.filename)):
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


def canonical_manifest_bytes(release: ApplicationRelease) -> bytes:
    return json.dumps(
        release_manifest(release),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_sha256(release: ApplicationRelease) -> str:
    return hashlib.sha256(canonical_manifest_bytes(release)).hexdigest()
