from __future__ import annotations

import json
from pathlib import Path


EVIDENCE = Path(__file__).parent / "evidence" / "issue_862" / "storage_backends.json"
ALLOWED_CLASSIFICATIONS = {"supported_optional", "experimental_only", "reject/defer"}
EXPECTED_BACKENDS = {"rustfs", "garage", "seaweedfs"}


def _manifest() -> dict[str, object]:
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


def test_storage_evidence_preserves_platform_authority() -> None:
    manifest = _manifest()
    invariants = manifest["platform_invariants"]

    assert invariants["metadata_authority"] == "platform_metadata_db"
    assert invariants["canonical_checksum"] == "sha256"
    assert invariants["etag_is_canonical_checksum"] is False
    assert invariants["local_filesystem_remains_valid"] is True
    assert invariants["object_store_is_mandatory"] is False


def test_storage_evidence_records_exact_reviewed_revisions() -> None:
    manifest = _manifest()
    backends = manifest["backends"]

    assert set(backends) == EXPECTED_BACKENDS
    for backend in backends.values():
        assert backend["reviewed_ref"]
        assert len(backend["reviewed_commit"]) == 40
        assert backend["license"]
        assert backend["source_url"].startswith("https://")
        assert backend["proposed_classification"] in ALLOWED_CLASSIFICATIONS


def test_s3_surface_is_minimal_and_does_not_assume_etag_checksum_semantics() -> None:
    manifest = _manifest()
    surface = manifest["required_s3_surface"]

    assert set(surface["required"]) == {"PutObject", "GetObject", "HeadObject", "DeleteObject"}
    assert "ListObjectsV2" in surface["required_for_repair_and_orphan_detection"]
    assert "ETag as SHA-256" in surface["explicitly_not_assumed"]


def test_final_classification_is_gated_on_runtime_evidence_and_setup_wizard() -> None:
    manifest = _manifest()
    gate = manifest["gate"]

    assert gate["final_classification_requires_runtime_evidence"] is True
    assert gate["setup_wizard_recommendation_requires_issue"] == 799
    assert gate["issue_799_currently_blocks_final_wizard_integration"] is True
    assert all(
        backend["classification_is_final"] is False
        for backend in manifest["backends"].values()
    )
