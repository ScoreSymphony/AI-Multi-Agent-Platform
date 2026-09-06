from ai_multi_agent_platform.release import (
    CompatibilityRecord,
    CompatibilityStatus,
    GateStatus,
    ReleaseGate,
    ReleaseKind,
    ReleaseManifest,
    UpstreamProvenance,
    evaluate_release,
    release_metadata,
)
from ai_multi_agent_platform.release.service import REQUIRED_RELEASE_GATES
from ai_multi_agent_platform.upgrade.models import VersionSnapshot


def _versions() -> VersionSnapshot:
    return VersionSnapshot(
        platform_release="1.2.3",
        domain_schema="1.0",
        api="v1",
        migration_revision="r3",
        plugin_manifest="1",
        portable_format="1",
        template_schema="1",
        backup_format="1",
        worker_protocol="1",
        message_protocol="1",
    )


def _manifest(*, gate_status: GateStatus = GateStatus.PASSED) -> ReleaseManifest:
    upstream = UpstreamProvenance(
        component="example-runtime",
        source_url="https://example.invalid/runtime",
        revision="abc123",
        revision_kind="commit",
        license="MIT",
        modified=False,
        build_status="passed",
        test_status="passed",
        last_verified_at="2026-09-06T08:00:00Z",
        artifact_hashes={"runtime.tar.zst": "sha256:123"},
        provenance_ref="attestation:runtime",
    )
    compatibility = CompatibilityRecord(
        component="example-runtime",
        upstream_revision="abc123",
        status=CompatibilityStatus.TESTED,
        tested_at="2026-09-06T08:00:00Z",
        platform_constraint="1.2.3",
    )
    gates = tuple(
        ReleaseGate(name=name, status=gate_status, evidence=f"ci:{name}")
        for name in sorted(REQUIRED_RELEASE_GATES)
    )
    return ReleaseManifest(
        release_version="1.2.3",
        release_kind=ReleaseKind.PATCH,
        source_commit="deadbeef",
        created_at="2026-09-06T08:00:00Z",
        release_notes_ref="release-notes:1.2.3",
        versions=_versions(),
        upstreams=(upstream,),
        compatibility=(compatibility,),
        gates=gates,
        sbom_ref="sbom:1.2.3",
        provenance_ref="attestation:1.2.3",
        artifact_hashes={"ai_multi_agent_platform-1.2.3.whl": "sha256:platform"},
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
    blocked = CompatibilityRecord(
        component="example-runtime",
        upstream_revision="abc123",
        status=CompatibilityStatus.BLOCKED,
        tested_at="2026-09-06T08:00:00Z",
        platform_constraint="1.2.3",
    )
    changed = ReleaseManifest(
        release_version=manifest.release_version,
        release_kind=manifest.release_kind,
        source_commit=manifest.source_commit,
        created_at=manifest.created_at,
        release_notes_ref=manifest.release_notes_ref,
        versions=manifest.versions,
        upstreams=manifest.upstreams,
        compatibility=(blocked,),
        gates=manifest.gates,
        sbom_ref=manifest.sbom_ref,
        provenance_ref=manifest.provenance_ref,
        artifact_hashes=manifest.artifact_hashes,
    )
    report = evaluate_release(changed)
    assert report.ready is False
    assert any("is blocked" in blocker for blocker in report.blockers)


def test_release_metadata_exposes_operator_status() -> None:
    payload = release_metadata(_manifest())
    assert payload["release_version"] == "1.2.3"
    assert payload["release_ready"] is True
    assert payload["upstreams"] == [
        {
            "component": "example-runtime",
            "revision": "abc123",
            "source_url": "https://example.invalid/runtime",
            "license": "MIT",
            "modified": False,
            "last_verified_at": "2026-09-06T08:00:00Z",
        }
    ]


def test_release_provenance_generation_is_traceable_and_complete() -> None:
    payload = _manifest().to_dict()

    assert payload["source_commit"] == "deadbeef"
    assert payload["versions"] == _versions().to_dict()
    assert payload["provenance_ref"] == "attestation:1.2.3"
    assert payload["sbom_ref"] == "sbom:1.2.3"
    assert payload["artifact_hashes"] == {"ai_multi_agent_platform-1.2.3.whl": "sha256:platform"}

    upstreams = payload["upstreams"]
    assert isinstance(upstreams, list)
    assert upstreams[0]["revision"] == "abc123"
    assert upstreams[0]["artifact_hashes"] == {"runtime.tar.zst": "sha256:123"}
    assert upstreams[0]["provenance_ref"] == "attestation:runtime"
