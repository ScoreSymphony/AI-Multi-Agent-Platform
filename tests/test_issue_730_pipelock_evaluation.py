from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.adapters.pipelock import (
    PIPELOCK_PINNED_REVISION,
    PipelockAdapterMode,
    PipelockDirective,
    PipelockMappingError,
    PipelockUnavailableAction,
    ReceiptVerification,
    normalize_pipelock_receipt,
    project_egress_decision,
    resolve_pipelock_unavailable,
)
from ai_multi_agent_platform.contracts import (
    DataClassification,
    EgressDecision,
    EgressOutcome,
    EgressReasonCode,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    OperationContext,
    digest_egress_payload,
)


def _request() -> EgressRequest:
    return EgressRequest(
        request_id="egress-730",
        target=EgressTarget(
            kind=EgressTargetKind.CAPABILITY,
            target_id="capability:external-http",
            posture=EgressTargetPosture.EXTERNAL,
        ),
        context=OperationContext(
            correlation_id="corr-730",
            owner_type="agent",
            owner_id="agent-owner-730",
        ),
        classification=DataClassification.PUBLIC,
        resource_type="tool_request",
        payload_digest=digest_egress_payload({"safe": "fixture"}),
        task_id="task-730",
        run_id="run-730",
        capability_id="capability-730",
        policy_descriptors={"agent_id": "agent-730"},
    )


def _decision(
    outcome: EgressOutcome = EgressOutcome.ALLOW,
    *,
    policy_version: str = "egress-policy/v1",
) -> EgressDecision:
    reason = {
        EgressOutcome.ALLOW: EgressReasonCode.ALLOWED,
        EgressOutcome.DENY: EgressReasonCode.TARGET_POLICY_DENIED,
        EgressOutcome.REQUIRE_APPROVAL: EgressReasonCode.APPROVAL_REQUIRED,
        EgressOutcome.LOCAL_ONLY: EgressReasonCode.LOCAL_ONLY_REQUIRED,
        EgressOutcome.UNKNOWN_BLOCKED: EgressReasonCode.UNKNOWN_TARGET_POSTURE,
    }[outcome]
    return EgressDecision(
        request_id="egress-730",
        outcome=outcome,
        target_kind=EgressTargetKind.CAPABILITY,
        target_id="capability:external-http",
        effective_classification=DataClassification.PUBLIC,
        reason_code=reason,
        policy_version=policy_version,
        approval_ref="approval-730" if outcome is EgressOutcome.REQUIRE_APPROVAL else None,
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PipelockAdapterMode.DISABLED, PipelockDirective.SKIP),
        (PipelockAdapterMode.AUDIT_ONLY, PipelockDirective.AUDIT),
        (PipelockAdapterMode.ENFORCE, PipelockDirective.MEDIATED_ALLOW),
    ],
)
def test_only_canonical_allow_can_enter_pipelock(
    mode: PipelockAdapterMode,
    expected: PipelockDirective,
) -> None:
    projection = project_egress_decision(_request(), _decision(), mode=mode)

    assert projection.directive is expected
    assert projection.requires_pipelock is (mode is not PipelockAdapterMode.DISABLED)
    assert projection.platform_policy_version == "egress-policy/v1"
    assert projection.upstream_revision == PIPELOCK_PINNED_REVISION


@pytest.mark.parametrize(
    "outcome",
    [
        EgressOutcome.DENY,
        EgressOutcome.REQUIRE_APPROVAL,
        EgressOutcome.LOCAL_ONLY,
        EgressOutcome.UNKNOWN_BLOCKED,
    ],
)
@pytest.mark.parametrize("mode", list(PipelockAdapterMode))
def test_canonical_non_allow_never_degrades_to_upstream_allow(
    outcome: EgressOutcome,
    mode: PipelockAdapterMode,
) -> None:
    projection = project_egress_decision(_request(), _decision(outcome), mode=mode)

    assert projection.directive is PipelockDirective.BLOCK
    assert not projection.requires_pipelock
    if outcome is EgressOutcome.REQUIRE_APPROVAL:
        assert projection.approval_ref == "approval-730"


def test_projection_rejects_decision_for_a_different_target() -> None:
    decision = replace(_decision(), target_id="capability:other")

    with pytest.raises(ValueError, match="target does not match request"):
        project_egress_decision(_request(), decision, mode=PipelockAdapterMode.AUDIT_ONLY)


def test_decision_digest_binds_payload_and_policy_revision() -> None:
    first = project_egress_decision(
        _request(),
        _decision(policy_version="egress-policy/v1"),
        mode=PipelockAdapterMode.AUDIT_ONLY,
    )
    second = project_egress_decision(
        _request(),
        _decision(policy_version="egress-policy/v2"),
        mode=PipelockAdapterMode.AUDIT_ONLY,
    )

    assert len(first.decision_digest) == 64
    assert first.decision_digest != second.decision_digest


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PipelockAdapterMode.DISABLED, PipelockUnavailableAction.NOT_REQUIRED),
        (
            PipelockAdapterMode.AUDIT_ONLY,
            PipelockUnavailableAction.CONTINUE_WITH_DEGRADED_EVIDENCE,
        ),
        (PipelockAdapterMode.ENFORCE, PipelockUnavailableAction.FAIL_CLOSED),
    ],
)
def test_pipelock_outage_behavior_is_explicit_for_canonical_allow(
    mode: PipelockAdapterMode,
    expected: PipelockUnavailableAction,
) -> None:
    projection = project_egress_decision(_request(), _decision(), mode=mode)

    assert resolve_pipelock_unavailable(projection) is expected


@pytest.mark.parametrize("mode", list(PipelockAdapterMode))
def test_pipelock_outage_is_irrelevant_when_canonical_policy_already_blocks(
    mode: PipelockAdapterMode,
) -> None:
    projection = project_egress_decision(
        _request(),
        _decision(EgressOutcome.DENY),
        mode=mode,
    )

    assert projection.directive is PipelockDirective.BLOCK
    assert resolve_pipelock_unavailable(projection) is PipelockUnavailableAction.NOT_REQUIRED


def test_receipt_normalization_preserves_canonical_refs_without_raw_destination() -> None:
    projection = project_egress_decision(
        _request(),
        _decision(),
        mode=PipelockAdapterMode.AUDIT_ONLY,
    )
    raw_target = "https://example.test/upload?token=do-not-persist"

    evidence = normalize_pipelock_receipt(
        projection,
        {
            "action_id": "0199-action-730",
            "verdict": "allow",
            "transport": "forward",
            "method": "POST",
            "target": raw_target,
            "layer": "dlp",
            "pattern": "sensitive raw rule detail",
            "policy_hash": "pipelock-config-hash",
            "verified": True,
        },
    )

    assert evidence.request_id == "egress-730"
    assert evidence.correlation_id == "corr-730"
    assert evidence.task_id == "task-730"
    assert evidence.run_id == "run-730"
    assert evidence.agent_id == "agent-730"
    assert evidence.capability_id == "capability-730"
    assert evidence.platform_policy_version == "egress-policy/v1"
    assert evidence.pipelock_policy_hash == "pipelock-config-hash"
    assert evidence.destination_digest is not None
    assert len(evidence.destination_digest) == 64
    assert raw_target not in repr(evidence)
    assert "sensitive raw rule detail" not in repr(evidence)
    assert evidence.verification is ReceiptVerification.NOT_CHECKED


def test_receipt_verification_state_must_come_from_external_verifier() -> None:
    projection = project_egress_decision(
        _request(),
        _decision(),
        mode=PipelockAdapterMode.ENFORCE,
    )
    receipt = {
        "action_id": "0199-action-verified",
        "verdict": "allow",
        "transport": "mcp_stdio",
        "verified": True,
    }

    evidence = normalize_pipelock_receipt(
        projection,
        receipt,
        verification=ReceiptVerification.FAILED,
    )

    assert evidence.verification is ReceiptVerification.FAILED


def test_malformed_receipt_fails_closed_in_evidence_normalization() -> None:
    projection = project_egress_decision(
        _request(),
        _decision(),
        mode=PipelockAdapterMode.AUDIT_ONLY,
    )

    with pytest.raises(PipelockMappingError, match="action_id"):
        normalize_pipelock_receipt(projection, {"verdict": "allow", "transport": "fetch"})
