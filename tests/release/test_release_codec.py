import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.release import ReleaseManifestError, load_release_manifest
from ai_multi_agent_platform.release.service import REQUIRED_RELEASE_GATES

SOURCE_COMMIT = "d" * 40
UPSTREAM_COMMIT = "a" * 40
PLATFORM_DIGEST = "sha256:" + "1" * 64
UPSTREAM_DIGEST = "sha256:" + "2" * 64
DEPENDENCY_DIGEST = "sha256:" + "3" * 64


def _document() -> dict[str, object]:
    return {
        "schema_version": "2",
        "release_version": "1.2.3",
        "release_kind": "patch",
        "source_commit": SOURCE_COMMIT,
        "created_at": "2026-09-06T08:00:00Z",
        "release_notes_ref": "release-notes:1.2.3",
        "versions": {
            "platform_release": "1.2.3",
            "domain_schema": "1.0",
            "api": "v1",
            "migration_revision": "r3",
            "plugin_manifest": "1",
            "portable_format": "1.0",
            "template_schema": "1",
            "backup_format": "1",
            "worker_protocol": "1.0",
            "message_protocol": "1.0",
            "adapter_versions": {"example-adapter": "1"},
            "plugin_interface_versions": {"tool": "1"},
        },
        "dependency_sets": [
            {
                "name": "python-runtime",
                "ecosystem": "python",
                "kind": "resolved_set",
                "source_ref": "artifact:dependencies/python-runtime.json",
                "digest": DEPENDENCY_DIGEST,
            }
        ],
        "upstreams": [
            {
                "component": "runtime",
                "source_url": "https://example.invalid/runtime",
                "revision": UPSTREAM_COMMIT,
                "revision_kind": "commit",
                "license": "MIT",
                "modified": False,
                "patches": [],
                "build_status": "passed",
                "test_status": "passed",
                "artifact_hashes": {"runtime": UPSTREAM_DIGEST},
                "sbom_ref": None,
                "provenance_ref": "attestation:runtime",
                "last_verified_at": "2026-09-06T08:00:00Z",
            }
        ],
        "compatibility": [
            {
                "component": "runtime",
                "upstream_revision": UPSTREAM_COMMIT,
                "status": "tested",
                "tested_at": "2026-09-06T08:00:00Z",
                "platform_constraint": "1.2.3",
                "notes": [],
            }
        ],
        "gates": [
            {
                "name": name,
                "status": "passed",
                "evidence": {
                    "kind": "workflow_run",
                    "ref": f"github-run:{name}:123",
                    "source_commit": SOURCE_COMMIT,
                    "digest": None,
                },
                "required": True,
            }
            for name in sorted(REQUIRED_RELEASE_GATES)
        ],
        "artifact_hashes": {"ai_multi_agent_platform-1.2.3.whl": PLATFORM_DIGEST},
        "sbom_ref": "sbom:1.2.3",
        "provenance_ref": "attestation:1.2.3",
    }


def _write(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_load_release_manifest_validates_schema(tmp_path: Path) -> None:
    manifest = load_release_manifest(_write(tmp_path, _document()))
    assert manifest.schema_version == "2"
    assert manifest.release_version == "1.2.3"
    assert manifest.source_commit == SOURCE_COMMIT
    assert manifest.dependency_sets[0].digest == DEPENDENCY_DIGEST
    assert manifest.upstreams[0].revision == UPSTREAM_COMMIT


def test_load_release_manifest_rejects_missing_dependency_set(tmp_path: Path) -> None:
    document = _document()
    document["dependency_sets"] = []
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(_write(tmp_path, document))


def test_load_release_manifest_rejects_short_source_commit(tmp_path: Path) -> None:
    document = _document()
    document["source_commit"] = "deadbeef"
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(_write(tmp_path, document))


def test_load_release_manifest_rejects_floating_upstream_revision(tmp_path: Path) -> None:
    document = _document()
    upstreams = document["upstreams"]
    assert isinstance(upstreams, list)
    upstream = upstreams[0]
    assert isinstance(upstream, dict)
    upstream["revision_kind"] = "tag"
    upstream["revision"] = "latest"
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(_write(tmp_path, document))


def test_load_release_manifest_rejects_invalid_artifact_digest(tmp_path: Path) -> None:
    document = _document()
    document["artifact_hashes"] = {"package.whl": "sha256:123"}
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(_write(tmp_path, document))


def test_load_release_manifest_rejects_untyped_gate_evidence(tmp_path: Path) -> None:
    document = _document()
    gates = document["gates"]
    assert isinstance(gates, list)
    gate = gates[0]
    assert isinstance(gate, dict)
    gate["evidence"] = "trust me"
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(_write(tmp_path, document))


def test_load_release_manifest_rejects_non_reference_evidence_ref(tmp_path: Path) -> None:
    document = _document()
    gates = document["gates"]
    assert isinstance(gates, list)
    gate = gates[0]
    assert isinstance(gate, dict)
    evidence = gate["evidence"]
    assert isinstance(evidence, dict)
    evidence["ref"] = "trust me"
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(_write(tmp_path, document))


def test_load_release_manifest_rejects_invalid_timestamp(tmp_path: Path) -> None:
    document = _document()
    document["created_at"] = "not-a-timestamp"
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(_write(tmp_path, document))
