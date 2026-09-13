from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from ai_multi_agent_platform.benchmarking.private_mcp_transport_evaluation import (
    CAPABILITY_CASES,
    COST_CASES,
    FAILURE_RECOVERY_CASES,
    IDENTITY_AUTHORIZATION_CASES,
    LATERAL_MOVEMENT_CASES,
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


def _report(*, complete: bool = True) -> dict[str, Any]:
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
        },
        "identity_authorization_results": [
            _result(case_id) for case_id in sorted(IDENTITY_AUTHORIZATION_CASES)
        ],
        "capability_results": [_result(case_id) for case_id in sorted(CAPABILITY_CASES)],
        "failure_recovery_results": [
            _result(case_id) for case_id in sorted(FAILURE_RECOVERY_CASES)
        ],
        "lateral_movement_results": [
            _result(case_id) for case_id in sorted(LATERAL_MOVEMENT_CASES)
        ],
        "cost_results": [_result(case_id) for case_id in sorted(COST_CASES)],
        "latency": {
            "direct_local": _distribution(),
            "private_overlay_warm": _distribution(),
            "connection_establishment": _distribution(),
            "reconnect": _distribution(),
        },
        "raw_evidence": [
            {"path": "tests/evidence/issue_967/run-001/raw.jsonl", "sha256": "b" * 64}
        ],
        "decision_eligible": complete,
        "recommendation": "experimental_only" if complete else None,
    }


def test_validator_accepts_complete_private_transport_report() -> None:
    validate_private_mcp_transport_evaluation_report(_report())


def test_readiness_accepts_complete_evidence_and_exactly_one_recommendation() -> None:
    readiness = assess_private_mcp_transport_evaluation(_report())

    assert readiness.decision_ready is True
    assert readiness.definition_of_done is True
    assert readiness.recommendation == "experimental_only"
    assert readiness.blockers == ()


def test_readiness_can_be_decision_ready_before_final_recommendation() -> None:
    readiness = assess_private_mcp_transport_evaluation(_report(complete=False))

    assert readiness.decision_ready is True
    assert readiness.definition_of_done is False
    assert "final recommendation is not recorded" in readiness.blockers
    assert "report is not marked decision_eligible" in readiness.blockers


def test_missing_mandatory_case_blocks_decision() -> None:
    report = _report(complete=False)
    report["failure_recovery_results"] = report["failure_recovery_results"][:-1]

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert readiness.missing_cases
    assert "mandatory evaluation cases are missing" in readiness.blockers


def test_failed_authorization_boundary_case_blocks_decision() -> None:
    report = _report(complete=False)
    report["identity_authorization_results"][0] = _result(
        report["identity_authorization_results"][0]["case_id"],
        "fail",
    )

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert readiness.failed_cases


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
def test_topology_invariants_block_decision(field: str, value: bool, blocker: str) -> None:
    report = _report(complete=False)
    report["topology"][field] = value

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert blocker in readiness.blockers


def test_plaintext_secret_leak_blocks_decision() -> None:
    report = _report(complete=False)
    report["secret_handling"]["plaintext_secret_leak_detected"] = True

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert "plaintext credential material leaked into retained evidence" in readiness.blockers


def test_latency_requires_repeatable_sample_count() -> None:
    report = _report(complete=False)
    report["latency"]["reconnect"]["count"] = 1

    readiness = assess_private_mcp_transport_evaluation(report)

    assert readiness.decision_ready is False
    assert "latency.reconnect has fewer than 5 retained samples" in readiness.blockers


def test_validator_rejects_inverted_latency_distribution() -> None:
    report = _report(complete=False)
    report["latency"]["private_overlay_warm"]["p95_ms"] = 1.0

    with pytest.raises(ValueError, match="p95_ms must be >= p50_ms"):
        validate_private_mcp_transport_evaluation_report(report)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_validator_rejects_non_finite_latency(value: float) -> None:
    report = _report(complete=False)
    report["latency"]["direct_local"]["p50_ms"] = value

    with pytest.raises(ValueError, match="non-finite numeric evidence"):
        validate_private_mcp_transport_evaluation_report(report)


def test_schema_rejects_recommendation_without_decision_eligible() -> None:
    report = deepcopy(_report(complete=False))
    report["recommendation"] = "prefer_simpler_private_networking"

    with pytest.raises(ValueError, match="decision_eligible"):
        validate_private_mcp_transport_evaluation_report(report)
