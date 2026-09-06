from dataclasses import replace

from ai_multi_agent_platform.release import (
    CompatibilityRecord,
    CompatibilityStatus,
    DependencySetKind,
    DependencySetProvenance,
    GateStatus,
    ReleaseEvidence,
    ReleaseEvidenceKind,
    ReleaseGate,
    ReleaseKind,
    ReleaseManifest,
    UpstreamProvenance,
    evaluate_release,
    release_metadata,
)
from ai_multi_agent_platform.release.service import REQUIRED_RELEASE_GATES
from ai_multi_agent_platform.upgrade.models import VersionSnapshot

SOURCE_COMMIT = "d" * 40
OTHER_COMMIT = "e" * 40
PLATFORM_DIGEST = "sha256:" + "1" * 64
UPSTREAM_DIGEST = "sha256:" + "2" * 64
DEPENDENCY_DIGEST = "sha256:" + "3" * 64
EVIDENCE_DIGEST = "sha256:" + "4" * 64
UPSTREAM_COMMIT = "a" * 40


def _versions() -> VersionSnapshot:
    return VersionSnapshot(
        platform_release="1.2.3",
        domain_schema="1.0",
        api="v1",
        migration_revision="r3",
        plugin_manifest="1",
        portable_format="1.0",
        template_schema="1",
        backup_format="1",
        worker_protocol="1.0",
        message_protocol="1.0",
        adapter_versions={"example-adapter": "1"},
        plugin_interface_versions={"tool": "1"},
    )


def _evidence(name: str, *, source_commit: str = SOURCE_COMMIT) -> ReleaseEvidence:
    return ReleaseEvidence(
        kind=ReleaseEvidenceKind.WORKFLOW_RUN,
        ref=f"github-run:{name}:123",
        source_commit=source_commit,
    )


def _manifest(*, gate_status: GateStatus = GateStatus.PASSED) -> ReleaseManifest:
    upstream = UpstreamProvenance(
        component="example-runtime",
        source_url="https://example.invalid/runtime",
        revision=UPSTREAM_COMMIT,
        revision_kind="commit",
        license="MIT",
        modified=False,
        build_status="passed",
        test_status="passed",
        last_verified_at="2026-09-06T08:00:00Z",
        artifact_hashes={"runtime.tar.zst": UPSTREAM_DIGEST},
        provenance_ref="attestation:runtime",
    )
    compatibility = CompatibilityRecord(
        component="example-runtime",
        upstream_revision=UPSTREAM_COMMIT,
        status=CompatibilityStatus.TESTED,
        tested_at="2026-09-06T08:00:00Z",
        platform_constraint="1.2.3",
    )
    gates = tuple(
        ReleaseGate(
            name=name,
            status=gate_status,
            evidence=_evidence(name),
        )
        for name in sorted(REQUIRED_RELEASE_GATES)
    )
    dependency_sets = (
        DependencySetProvenance(
            name="python-runtime",
            ecosystem="python",
            kind=DependencySetKind.RESOLVED_SET,
            source_ref="artifact:dependencies/python-runtime.json",
            digest=DEPENDENCY_DIGEST,
        ),
    )
    return ReleaseManifest(
        release_version="1.2.3",
        release_kind=ReleaseKind.PATCH,
        source_commit=SOURCE_COMMIT,
        created_at="2026-09-06T08:00:00Z",
        release_notes_ref="release-notes:1.2.3",
        versions=_versions(),
        dependency_sets=dependency_sets,
        upstreams=(upstream,),
        compatibility=(compatibility,),
        gates=gates,
        sbom_ref="sbom:1.2.3",
        provenance_ref="attestation:1.2.3",
        artifact_hashes={"ai_multi_agent_platform-1.2.3.whl": PLATFORM_DIGEST},
    )


def test_release_is_ready_only_when_required_gates_pass() -> None:
    report = evaluate_release(_manifest())
    assert report.ready is True
    assert report.blockers == ()


def test_failed_required_gate_blocks_release() -> None:
    report = evaluate_release(_manifest(gate_status=GateStatus.FAILED))
    assert report.ready is False
    assert any("required gate" in blocker for blocker in report.blockers)


def test_blocked_compatibility_blocks_release() -> None:
    manifest = _manifest()
    blocked = replace(manifest.compatibility[0], status=CompatibilityStatus.BLOCKED)
    report = evaluate_release(replace(manifest, compatibility=(blocked,)))
    assert report.ready is False
    assert any("is blocked" in blocker for blocker in report.blockers)


def test_release_metadata_exposes_operator_status() -> None:
    payload = release_metadata(_manifest())
    assert payload["release_version"] == "1.2.3"
    assert payload["release_ready"] is True
    assert payload["dependency_sets"] == [
        {
            "name": "python-runtime",
            "ecosystem": "python",
            "kind": "resolved_set",
            "source_ref": "artifact:dependencies/python-runtime.json",
            "digest": DEPENDENCY_DIGEST,
        }
    ]
    assert payload["upstreams"] == [
        {
            "component": "example-runtime",
            "revision": UPSTREAM_COMMIT,
            "source_url": "https://example.invalid/runtime",
            "license": "MIT",
            "modified": False,
            "last_verified_at": "2026-09-06T08:00:00Z",
        }
    ]


def test_release_provenance_generation_is_traceable_and_complete() -> None:
    payload = _manifest().to_dict()

    assert payload["schema_version"] == "2"
    assert payload["source_commit"] == SOURCE_COMMIT
    assert payload["versions"] == _versions().to_dict()
    assert payload["dependency_sets"] == [
        {
            "name": "python-runtime",
            "ecosystem": "python",
            "kind": "resolved_set",
            "source_ref": "artifact:dependencies/python-runtime.json",
            "digest": DEPENDENCY_DIGEST,
        }
    ]
    assert payload["provenance_ref"] == "attestation:1.2.3"
    assert payload["sbom_ref"] == "sbom:1.2.3"
    assert payload["artifact_hashes"] == {
        "ai_multi_agent_platform-1.2.3.whl": PLATFORM_DIGEST
    }

    upstreams = payload["upstreams"]
    assert isinstance(upstreams, list)
    assert upstreams[0]["revision"] == UPSTREAM_COMMIT
    assert upstreams[0]["artifact_hashes"] == {"runtime.tar.zst": UPSTREAM_DIGEST}
    assert upstreams[0]["provenance_ref"] == "attestation:runtime"


def test_commit_bound_gate_evidence_must_match_release_commit() -> None:
    manifest = _manifest()
    changed_gates = tuple(
        replace(gate, evidence=_evidence(gate.name, source_commit=OTHER_COMMIT))
        if gate.name == "security"
        else gate
        for gate in manifest.gates
    )
    report = evaluate_release(replace(manifest, gates=changed_gates))
    assert report.ready is False
    assert any("security" in blocker and "source_commit" in blocker for blocker in report.blockers)


def test_digest_evidence_kind_requires_digest() -> None:
    manifest = _manifest()
    changed_gates = tuple(
        replace(
            gate,
            evidence=ReleaseEvidence(
                kind=ReleaseEvidenceKind.REPORT,
                ref="report:security",
                source_commit=SOURCE_COMMIT,
            ),
        )
        if gate.name == "security"
        else gate
        for gate in manifest.gates
    )
    report = evaluate_release(replace(manifest, gates=changed_gates))
    assert report.ready is False
    assert any("requires a digest" in blocker for blocker in report.blockers)


def test_digest_evidence_can_bind_report_to_release_commit() -> None:
    manifest = _manifest()
    changed_gates = tuple(
        replace(
            gate,
            evidence=ReleaseEvidence(
                kind=ReleaseEvidenceKind.REPORT,
                ref="report:security",
                source_commit=SOURCE_COMMIT,
                digest=EVIDENCE_DIGEST,
            ),
        )
        if gate.name == "security"
        else gate
        for gate in manifest.gates
    )
    report = evaluate_release(replace(manifest, gates=changed_gates))
    assert report.ready is True


def test_compatibility_revision_must_match_release_upstream_provenance() -> None:
    manifest = _manifest()
    mismatched = replace(manifest.compatibility[0], upstream_revision="b" * 40)
    report = evaluate_release(replace(manifest, compatibility=(mismatched,)))
    assert report.ready is False
    assert any("revision does not match" in blocker for blocker in report.blockers)
