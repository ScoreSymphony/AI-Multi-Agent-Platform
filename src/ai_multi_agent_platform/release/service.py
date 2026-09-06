"""Release-readiness and operator metadata service."""

from __future__ import annotations

from .models import (
    CompatibilityStatus,
    GateStatus,
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


def evaluate_release(manifest: ReleaseManifest) -> ReleaseReadinessReport:
    blockers: list[str] = []
    warnings: list[str] = []

    if manifest.release_version != manifest.versions.platform_release:
        blockers.append(
            "release_version must match versions.platform_release "
            f"({manifest.release_version!r} != {manifest.versions.platform_release!r})"
        )

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

    if not manifest.artifact_hashes:
        blockers.append("release artifact hashes are missing")

    upstream_names = {upstream.component for upstream in manifest.upstreams}
    for upstream in manifest.upstreams:
        if not upstream.provenance_ref:
            blockers.append(f"upstream {upstream.component!r} is missing provenance_ref")

    for record in manifest.compatibility:
        if record.component not in upstream_names:
            blockers.append(
                f"compatibility record {record.component!r} has no matching upstream provenance"
            )
        if record.status is CompatibilityStatus.BLOCKED:
            blockers.append(
                f"upstream {record.component!r} revision {record.upstream_revision!r} is blocked"
            )
        elif record.status is CompatibilityStatus.DEPRECATED:
            warnings.append(f"upstream {record.component!r} is deprecated")
        elif record.status is CompatibilityStatus.EXPERIMENTAL:
            warnings.append(f"upstream {record.component!r} is experimental")

    return ReleaseReadinessReport(
        release_version=manifest.release_version,
        ready=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )


def release_metadata(manifest: ReleaseManifest) -> dict[str, object]:
    report = evaluate_release(manifest)
    return {
        "release_version": manifest.release_version,
        "release_kind": manifest.release_kind.value,
        "source_commit": manifest.source_commit,
        "created_at": manifest.created_at,
        "release_notes_ref": manifest.release_notes_ref,
        "versions": manifest.versions.to_dict(),
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
        "sbom_ref": manifest.sbom_ref,
        "provenance_ref": manifest.provenance_ref,
        "artifact_hashes": dict(manifest.artifact_hashes),
        "release_ready": report.ready,
        "release_blockers": list(report.blockers),
        "release_warnings": list(report.warnings),
    }
