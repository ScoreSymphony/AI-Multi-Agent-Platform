import json

import pytest

from ai_multi_agent_platform.release import (
    GateStatus,
    ReleaseEvidenceKind,
    UpdateGateEvidence,
    UpdateValidationEvidenceError,
    UpdateValidationEvidenceSet,
    load_update_validation_evidence,
    validate_update_adoption_evidence,
)

REQUIRED_GATES = frozenset(
    {
        "adapter_contract_tests",
        "eval_regression",
        "security",
        "compatibility_review",
    }
)
CANDIDATE_REVISION = "b" * 40


def _validation() -> dict[str, GateStatus]:
    return {name: GateStatus.PASSED for name in REQUIRED_GATES}


def _evidence() -> UpdateValidationEvidenceSet:
    return UpdateValidationEvidenceSet(
        component="runtime",
        candidate_revision=CANDIDATE_REVISION,
        gates={
            "adapter_contract_tests": UpdateGateEvidence(
                status=GateStatus.PASSED,
                kind=ReleaseEvidenceKind.WORKFLOW_RUN,
                ref="workflow:run/123",
            ),
            "eval_regression": UpdateGateEvidence(
                status=GateStatus.PASSED,
                kind=ReleaseEvidenceKind.REPORT,
                ref="report:eval.json",
                digest="sha256:" + "1" * 64,
            ),
            "security": UpdateGateEvidence(
                status=GateStatus.PASSED,
                kind=ReleaseEvidenceKind.REPORT,
                ref="report:security.json",
                digest="sha256:" + "2" * 64,
            ),
            "compatibility_review": UpdateGateEvidence(
                status=GateStatus.PASSED,
                kind=ReleaseEvidenceKind.REVIEW,
                ref="review:upstream/runtime",
            ),
        },
    )


def test_revision_bound_evidence_accepts_exact_candidate() -> None:
    validate_update_adoption_evidence(
        component="runtime",
        candidate_revision=CANDIDATE_REVISION,
        validation=_validation(),
        evidence=_evidence(),
        required_gates=REQUIRED_GATES,
    )


def test_revision_bound_evidence_rejects_missing_gate() -> None:
    evidence = _evidence()
    gates = dict(evidence.gates)
    gates.pop("security")
    incomplete = UpdateValidationEvidenceSet(
        component=evidence.component,
        candidate_revision=evidence.candidate_revision,
        gates=gates,
    )
    with pytest.raises(UpdateValidationEvidenceError, match="security"):
        validate_update_adoption_evidence(
            component="runtime",
            candidate_revision=CANDIDATE_REVISION,
            validation=_validation(),
            evidence=incomplete,
            required_gates=REQUIRED_GATES,
        )


def test_report_evidence_requires_cryptographic_digest() -> None:
    with pytest.raises(UpdateValidationEvidenceError, match="cryptographic digest"):
        UpdateGateEvidence(
            status=GateStatus.PASSED,
            kind=ReleaseEvidenceKind.REPORT,
            ref="report:security.json",
        )


def test_evidence_loader_round_trips_typed_contract(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_evidence().to_dict()), encoding="utf-8")
    loaded = load_update_validation_evidence(path)
    assert loaded.to_dict() == _evidence().to_dict()


def test_evidence_loader_rejects_floating_candidate_revision(tmp_path) -> None:
    payload = _evidence().to_dict()
    payload["candidate_revision"] = "latest"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(UpdateValidationEvidenceError, match="immutable"):
        load_update_validation_evidence(path)
