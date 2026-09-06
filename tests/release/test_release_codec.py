import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.release import ReleaseManifestError, load_release_manifest
from ai_multi_agent_platform.release.service import REQUIRED_RELEASE_GATES


def _document() -> dict[str, object]:
    return {
        "schema_version": "1",
        "release_version": "1.2.3",
        "release_kind": "patch",
        "source_commit": "deadbeef",
        "created_at": "2026-09-06T08:00:00Z",
        "release_notes_ref": "notes:1.2.3",
        "versions": {
            "platform_release": "1.2.3",
            "domain_schema": "1.0",
            "api": "v1",
            "migration_revision": "r3",
            "plugin_manifest": "1",
            "portable_format": "1",
            "template_schema": "1",
            "backup_format": "1",
            "worker_protocol": "1",
            "message_protocol": "1",
            "adapter_versions": {},
            "plugin_interface_versions": {},
        },
        "upstreams": [
            {
                "component": "runtime",
                "source_url": "https://example.invalid/runtime",
                "revision": "abc123",
                "revision_kind": "commit",
                "license": "MIT",
                "modified": False,
                "patches": [],
                "build_status": "passed",
                "test_status": "passed",
                "artifact_hashes": {"runtime": "sha256:123"},
                "sbom_ref": None,
                "provenance_ref": "attestation:runtime",
                "last_verified_at": "2026-09-06T08:00:00Z",
            }
        ],
        "compatibility": [
            {
                "component": "runtime",
                "upstream_revision": "abc123",
                "status": "tested",
                "tested_at": "2026-09-06T08:00:00Z",
                "platform_constraint": "1.2.3",
                "notes": [],
            }
        ],
        "gates": [
            {"name": name, "status": "passed", "evidence": f"ci:{name}", "required": True}
            for name in sorted(REQUIRED_RELEASE_GATES)
        ],
        "artifact_hashes": {"ai_multi_agent_platform-1.2.3.whl": "sha256:platform"},
        "sbom_ref": "sbom:1.2.3",
        "provenance_ref": "attestation:1.2.3",
    }


def test_load_release_manifest_validates_schema(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    manifest = load_release_manifest(path)
    assert manifest.release_version == "1.2.3"
    assert manifest.upstreams[0].revision == "abc123"


def test_load_release_manifest_rejects_floating_missing_revision(tmp_path: Path) -> None:
    document = _document()
    upstreams = document["upstreams"]
    assert isinstance(upstreams, list)
    upstream = upstreams[0]
    assert isinstance(upstream, dict)
    upstream["revision"] = ""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(path)


def test_load_release_manifest_rejects_invalid_timestamp(tmp_path: Path) -> None:
    document = _document()
    document["created_at"] = "not-a-timestamp"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReleaseManifestError):
        load_release_manifest(path)
