from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.benchmarking.private_mcp_transport_evaluation import (
    CAPABILITY_CASES,
    COMPARISON_CASES,
    COST_CASES,
    FAILURE_RECOVERY_CASES,
    IDENTITY_AUTHORIZATION_CASES,
    LATERAL_MOVEMENT_CASES,
    NETWORK_EXPOSURE_CASES,
    SECRET_CASES,
    WORKLOAD_CASES,
)
from ai_multi_agent_platform.benchmarking.private_mcp_transport_evaluation_cli import main
from ai_multi_agent_platform.benchmarking.private_mcp_transport_evidence import (
    verify_private_mcp_transport_evidence_files,
)

_EVIDENCE_PATH = "tests/evidence/issue_967/live-run/evidence.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result(case_id: str, *, status: str = "pass", ref: str = _EVIDENCE_PATH) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": status,
        "evidence_refs": [ref],
    }


def _distribution() -> dict[str, Any]:
    return {"count": 5, "p50_ms": 10.0, "p95_ms": 20.0}


def _report(
    sha256: str,
    *,
    recommendation: str | None = "adopt_reference_private_mcp_profile",
    decision_eligible: bool | None = None,
) -> dict[str, Any]:
    if decision_eligible is None:
        decision_eligible = recommendation is not None

    workload_results = []
    for case_id in sorted(WORKLOAD_CASES):
        status = "not_supported" if case_id == "streaming_or_long_running_behavior" else "pass"
        workload_results.append(_result(case_id, status=status))

    return {
        "schema_version": "1.0",
        "campaign_id": "issue-967-live-run",
        "provider": "openziti-mcp-gateway",
        "provider_revision": "8f99623d95d2f5223d2fa12b9f125688d8c80bf9",
        "platform_commit": "a" * 40,
        "started_at": "2026-09-13T20:00:00Z",
        "completed_at": "2026-09-13T20:30:00Z",
        "components": [
            {
                "name": "openziti/mcp-gateway",
                "version": "v0.1.11",
                "revision": "8f99623d95d2f5223d2fa12b9f125688d8c80bf9",
                "license": "Apache-2.0",
            }
        ],
        "topology": {
            "profile": "two-vps-self-hosted-private-mcp",
            "node_a_role": "platform MCP client",
            "node_b_role": "MCP gateway plus stdio backend",
            "client_binding": "127.0.0.1:18080",
            "backend_binding": "stdio child process; no TCP listener",
            "public_backend_exposed": False,
            "self_hosted_overlay": True,
            "recurring_paid_service_required": False,
            "overlay_control_plane_exposure": "recorded separately",
        },
        "authority_boundary": {
            "canonical_authorization_authority": "platform #15",
            "transport_identity_authority": "zrok/OpenZiti",
            "transport_identity_separate_from_authorization": True,
        },
        "secret_handling": {
            "secret_references_only": True,
            "plaintext_secret_leak_detected": False,
            "process_argv_secret_exposure_detected": False,
        },
        "network_exposure_results": [
            _result(case_id) for case_id in sorted(NETWORK_EXPOSURE_CASES)
        ],
        "identity_authorization_results": [
            _result(case_id) for case_id in sorted(IDENTITY_AUTHORIZATION_CASES)
        ],
        "capability_results": [_result(case_id) for case_id in sorted(CAPABILITY_CASES)],
        "secret_results": [_result(case_id) for case_id in sorted(SECRET_CASES)],
        "failure_recovery_results": [
            _result(case_id) for case_id in sorted(FAILURE_RECOVERY_CASES)
        ],
        "lateral_movement_results": [
            _result(case_id) for case_id in sorted(LATERAL_MOVEMENT_CASES)
        ],
        "workload_results": workload_results,
        "cost_results": [_result(case_id) for case_id in sorted(COST_CASES)],
        "comparison_results": [_result(case_id) for case_id in sorted(COMPARISON_CASES)],
        "latency": {
            "direct_local": _distribution(),
            "private_overlay_warm": _distribution(),
            "small_response": _distribution(),
            "moderate_structured_response": _distribution(),
            "connection_establishment": _distribution(),
            "reconnect": _distribution(),
            "simpler_private_warm": _distribution(),
            "streaming_supported": False,
            "streaming_or_long_running": None,
        },
        "raw_evidence": [{"path": _EVIDENCE_PATH, "sha256": sha256}],
        "decision_eligible": decision_eligible,
        "recommendation": recommendation,
    }


def _write_evidence(root: Path, *, content: str = "measured evidence\n") -> Path:
    path = root / _EVIDENCE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_report(root: Path, report: dict[str, Any]) -> Path:
    path = root / "tests/evidence/issue_967/live-run/report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_verifier_accepts_existing_hash_bound_evidence(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = _report(_sha256(evidence))

    verification = verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)

    assert verification.files_verified == 1
    assert verification.referenced_files == 1
    assert verification.verified_paths == (_EVIDENCE_PATH,)


def test_verifier_rejects_missing_retained_evidence(tmp_path: Path) -> None:
    report = _report("b" * 64)

    with pytest.raises(ValueError, match="does not exist"):
        verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)


def test_verifier_rejects_modified_evidence_after_hash_capture(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = _report(_sha256(evidence))
    evidence.write_text("modified after capture\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)


def test_verifier_rejects_result_reference_absent_from_manifest(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = _report(_sha256(evidence))
    report["network_exposure_results"][0]["evidence_refs"] = ["unmanifested.json"]

    with pytest.raises(ValueError, match="absent from raw_evidence"):
        verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)


def test_verifier_rejects_parent_traversal(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    report = _report(_sha256(evidence))
    report["raw_evidence"] = [{"path": "../outside.json", "sha256": "b" * 64}]

    with pytest.raises(ValueError, match="must not traverse"):
        verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)


def test_verifier_rejects_windows_drive_relative_path(tmp_path: Path) -> None:
    report = _report("b" * 64)
    report["raw_evidence"] = [{"path": "C:outside.json", "sha256": "b" * 64}]

    with pytest.raises(ValueError, match="must be relative"):
        verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)


def test_verifier_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-evidence.json"
    outside.write_text("outside evidence\n", encoding="utf-8")
    evidence = tmp_path / _EVIDENCE_PATH
    evidence.parent.mkdir(parents=True, exist_ok=True)
    try:
        evidence.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    report = _report(_sha256(outside))

    with pytest.raises(ValueError, match="escapes evidence_root"):
        verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)


def test_verifier_rejects_duplicate_manifest_path(tmp_path: Path) -> None:
    evidence = _write_evidence(tmp_path)
    entry = {"path": _EVIDENCE_PATH, "sha256": _sha256(evidence)}
    report = _report(_sha256(evidence))
    report["raw_evidence"] = [entry, deepcopy(entry)]

    with pytest.raises(ValueError, match="duplicate raw_evidence path"):
        verify_private_mcp_transport_evidence_files(report, evidence_root=tmp_path)


def test_cli_validate_emits_verified_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _write_evidence(tmp_path)
    report_path = _write_report(tmp_path, _report(_sha256(evidence)))

    exit_code = main(
        [
            "validate",
            "--report",
            str(report_path),
            "--evidence-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["evidence"]["files_verified"] == 1
    assert "evidence_root" not in payload["evidence"]


def test_cli_can_gate_definition_of_done(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = _write_evidence(tmp_path)
    report = _report(_sha256(evidence), recommendation=None, decision_eligible=False)
    report_path = _write_report(tmp_path, report)

    exit_code = main(
        [
            "assess",
            "--report",
            str(report_path),
            "--evidence-root",
            str(tmp_path),
            "--require-definition-of-done",
        ]
    )

    assert exit_code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["readiness"]["decision_ready"] is True
    assert payload["readiness"]["definition_of_done"] is False


def test_cli_returns_validation_error_for_hash_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = _write_evidence(tmp_path)
    report_path = _write_report(tmp_path, _report(_sha256(evidence)))
    evidence.write_text("tampered\n", encoding="utf-8")

    exit_code = main(
        [
            "validate",
            "--report",
            str(report_path),
            "--evidence-root",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "SHA-256 mismatch" in captured.err
