from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_PATH = REPO_ROOT / "tests" / "evidence" / "issue_862" / "runtime_image_digests.json"
VERIFIER_PATH = REPO_ROOT / "scripts" / "benchmarks" / "verify_issue862_storage_vps_capture.py"
CAPTURE_PATH = REPO_ROOT / "scripts" / "benchmarks" / "run_issue862_storage_vps_capture.sh"


def _reference() -> dict[str, Any]:
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


def _write_image_identity(root: Path, backend: str, expected: dict[str, Any]) -> None:
    image = expected["image"]
    image_id = expected["image_id_on_ci_runner"]
    repo_digest = expected["repo_digest"]
    (root / f"{backend}-image.txt").write_text(
        "\n".join(
            [
                f"backend={backend}",
                f"image={image}",
                f"image_id={image_id}",
                f"repo_digests={json.dumps([repo_digest])}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_runtime_digest_reference_is_complete_and_blocks_tag_only_equivalence() -> None:
    reference = _reference()

    assert reference["issue"] == 862
    assert set(reference["backends"]) == {"rustfs", "garage", "seaweedfs"}
    assert reference["policy"] == {
        "vps_capture_must_record_repo_digest": True,
        "digest_mismatch_blocks_reference_host_claim": True,
        "tag_match_without_digest_match_is_insufficient": True,
        "reference_host_followup_issue": 829,
    }

    for backend in reference["backends"].values():
        image_repository = str(backend["image"]).split(":", 1)[0]
        repo_digest = str(backend["repo_digest"])
        assert backend["image"]
        assert repo_digest.startswith(image_repository)
        assert "@sha256:" in repo_digest


def test_runtime_identity_verifier_accepts_authoritative_ci_digests(tmp_path: Path) -> None:
    module = runpy.run_path(str(VERIFIER_PATH))
    verify_runtime_identity = module["_verify_runtime_identity"]
    reference = _reference()

    for backend, expected in reference["backends"].items():
        _write_image_identity(tmp_path, backend, expected)

    verified = verify_runtime_identity(tmp_path, reference)

    assert verified == {
        backend: expected["repo_digest"] for backend, expected in reference["backends"].items()
    }


def test_runtime_identity_verifier_rejects_digest_drift(tmp_path: Path) -> None:
    module = runpy.run_path(str(VERIFIER_PATH))
    verify_runtime_identity = module["_verify_runtime_identity"]
    error_type = module["EvidenceValidationError"]
    reference = _reference()

    for backend, expected in reference["backends"].items():
        _write_image_identity(tmp_path, backend, expected)

    rustfs_path = tmp_path / "rustfs-image.txt"
    content = rustfs_path.read_text(encoding="utf-8")
    content = content.replace(
        str(reference["backends"]["rustfs"]["repo_digest"]),
        "rustfs/rustfs@sha256:" + "0" * 64,
    )
    rustfs_path.write_text(content, encoding="utf-8")

    with pytest.raises(error_type, match="rustfs runtime digest mismatch"):
        verify_runtime_identity(tmp_path, reference)


def test_vps_capture_keeps_benchmark_services_local_and_evidence_isolated() -> None:
    content = CAPTURE_PATH.read_text(encoding="utf-8")

    for port in (9000, 3900, 8333):
        assert f"-p 127.0.0.1:{port}:{port}" in content
        assert f"-p {port}:{port}" not in content

    assert 'find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit' in content
    assert "refusing to mix #862 evidence into non-empty output directory" in content
    assert 'if [[ "$(id -u)" -eq 0 ]]; then' in content
    assert "elif command -v sudo >/dev/null 2>&1; then" in content
