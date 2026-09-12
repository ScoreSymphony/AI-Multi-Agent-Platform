#!/usr/bin/env python3
"""Verify an issue #862 target-VPS evidence bundle before classification.

The capture harness records image tags and repository digests. This verifier binds
those measurements to the exact container artifacts already exercised by the
authoritative CI campaign, validates the generated summaries, and checks the
bundle checksums. A matching tag without the expected repository digest is not
accepted as equivalent runtime evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_BACKENDS = ("rustfs", "garage", "seaweedfs")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceValidationError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise EvidenceValidationError(f"missing required evidence file: {path.name}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise EvidenceValidationError(f"expected JSON object: {path.name}")
    return raw


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_image_identity(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise EvidenceValidationError(f"missing image identity file: {path.name}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    for key in ("backend", "image", "image_id", "repo_digests"):
        if not values.get(key):
            raise EvidenceValidationError(f"{path.name} is missing {key}")
    return values


def _verify_sha256_manifest(root: Path) -> int:
    path = root / "SHA256SUMS"
    if not path.is_file():
        raise EvidenceValidationError("missing SHA256SUMS")

    verified = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split(maxsplit=1)
        if len(parts) != 2 or not _SHA256_RE.fullmatch(parts[0]):
            raise EvidenceValidationError(f"invalid SHA256SUMS line: {raw_line!r}")
        relative = parts[1].lstrip("* ")
        if relative.startswith("./"):
            relative = relative[2:]
        target = root / relative
        if not target.is_file():
            raise EvidenceValidationError(f"SHA256SUMS references missing file: {relative}")
        actual = _sha256(target)
        if actual != parts[0]:
            raise EvidenceValidationError(
                f"SHA-256 mismatch for {relative}: expected {parts[0]}, got {actual}"
            )
        verified += 1

    if verified == 0:
        raise EvidenceValidationError("SHA256SUMS contains no file entries")
    return verified


def _verify_archive_digest(root: Path) -> str:
    archive = root / "issue862-storage-vps-evidence.tar.gz"
    digest_file = root / "issue862-storage-vps-evidence.tar.gz.sha256"
    if not archive.is_file() or not digest_file.is_file():
        raise EvidenceValidationError("missing final evidence archive or archive SHA-256 file")
    parts = digest_file.read_text(encoding="utf-8").strip().split(maxsplit=1)
    if len(parts) != 2 or not _SHA256_RE.fullmatch(parts[0]):
        raise EvidenceValidationError("invalid evidence archive SHA-256 file")
    actual = _sha256(archive)
    if actual != parts[0]:
        raise EvidenceValidationError(
            f"evidence archive SHA-256 mismatch: expected {parts[0]}, got {actual}"
        )
    return actual


def _verify_runtime_identity(root: Path, reference: dict[str, Any]) -> dict[str, str]:
    verified: dict[str, str] = {}
    reference_backends = reference.get("backends")
    if not isinstance(reference_backends, dict):
        raise EvidenceValidationError("runtime image reference is missing backends")

    for backend in _BACKENDS:
        expected = reference_backends.get(backend)
        if not isinstance(expected, dict):
            raise EvidenceValidationError(f"runtime image reference is missing {backend}")
        identity = _parse_image_identity(root / f"{backend}-image.txt")
        if identity["backend"] != backend:
            raise EvidenceValidationError(
                f"{backend}-image.txt records unexpected backend {identity['backend']!r}"
            )
        expected_image = expected.get("image")
        if identity["image"] != expected_image:
            raise EvidenceValidationError(
                f"{backend} image tag mismatch: expected {expected_image!r}, "
                f"got {identity['image']!r}"
            )
        try:
            repo_digests = json.loads(identity["repo_digests"])
        except json.JSONDecodeError as exc:
            raise EvidenceValidationError(
                f"{backend}-image.txt contains invalid repo_digests JSON"
            ) from exc
        if not isinstance(repo_digests, list) or not all(
            isinstance(item, str) for item in repo_digests
        ):
            raise EvidenceValidationError(
                f"{backend}-image.txt repo_digests must be a JSON string list"
            )
        expected_digest = expected.get("repo_digest")
        if not isinstance(expected_digest, str) or expected_digest not in repo_digests:
            raise EvidenceValidationError(
                f"{backend} runtime digest mismatch: expected {expected_digest!r}, "
                f"got {repo_digests!r}"
            )
        verified[backend] = expected_digest
    return verified


def _verify_summary(root: Path) -> dict[str, Any]:
    summary = _read_json(root / "storage-vps-summary.json")
    if summary.get("issue") != 862:
        raise EvidenceValidationError("summary does not belong to issue #862")
    if summary.get("evidence_class") != "ordinary-vps-reference-summary":
        raise EvidenceValidationError("summary is not ordinary target-VPS evidence")

    source_manifest = summary.get("source_manifest")
    if not isinstance(source_manifest, dict):
        raise EvidenceValidationError("summary is missing source_manifest")
    if source_manifest.get("evidence_class") != "ordinary-vps-reference":
        raise EvidenceValidationError("source manifest is not ordinary target-VPS evidence")
    platform_commit = source_manifest.get("platform_commit")
    if not isinstance(platform_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", platform_commit):
        raise EvidenceValidationError("source manifest has invalid platform_commit")

    local = summary.get("local_filesystem")
    if not isinstance(local, dict) or local.get("workload_status") != "pass":
        raise EvidenceValidationError("local filesystem workload did not pass")

    backends = summary.get("object_store_backends")
    if not isinstance(backends, dict):
        raise EvidenceValidationError("summary is missing object_store_backends")
    for backend in _BACKENDS:
        item = backends.get(backend)
        if not isinstance(item, dict) or item.get("workload_status") != "pass":
            raise EvidenceValidationError(f"{backend} VPS workload did not pass")
        active_samples = item.get("active_samples")
        if not isinstance(active_samples, int) or active_samples < 1:
            raise EvidenceValidationError(f"{backend} has no active resource samples")

    return {
        "platform_commit": platform_commit,
        "host": source_manifest.get("host"),
    }


def verify_capture(capture_dir: Path, repo_root: Path) -> dict[str, Any]:
    root = capture_dir.resolve()
    if not root.is_dir():
        raise EvidenceValidationError(f"capture directory does not exist: {root}")

    reference = _read_json(
        repo_root / "tests" / "evidence" / "issue_862" / "runtime_image_digests.json"
    )
    if reference.get("issue") != 862:
        raise EvidenceValidationError("runtime image reference does not belong to issue #862")

    runtime_digests = _verify_runtime_identity(root, reference)
    summary = _verify_summary(root)
    manifest_entries_verified = _verify_sha256_manifest(root)
    archive_sha256 = _verify_archive_digest(root)

    return {
        "schema_version": 1,
        "issue": 862,
        "status": "pass",
        "capture_dir": str(root),
        "platform_commit": summary["platform_commit"],
        "host": summary["host"],
        "runtime_repo_digests": runtime_digests,
        "sha256_manifest_entries_verified": manifest_entries_verified,
        "archive_sha256": archive_sha256,
        "classification_gate": (
            "runtime identity and evidence integrity verified; review measured VPS "
            "suitability before final classification"
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = verify_capture(args.capture_dir, repo_root)
    except (EvidenceValidationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"issue": 862, "status": "fail", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
