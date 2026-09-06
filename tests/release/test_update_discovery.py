from pathlib import Path

import pytest

from ai_multi_agent_platform.release.adoption import (
    UpdateGateEvidence,
    UpdateValidationEvidenceSet,
)
from ai_multi_agent_platform.release.discovery import (
    CompatibilityInventory,
    ObservedUpstream,
    UpdateClassification,
    UpdateDiscoveryError,
    UpdateDisposition,
    UpstreamInventoryEntry,
    evaluate_update_candidates,
    load_compatibility_inventory,
    load_observations,
    record_reviewed_candidate,
)
from ai_multi_agent_platform.release.models import GateStatus, ReleaseEvidenceKind
from ai_multi_agent_platform.upgrade.models import VersionSnapshot


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
        adapter_versions={"runtime-adapter": "1"},
        plugin_interface_versions={"tool": "1"},
    )


def _inventory() -> CompatibilityInventory:
    return CompatibilityInventory(
        versions=_versions(),
        last_reviewed_at="2026-09-01T00:00:00Z",
        entries=(
            UpstreamInventoryEntry(
                component="runtime",
                source_url="https://example.invalid/runtime",
                revision="v1.0.0",
                compatibility_status="tested",
                integration_mode="optional adapter",
                boundary="Executor",
                license="MIT",
                last_checked_at="2026-09-01T00:00:00Z",
                latest_known_revision="v1.0.0",
                update_risk="high",
                local_modifications=False,
            ),
        ),
    )


def _passed_validation() -> dict[str, GateStatus]:
    return {
        "adapter_contract_tests": GateStatus.PASSED,
        "eval_regression": GateStatus.PASSED,
        "security": GateStatus.PASSED,
        "compatibility_review": GateStatus.PASSED,
    }


def _passed_evidence(*, revision: str = "v1.1.0") -> UpdateValidationEvidenceSet:
    return UpdateValidationEvidenceSet(
        component="runtime",
        candidate_revision=revision,
        gates={
            name: UpdateGateEvidence(
                status=GateStatus.PASSED,
                kind=ReleaseEvidenceKind.WORKFLOW_RUN,
                ref=f"workflow:run/{name}",
            )
            for name in _passed_validation()
        },
    )


def _observed(
    *,
    revision: str = "v1.1.0",
    license: str = "MIT",
    classifications: tuple[UpdateClassification, ...] = (UpdateClassification.FEATURE,),
    patch_conflicts: tuple[str, ...] = (),
    validation: dict[str, GateStatus] | None = None,
) -> ObservedUpstream:
    return ObservedUpstream(
        component="runtime",
        source_url="https://example.invalid/runtime",
        revision=revision,
        license=license,
        classifications=classifications,
        patch_conflicts=patch_conflicts,
        validation=validation,
    )


def test_detects_newer_candidate_without_mutating_baseline() -> None:
    inventory = _inventory()
    report = evaluate_update_candidates(
        inventory,
        (_observed(),),
        observed_at="2026-09-06T00:00:00Z",
    )
    candidate = report.candidates[0]
    assert candidate.disposition is UpdateDisposition.UPDATE_AVAILABLE
    assert candidate.candidate_revision == "v1.1.0"
    assert inventory.entries[0].revision == "v1.0.0"


def test_no_update_case_is_current() -> None:
    report = evaluate_update_candidates(_inventory(), (_observed(revision="v1.0.0"),))
    assert report.candidates[0].disposition is UpdateDisposition.CURRENT
    assert report.update_available is False


def test_license_metadata_change_requires_manual_review() -> None:
    report = evaluate_update_candidates(
        _inventory(),
        (_observed(license="Apache-2.0"),),
    )
    candidate = report.candidates[0]
    assert candidate.disposition is UpdateDisposition.MANUAL_REVIEW
    assert UpdateClassification.LICENSE_GOVERNANCE in candidate.classifications
    assert candidate.manual_review_required is True


def test_breaking_candidate_requires_manual_review() -> None:
    report = evaluate_update_candidates(
        _inventory(),
        (_observed(classifications=(UpdateClassification.BREAKING_API,)),),
    )
    assert report.candidates[0].disposition is UpdateDisposition.MANUAL_REVIEW


def test_adapter_contract_regression_blocks_candidate() -> None:
    inventory = _inventory()
    report = evaluate_update_candidates(
        inventory,
        (_observed(validation={"adapter_contract_tests": GateStatus.FAILED}),),
    )
    assert report.candidates[0].disposition is UpdateDisposition.BLOCKED
    assert inventory.entries[0].revision == "v1.0.0"


def test_evaluation_regression_blocks_candidate() -> None:
    report = evaluate_update_candidates(
        _inventory(),
        (_observed(validation={"eval_regression": GateStatus.FAILED}),),
    )
    assert report.candidates[0].disposition is UpdateDisposition.BLOCKED


def test_local_patch_conflict_is_reported_and_blocks_candidate() -> None:
    report = evaluate_update_candidates(
        _inventory(),
        (_observed(patch_conflicts=("patch-001",)),),
    )
    candidate = report.candidates[0]
    assert candidate.disposition is UpdateDisposition.BLOCKED
    assert any("patch-001" in reason for reason in candidate.reasons)


def test_disabled_and_offline_modes_do_not_claim_an_update() -> None:
    disabled = evaluate_update_candidates(_inventory(), enabled=False)
    offline = evaluate_update_candidates(_inventory(), offline=True)
    assert disabled.mode is UpdateDisposition.DISABLED
    assert disabled.candidates[0].disposition is UpdateDisposition.DISABLED
    assert offline.mode is UpdateDisposition.OFFLINE
    assert offline.candidates[0].disposition is UpdateDisposition.OFFLINE


def test_reviewed_candidate_can_produce_new_matrix_without_mutating_old_one() -> None:
    inventory = _inventory()
    candidate = evaluate_update_candidates(
        inventory,
        (_observed(validation=_passed_validation()),),
    ).candidates[0]
    updated = record_reviewed_candidate(
        inventory,
        candidate,
        compatibility_status="tested",
        reviewed_at="2026-09-06T00:00:00Z",
        validation_evidence=_passed_evidence(),
    )
    assert inventory.entries[0].revision == "v1.0.0"
    assert updated.entries[0].revision == "v1.1.0"
    assert updated.last_reviewed_at == "2026-09-06T00:00:00Z"
    assert updated.versions == inventory.versions
    assert updated.to_dict()["versions"] == _versions().to_dict()


def test_candidate_cannot_be_adopted_with_missing_validation_gate() -> None:
    validation = _passed_validation()
    validation.pop("security")
    candidate = evaluate_update_candidates(
        _inventory(),
        (_observed(validation=validation),),
    ).candidates[0]
    with pytest.raises(UpdateDiscoveryError, match="security"):
        record_reviewed_candidate(
            _inventory(),
            candidate,
            compatibility_status="tested",
            reviewed_at="2026-09-06T00:00:00Z",
            validation_evidence=_passed_evidence(),
        )


def test_candidate_cannot_be_adopted_with_not_run_validation_gate() -> None:
    validation = _passed_validation()
    validation["compatibility_review"] = GateStatus.NOT_RUN
    candidate = evaluate_update_candidates(
        _inventory(),
        (_observed(validation=validation),),
    ).candidates[0]
    with pytest.raises(UpdateDiscoveryError, match="compatibility_review"):
        record_reviewed_candidate(
            _inventory(),
            candidate,
            compatibility_status="tested",
            reviewed_at="2026-09-06T00:00:00Z",
            validation_evidence=_passed_evidence(),
        )


def test_candidate_cannot_be_adopted_with_evidence_for_different_revision() -> None:
    candidate = evaluate_update_candidates(
        _inventory(),
        (_observed(validation=_passed_validation()),),
    ).candidates[0]
    with pytest.raises(UpdateDiscoveryError, match="different candidate revision"):
        record_reviewed_candidate(
            _inventory(),
            candidate,
            compatibility_status="tested",
            reviewed_at="2026-09-06T00:00:00Z",
            validation_evidence=_passed_evidence(revision="v1.2.0"),
        )


def test_manual_review_candidate_cannot_be_recorded_without_approval() -> None:
    candidate = evaluate_update_candidates(
        _inventory(),
        (
            _observed(
                classifications=(UpdateClassification.UNKNOWN,),
                validation=_passed_validation(),
            ),
        ),
    ).candidates[0]
    with pytest.raises(UpdateDiscoveryError, match="manual review approval"):
        record_reviewed_candidate(
            _inventory(),
            candidate,
            compatibility_status="tested",
            reviewed_at="2026-09-06T00:00:00Z",
            validation_evidence=_passed_evidence(),
        )


def test_observation_loader_rejects_floating_latest(tmp_path: Path) -> None:
    path = tmp_path / "observations.json"
    path.write_text(
        """{
          "schema_version": "1",
          "observed_at": "2026-09-06T00:00:00Z",
          "components": [{
            "component": "runtime",
            "source_url": "https://example.invalid/runtime",
            "revision": "latest",
            "license": "MIT",
            "classifications": [],
            "patch_conflicts": [],
            "validation": {}
          }]
        }""",
        encoding="utf-8",
    )
    with pytest.raises(UpdateDiscoveryError):
        load_observations(path)


def test_packaged_compatibility_inventory_is_loadable() -> None:
    inventory = load_compatibility_inventory()
    assert inventory.schema_version == "2"
    assert inventory.entries
    assert inventory.versions.worker_protocol
    assert inventory.versions.message_protocol
    assert all(entry.revision.lower() != "latest" for entry in inventory.entries)
