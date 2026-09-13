from __future__ import annotations

from copy import deepcopy
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
    assess_private_mcp_transport_evaluation,
    validate_private_mcp_transport_evaluation_report,
)


def _result(case_id: str, status: str = "pass") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": status,
        "evidence_refs": [f"evidence/{case_id}.json"],
    }


def _distribution() -> dict[str, Any]:
    return {"count": 10, "p50_ms": 12.0, "p95_ms": 20.0}


def _report(
    *,
    recommendation: str | None = "adopt_reference_private_mcp_profile",
    decision_eligible: bool | None = None,
    streaming_supported: bool = True,
) -> dict[str, Any]:
    if decision_eligible is None:
        decision_eligible = recommendation is not None

    workload_results = [_result(case_id) for case_id in sorted(WORKLOAD_CASES)]
    if not streaming_supported:
        for index, result in enumerate(workload_results):
            if result["case_id"] == "streaming_or_long_running_behavior":
                workload_results[index] = _result(result["case_id"], "not_supported")
                break

    return {
        "schema_version": "1.0",
        "campaign_id": "issue-967-openziti-mcp-gateway-v0.1.11",
        "provider": "openziti-mcp-gateway",
        "provider_revision": "8f99623d95d2f5223d2fa12b9f125688d8c80bf9",
        "platform_commit": "a" * 40,
        "started_at": "2026-09-13T20:00:00Z",
        "completed_at": "2026-09-13T20:10:00Z",
        "components": [
            {
                "name": "openziti/mcp-gateway",
                "version": "v0.1.11",
                "revision": "8f99623d95d2f5223d2fa12b9f125688d8c80bf9",
                "license": "Apache-2.0",
            },
            {
                "name": "openziti/zrok",
                "version": "v2.0.0-rc7",
                "revision": "a325978114282cfb59d794f67c5d1e82956e816a",
                "license": "Apache-2.0",
            },
        ],
        "topology": {
            "profile": "two-node-self-hosted-private-mcp",
            "node_a_role": "platform MCP client",
            "node_b_role": "MCP gateway plus loopback backend",
            "client_binding": "127.0.0.1:18080",
            "backend_binding": "stdio child process; no TCP listener",
            "public_backend_exposed": False,
            "self_hosted_overlay": True,
            "recurring_paid_service_required": False,
            "overlay_control_plane_exposure": "captured separately from MCP backend exposure",
        },
        "authority_boundary": {
            "canonical_authorization_authority": "platform #15 authorization/approval",
            "transport_identity_authority": "zrok/OpenZiti identity",
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
            "streaming_supported": streaming_supported,
            "streaming_or_long_running": _distribution() if streaming_supported else None,
        },
        "raw_evidence": [
            {"path": "tests/evidence/issue_967/run-001/raw.jsonl", "sha256": "b" * 64}
        ],
        "decision_eligible": decision_eligible,
        "recommendation": recommendation,
    }


def _replace_result(
    report: dict[str, Any],
    field: str,
    case_id: str,
    status: str,
) -> None:
    for index, result in enumerate(report[field]):
        if result["case_id"] == case_id:
            report[field][index] = _result(case_id, status)
            return
    raise AssertionError(f"case {field}:{case_id} not found")


def test_validator_accepts_complete_private_transport_report() -> None:
    validate_private_mcp_transport_evaluation_report(_report())


def test_complete_passing_evidence_can_be_adoptable() -> None:
    readiness = assess_private_mcp_transport_evaluation(_report())

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is True
    assert readiness.definition_of_done is True
    assert readiness.recommendation == "adopt_reference_private_mcp_profile"
    assert readiness.blockers == ()
    assert readiness.adoption_blockers == ()


def test_evidence_can_be_decision_ready_before_final_recommendation() -> None:
    readiness = assess_private_mcp_transport_evaluation(
        _report(recommendation=None, decision_eligible=False)
    )

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is True
    assert readiness.definition_of_done is False
    assert "final recommendation is not recorded" in readiness.blockers
    assert "report is not marked decision_eligible" in readiness.blockers


def test_missing_mandatory_case_blocks_decision_readiness() -> None:
    report = _report(recommendation=None, decision_eligible=False)
    report["failure_recovery_results"] = report["failure_recovery_results"][:-1]

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert readiness.adoption_eligible is False
    assert readiness.missing_cases
    assert "mandatory evaluation cases are missing" in readiness.blockers


def test_measured_security_failure_allows_negative_decision_but_blocks_adoption() -> None:
    report = _report(recommendation="reject/defer")
    _replace_result(
        report,
        "identity_authorization_results",
        "unauthorized_valid_transport_denied",
        "fail",
    )

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is True
    assert readiness.failed_cases
    assert any("unauthorized_valid_transport_denied" in item for item in readiness.failed_cases)


def test_hard_security_failure_rejects_experimental_only_recommendation() -> None:
    report = _report(recommendation="experimental_only")
    _replace_result(
        report,
        "capability_results",
        "gateway_filter_cannot_widen",
        "fail",
    )

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is False
    assert (
        "hard security/cost blockers require a negative final recommendation" in readiness.blockers
    )


@pytest.mark.parametrize(
    "case_id",
    [
        "established_session_revocation_fails_closed",
        "service_removal_reconfiguration_fails_closed",
    ],
)
def test_hard_recovery_failure_requires_negative_recommendation(case_id: str) -> None:
    report = _report(recommendation="experimental_only")
    _replace_result(report, "failure_recovery_results", case_id, "fail")

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is False
    assert (
        "hard security/cost blockers require a negative final recommendation" in readiness.blockers
    )


def test_operational_failure_can_support_experimental_only() -> None:
    report = _report(recommendation="experimental_only")
    _replace_result(report, "failure_recovery_results", "gateway_restart", "fail")

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is True


@pytest.mark.parametrize(
    ("field", "value", "blocker"),
    [
        ("public_backend_exposed", True, "backend MCP service is publicly exposed"),
        ("self_hosted_overlay", False, "self-hosted overlay path is not proven"),
        (
            "recurring_paid_service_required",
            True,
            "evaluated path requires a new recurring paid service",
        ),
    ],
)
def test_topology_failure_allows_negative_decision_but_blocks_adoption(
    field: str,
    value: bool,
    blocker: str,
) -> None:
    report = _report(recommendation="prefer_simpler_private_networking")
    report["topology"][field] = value

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is True
    assert blocker in readiness.adoption_blockers


def test_plaintext_secret_leak_requires_negative_recommendation() -> None:
    report = _report(recommendation="reject/defer")
    report["secret_handling"]["plaintext_secret_leak_detected"] = True

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is True
    assert "plaintext credential material leaked into retained evidence" in (
        readiness.adoption_blockers
    )


def test_process_argv_secret_exposure_can_support_prefer_simpler_decision() -> None:
    report = _report(recommendation="prefer_simpler_private_networking")
    report["secret_handling"]["process_argv_secret_exposure_detected"] = True

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is True
    assert "credential material is exposed through process argv" in readiness.adoption_blockers


def test_adopt_recommendation_conflicts_with_measured_adoption_blocker() -> None:
    report = _report(recommendation="adopt_reference_private_mcp_profile")
    _replace_result(report, "failure_recovery_results", "gateway_restart", "fail")

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is False
    assert readiness.definition_of_done is False
    assert "adopt recommendation conflicts with measured adoption blockers" in readiness.blockers


def test_comparison_evidence_must_be_complete() -> None:
    report = _report(recommendation=None, decision_eligible=False)
    _replace_result(
        report,
        "comparison_results",
        "simpler_private_route_baseline_recorded",
        "fail",
    )

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert "required comparison baseline evidence is incomplete" in readiness.blockers


def test_latency_requires_repeatable_sample_count() -> None:
    report = _report(recommendation=None, decision_eligible=False)
    report["latency"]["reconnect"]["count"] = 1

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert "latency.reconnect has fewer than 5 retained samples" in readiness.blockers


def test_streaming_can_be_explicitly_not_supported() -> None:
    report = _report(streaming_supported=False)

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is True
    assert readiness.adoption_eligible is True
    assert readiness.definition_of_done is True


def test_validator_rejects_inverted_latency_distribution() -> None:
    report = _report(recommendation=None, decision_eligible=False)
    report["latency"]["private_overlay_warm"]["p95_ms"] = 1.0

    with pytest.raises(ValueError, match="p95_ms must be >= p50_ms"):
        validate_private_mcp_transport_evaluation_report(report)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_validator_rejects_non_finite_latency(value: float) -> None:
    report = _report(recommendation=None, decision_eligible=False)
    report["latency"]["direct_local"]["p50_ms"] = value

    with pytest.raises(ValueError, match="non-finite numeric evidence"):
        validate_private_mcp_transport_evaluation_report(report)


def test_schema_rejects_recommendation_without_decision_eligible() -> None:
    report = deepcopy(_report(recommendation=None, decision_eligible=False))
    report["recommendation"] = "prefer_simpler_private_networking"

    with pytest.raises(ValueError, match="decision_eligible"):
        validate_private_mcp_transport_evaluation_report(report)
