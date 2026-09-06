"""Release-readiness and operator metadata service."""

from __future__ import annotations

import re

from .models import (
    RELEASE_MANIFEST_SCHEMA_VERSION,
    CompatibilityStatus,
    GateStatus,
    ReleaseEvidenceKind,
    ReleaseManifest,
    ReleaseReadinessReport,
)

REQUIRED_RELEASE_GATES = frozenset(
    {
        "ci",
        "adapter_contract_tests",
        "eval_regression",
        "security",
        "compatibility_review",
        "migration_compatibility",
        "rollback_verified",
        "provenance_complete",
        "backup_restore_fresh",
    }
)
COMMIT_BOUND_RELEASE_GATES = frozenset(
    {
        "ci",
        "adapter_contract_tests",
        "eval_regression",
        "security",
        "compatibility_review",
        "migration_compatibility",
        "provenance_complete",
    }
)

_GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST = re.compile(r"^(?:sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})$")
_REFERENCE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:.+$")
_FLOATING_REVISION = re.compile(r"(^|[:/@])latest$", re.IGNORECASE)
_DIGEST_EVIDENCE_KINDS = {
    ReleaseEvidenceKind.ARTIFACT,
    ReleaseEvidenceKind.REPORT,
    ReleaseEvidenceKind.ATTESTATION,
}


def evaluate_release(manifest: ReleaseManifest) -> ReleaseReadinessReport:
    blockers: list[str] = []
    warnings: list[str] = []

    _validate_release_identity(manifest, blockers)
    _validate_dependency_sets(manifest, blockers)
    _validate_release_gates(manifest, blockers, warnings)
    _validate_artifacts(manifest, blockers)
    _validate_upstreams_and_compatibility(manifest, blockers, warnings)

    return ReleaseReadinessReport(
        release_version=manifest.release_version,
        ready=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def _validate_release_identity(manifest: ReleaseManifest, blockers: list[str]) -> None:
    if manifest.schema_version != RELEASE_MANIFEST_SCHEMA_VERSION:
        blockers.append(
            "unsupported release manifest schema_version "
            f"{manifest.schema_version!r}; expected {RELEASE_MANIFEST_SCHEMA_VERSION!r}"
        )
    if manifest.release_version != manifest.versions.platform_release:
        blockers.append(
            "release_version must match versions.platform_release "
            f"({manifest.release_version!r} != {manifest.versions.platform_release!r})"
        )
    if _GIT_COMMIT.fullmatch(manifest.source_commit) is None:
        blockers.append("source_commit must be a full 40- or 64-character Git commit SHA")
    for name, reference in (
        ("release_notes_ref", manifest.release_notes_ref),
        ("sbom_ref", manifest.sbom_ref),
        ("provenance_ref", manifest.provenance_ref),
    ):
        if _REFERENCE.fullmatch(reference) is None:
            blockers.append(f"{name} must be a scheme-qualified immutable reference")


def _validate_dependency_sets(manifest: ReleaseManifest, blockers: list[str]) -> None:
    dependency_names = [item.name for item in manifest.dependency_sets]
    if not dependency_names:
        blockers.append("at least one dependency lock/resolved set is required")
    duplicate_dependency_names = sorted(
        name for name in set(dependency_names) if dependency_names.count(name) > 1
    )
    if duplicate_dependency_names:
        blockers.append("duplicate dependency-set names: " + ", ".join(duplicate_dependency_names))
    for item in manifest.dependency_sets:
        if _REFERENCE.fullmatch(item.source_ref) is None:
            blockers.append(f"dependency set {item.name!r} has an invalid source_ref")
        if _DIGEST.fullmatch(item.digest) is None:
            blockers.append(f"dependency set {item.name!r} has an invalid digest")


def _validate_release_gates(
    manifest: ReleaseManifest,
    blockers: list[str],
    warnings: list[str],
) -> None:
    gate_by_name = {gate.name: gate for gate in manifest.gates}
    duplicate_gate_names = sorted(
        name for name in gate_by_name if sum(gate.name == name for gate in manifest.gates) > 1
    )
    if duplicate_gate_names:
        blockers.append(f"duplicate release gates: {', '.join(duplicate_gate_names)}")

    missing = sorted(REQUIRED_RELEASE_GATES - gate_by_name.keys())
    if missing:
        blockers.append(f"missing required release gates: {', '.join(missing)}")

    for gate in manifest.gates:
        if gate.required and gate.status is not GateStatus.PASSED:
            blockers.append(f"required gate {gate.name!r} is {gate.status.value}")
        elif not gate.required and gate.status is GateStatus.FAILED:
            warnings.append(f"optional gate {gate.name!r} failed")

        evidence = gate.evidence
        if _REFERENCE.fullmatch(evidence.ref) is None:
            blockers.append(f"gate {gate.name!r} has an invalid evidence reference")
        if (
            evidence.source_commit is not None
            and _GIT_COMMIT.fullmatch(evidence.source_commit) is None
        ):
            blockers.append(f"gate {gate.name!r} has an invalid evidence source_commit")
        if evidence.digest is not None and _DIGEST.fullmatch(evidence.digest) is None:
            blockers.append(f"gate {gate.name!r} has an invalid evidence digest")
        if evidence.kind in _DIGEST_EVIDENCE_KINDS and evidence.digest is None:
            blockers.append(
                f"gate {gate.name!r} evidence kind {evidence.kind.value!r} requires a digest"
            )
        if (
            gate.name in COMMIT_BOUND_RELEASE_GATES
            and evidence.source_commit != manifest.source_commit
        ):
            blockers.append(f"gate {gate.name!r} evidence is not bound to release source_commit")


def _validate_artifacts(manifest: ReleaseManifest, blockers: list[str]) -> None:
    if not manifest.artifact_hashes:
        blockers.append("release artifact hashes are missing")
    for artifact, digest in manifest.artifact_hashes.items():
        if _DIGEST.fullmatch(digest) is None:
            blockers.append(f"release artifact {artifact!r} has an invalid digest")


def _validate_upstreams_and_compatibility(
    manifest: ReleaseManifest,
    blockers: list[str],
    warnings: list[str],
) -> None:
    upstream_by_name = {upstream.component: upstream for upstream in manifest.upstreams}
    for upstream in manifest.upstreams:
        if _FLOATING_REVISION.search(upstream.revision) is not None or "*" in upstream.revision:
            blockers.append(f"upstream {upstream.component!r} uses a floating revision")
        if upstream.revision_kind == "commit" and _GIT_COMMIT.fullmatch(upstream.revision) is None:
            blockers.append(
                f"upstream {upstream.component!r} commit revision must be a full Git SHA"
            )
        if upstream.revision_kind == "digest" and _DIGEST.fullmatch(upstream.revision) is None:
            blockers.append(f"upstream {upstream.component!r} digest revision is invalid")
        if not upstream.provenance_ref:
            blockers.append(f"upstream {upstream.component!r} is missing provenance_ref")
        elif _REFERENCE.fullmatch(upstream.provenance_ref) is None:
            blockers.append(f"upstream {upstream.component!r} has an invalid provenance_ref")
        if upstream.sbom_ref is not None and _REFERENCE.fullmatch(upstream.sbom_ref) is None:
            blockers.append(f"upstream {upstream.component!r} has an invalid sbom_ref")
        for artifact, digest in upstream.artifact_hashes.items():
            if _DIGEST.fullmatch(digest) is None:
                blockers.append(
                    f"upstream {upstream.component!r} artifact {artifact!r} has an invalid digest"
                )

    for record in manifest.compatibility:
        matched_upstream = upstream_by_name.get(record.component)
        if matched_upstream is None:
            blockers.append(
                f"compatibility record {record.component!r} has no matching upstream provenance"
            )
        elif record.upstream_revision != matched_upstream.revision:
            blockers.append(
                f"compatibility record {record.component!r} revision does not match "
                "the release upstream provenance"
            )
        if record.status is CompatibilityStatus.BLOCKED:
            blockers.append(
                f"upstream {record.component!r} revision {record.upstream_revision!r} is blocked"
            )
        elif record.status is CompatibilityStatus.DEPRECATED:
            warnings.append(f"upstream {record.component!r} is deprecated")
        elif record.status is CompatibilityStatus.EXPERIMENTAL:
            warnings.append(f"upstream {record.component!r} is experimental")


def release_metadata(manifest: ReleaseManifest) -> dict[str, object]:
    report = evaluate_release(manifest)
    return {
        "release_version": manifest.release_version,
        "release_kind": manifest.release_kind.value,
        "source_commit": manifest.source_commit,
        "created_at": manifest.created_at,
        "release_notes_ref": manifest.release_notes_ref,
        "versions": manifest.versions.to_dict(),
        "dependency_sets": [item.to_dict() for item in manifest.dependency_sets],
        "upstreams": [
            {
                "component": item.component,
                "revision": item.revision,
                "source_url": item.source_url,
                "license": item.license,
                "modified": item.modified,
                "last_verified_at": item.last_verified_at,
            }
            for item in manifest.upstreams
        ],
        "compatibility": [item.to_dict() for item in manifest.compatibility],
        "gates": [item.to_dict() for item in manifest.gates],
        "sbom_ref": manifest.sbom_ref,
        "provenance_ref": manifest.provenance_ref,
        "artifact_hashes": dict(manifest.artifact_hashes),
        "release_ready": report.ready,
        "release_blockers": list(report.blockers),
        "release_warnings": list(report.warnings),
    }
