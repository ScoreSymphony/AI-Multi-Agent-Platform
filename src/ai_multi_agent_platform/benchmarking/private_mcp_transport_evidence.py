"""File-integrity verification for retained private remote MCP evaluation evidence.

The report schema proves shape and decision semantics. This module additionally proves that retained
file references resolve inside an explicitly selected evidence root and still match their recorded
SHA-256 digests. It never interprets provider-native credentials or network identities.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .private_mcp_transport_evaluation import validate_private_mcp_transport_evaluation_report

_RESULT_FIELDS = (
    "network_exposure_results",
    "identity_authorization_results",
    "capability_results",
    "secret_results",
    "failure_recovery_results",
    "lateral_movement_results",
    "workload_results",
    "cost_results",
    "comparison_results",
)


@dataclass(frozen=True, slots=True)
class PrivateMCPEvidenceVerification:
    """Summary of one successful retained-evidence integrity verification."""

    evidence_root: str
    files_verified: int
    referenced_files: int
    verified_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_root": self.evidence_root,
            "files_verified": self.files_verified,
            "referenced_files": self.referenced_files,
            "verified_paths": list(self.verified_paths),
        }


def verify_private_mcp_transport_evidence_files(
    report: Mapping[str, Any],
    *,
    evidence_root: Path,
) -> PrivateMCPEvidenceVerification:
    """Verify retained evidence files, hashes and result-to-manifest references.

    ``raw_evidence`` paths are deliberately interpreted relative to an explicit root rather than the
    process working directory or the report location. Paths must be canonical forward-slash relative
    paths. Absolute paths, traversal, symlink escapes and duplicate manifest entries fail closed.
    """

    validate_private_mcp_transport_evaluation_report(report)

    root = evidence_root.expanduser().resolve()
    if not root.exists():
        raise ValueError(f"evidence_root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"evidence_root must be a directory: {root}")

    raw_evidence = _require_sequence(report.get("raw_evidence"), "raw_evidence")
    manifest: dict[str, Path] = {}
    for index, item in enumerate(raw_evidence):
        evidence = _require_mapping(item, f"raw_evidence[{index}]")
        path_text = _require_str(evidence, "path", f"raw_evidence[{index}]")
        expected_sha256 = _require_str(evidence, "sha256", f"raw_evidence[{index}]")
        if path_text in manifest:
            raise ValueError(f"duplicate raw_evidence path {path_text!r}")

        candidate = _resolve_evidence_path(root, path_text)
        if not candidate.exists():
            raise ValueError(f"retained evidence file does not exist: {path_text}")
        if not candidate.is_file():
            raise ValueError(f"retained evidence path is not a regular file: {path_text}")

        actual_sha256 = _file_sha256(candidate)
        if actual_sha256 != expected_sha256:
            raise ValueError(
                "retained evidence SHA-256 mismatch for "
                f"{path_text!r}: expected {expected_sha256}, got {actual_sha256}"
            )
        manifest[path_text] = candidate

    referenced: set[str] = set()
    for field in _RESULT_FIELDS:
        results = _require_sequence(report.get(field), field)
        for index, item in enumerate(results):
            result = _require_mapping(item, f"{field}[{index}]")
            refs = _require_sequence(result.get("evidence_refs"), f"{field}[{index}].evidence_refs")
            for ref_index, ref in enumerate(refs):
                if not isinstance(ref, str) or not ref:
                    raise ValueError(
                        f"{field}[{index}].evidence_refs[{ref_index}] must be a non-empty string"
                    )
                referenced.add(ref)
                if ref not in manifest:
                    raise ValueError(
                        f"{field}[{index}] references evidence {ref!r} that is absent from raw_evidence"
                    )

    return PrivateMCPEvidenceVerification(
        evidence_root=str(root),
        files_verified=len(manifest),
        referenced_files=len(referenced),
        verified_paths=tuple(sorted(manifest)),
    )


def _resolve_evidence_path(root: Path, path_text: str) -> Path:
    if "\\" in path_text:
        raise ValueError(
            f"raw_evidence path must use canonical forward slashes: {path_text!r}"
        )
    posix_path = PurePosixPath(path_text)
    if posix_path.is_absolute() or PureWindowsPath(path_text).is_absolute():
        raise ValueError(f"raw_evidence path must be relative: {path_text!r}")
    if ".." in posix_path.parts:
        raise ValueError(f"raw_evidence path must not traverse outside evidence_root: {path_text!r}")
    if posix_path.as_posix() != path_text:
        raise ValueError(f"raw_evidence path must be normalized: {path_text!r}")

    candidate = (root / Path(*posix_path.parts)).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"raw_evidence path escapes evidence_root: {path_text!r}")
    return candidate


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be an array")
    return value


def _require_str(mapping: Mapping[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}.{key} must be a non-empty string")
    return value
