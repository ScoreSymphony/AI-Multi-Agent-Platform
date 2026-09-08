from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import (
    ClassificationDowngradeError,
    ContractError,
    DataClassification,
    EgressCostClass,
    EgressDecision,
    EgressOutcome,
    EgressProfile,
    EgressProfileTrust,
    EgressReasonCode,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    OperationContext,
    digest_egress_payload,
    require_monotonic_classification,
    strongest_classification,
)
from ai_multi_agent_platform.security.egress import (
    CanonicalEgressPolicy,
    EgressGate,
    InMemoryEgressAuditSink,
)


def _profile(
    *,
    target_id: str = "provider:test",
    posture: EgressTargetPosture = EgressTargetPosture.EXTERNAL,
    cost_class: EgressCostClass = EgressCostClass.FREE_EXTERNAL,
    trust: EgressProfileTrust = EgressProfileTrust.CONFIGURED,
    allowed: tuple[DataClassification, ...] = (),
    denied: tuple[DataClassification, ...] = (),
    metadata: dict[str, object] | None = None,
) -> EgressProfile:
    return EgressProfile(
        profile_id=f"egress-profile:{target_id}",
        revision=1,
        target_kind=EgressTargetKind.MODEL_PROVIDER,
        target_id=target_id,
        posture=posture,
        allowed_classifications=allowed,
        denied_classifications=denied,
        network_egress_required=posture is EgressTargetPosture.EXTERNAL,
        cost_class=cost_class,
        credential_required=False,
        policy_source="operator-config",
        source_revision="test-v1",
        trust=trust,
        metadata=metadata or {},
    )


def _request(
    classification: DataClassification | None,
    *,
    posture: EgressTargetPosture = EgressTargetPosture.EXTERNAL,
    target_id: str = "provider:test",
    allowed: tuple[DataClassification, ...] = (),
    policy_metadata: dict[str, object] | None = None,
    profile: EgressProfile | None = None,
) -> EgressRequest:
    return EgressRequest(
        request_id="egress-test-1",
        target=EgressTarget(
            kind=EgressTargetKind.MODEL_PROVIDER,
            target_id=target_id,
            posture=(EgressTargetPosture.UNKNOWN if profile is not None else posture),
            allowed_classifications=(() if profile is not None else allowed),
            policy_metadata=policy_metadata or {},
            profile=profile,
        ),
        context=OperationContext(correlation_id="corr-591"),
        classification=classification,
        resource_type="model_request",
        payload_digest=digest_egress_payload({"messages": ["sensitive-value"]}),
    )


def test_merge_uses_strongest_classification() -> None:
    assert (
        strongest_classification(
            DataClassification.PUBLIC,
            DataClassification.CONFIDENTIAL,
            DataClassification.INTERNAL,
        )
        is DataClassification.CONFIDENTIAL
    )
    assert (
        strongest_classification(
            DataClassification.SECRET_REFERENCE,
            DataClassification.SECRET,
        )
        is DataClassification.SECRET
    )


def test_silent_classification_downgrade_is_rejected() -> None:
    with pytest.raises(ClassificationDowngradeError):
        require_monotonic_classification(
            DataClassification.SECRET,
            DataClassification.PUBLIC,
        )


def test_explicit_redaction_policy_can_downgrade_derivative() -> None:
    assert (
        require_monotonic_classification(
            DataClassification.SECRET,
            DataClassification.INTERNAL,
            redacted=True,
            policy_decision_id="egress-decision-1",
        )
        is DataClassification.INTERNAL
    )


def test_unknown_target_posture_fails_closed() -> None:
    decision = asyncio.run(
        CanonicalEgressPolicy().evaluate(
            _request(DataClassification.PUBLIC, posture=EgressTargetPosture.UNKNOWN)
        )
    )
    assert decision.outcome is EgressOutcome.UNKNOWN_BLOCKED
    assert decision.reason_code is EgressReasonCode.UNKNOWN_TARGET_POSTURE


def test_absent_classification_fails_closed() -> None:
    decision = asyncio.run(CanonicalEgressPolicy().evaluate(_request(None)))
    assert decision.outcome is EgressOutcome.DENY
    assert decision.reason_code is EgressReasonCode.CLASSIFICATION_REQUIRED


@pytest.mark.parametrize(
    "classification",
    [
        DataClassification.SECRET,
        DataClassification.SECRET_REFERENCE,
        DataClassification.REGULATED,
    ],
)
def test_sensitive_data_is_denied_to_external_target_by_default(
    classification: DataClassification,
) -> None:
    decision = asyncio.run(CanonicalEgressPolicy().evaluate(_request(classification)))
    assert decision.outcome is EgressOutcome.DENY
    assert decision.reason_code is EgressReasonCode.SENSITIVE_EXTERNAL_DENIED


def test_public_data_is_allowed_to_known_external_target() -> None:
    decision = asyncio.run(CanonicalEgressPolicy().evaluate(_request(DataClassification.PUBLIC)))
    assert decision.outcome is EgressOutcome.ALLOW
    assert decision.reason_code is EgressReasonCode.ALLOWED


def test_sensitive_external_egress_requires_exact_trusted_target_opt_in() -> None:
    decision = asyncio.run(
        CanonicalEgressPolicy().evaluate(
            _request(
                DataClassification.SECRET,
                allowed=(DataClassification.SECRET,),
                policy_metadata={"allow_sensitive_external": True},
            )
        )
    )
    assert decision.outcome is EgressOutcome.ALLOW


def test_versioned_profile_preserves_target_identity_and_provenance() -> None:
    profile = _profile()
    target = EgressTarget(
        kind=EgressTargetKind.MODEL_PROVIDER,
        target_id="provider:test",
        profile=profile,
    )

    assert target.effective_posture is EgressTargetPosture.EXTERNAL
    assert target.profile_ref == "egress-profile:provider:test@1"
    assert profile.policy_source == "operator-config"
    assert profile.source_revision == "test-v1"


def test_unverified_external_profile_fails_closed() -> None:
    decision = asyncio.run(
        CanonicalEgressPolicy().evaluate(
            _request(
                DataClassification.PUBLIC,
                profile=_profile(trust=EgressProfileTrust.UNVERIFIED),
            )
        )
    )

    assert decision.outcome is EgressOutcome.UNKNOWN_BLOCKED
    assert decision.reason_code is EgressReasonCode.UNVERIFIED_PROFILE


def test_paid_external_profile_is_denied_by_baseline() -> None:
    decision = asyncio.run(
        CanonicalEgressPolicy().evaluate(
            _request(
                DataClassification.PUBLIC,
                profile=_profile(cost_class=EgressCostClass.PAID_EXTERNAL),
            )
        )
    )

    assert decision.outcome is EgressOutcome.DENY
    assert decision.reason_code is EgressReasonCode.PAID_EXTERNAL_DENIED


def test_paid_external_profile_can_be_enabled_by_deployment_policy() -> None:
    decision = asyncio.run(
        CanonicalEgressPolicy(allow_paid_external=True).evaluate(
            _request(
                DataClassification.PUBLIC,
                profile=_profile(cost_class=EgressCostClass.PAID_EXTERNAL),
            )
        )
    )

    assert decision.outcome is EgressOutcome.ALLOW


def test_unknown_external_cost_is_explicitly_blocked() -> None:
    decision = asyncio.run(
        CanonicalEgressPolicy().evaluate(
            _request(
                DataClassification.PUBLIC,
                profile=_profile(cost_class=EgressCostClass.UNKNOWN),
            )
        )
    )

    assert decision.outcome is EgressOutcome.UNKNOWN_BLOCKED
    assert decision.reason_code is EgressReasonCode.UNKNOWN_COST_BLOCKED


def test_profile_denied_classification_takes_precedence() -> None:
    decision = asyncio.run(
        CanonicalEgressPolicy().evaluate(
            _request(
                DataClassification.CONFIDENTIAL,
                profile=_profile(denied=(DataClassification.CONFIDENTIAL,)),
            )
        )
    )

    assert decision.outcome is EgressOutcome.DENY
    assert decision.reason_code is EgressReasonCode.TARGET_POLICY_DENIED


def test_gate_emits_stable_value_free_audit_events() -> None:
    sink = InMemoryEgressAuditSink()
    gate = EgressGate(CanonicalEgressPolicy(), audit_sink=sink)
    request = _request(DataClassification.PUBLIC)

    asyncio.run(gate.enforce(request))

    assert [event.event_type.value for event in sink.events] == [
        "EgressEvaluated",
        "EgressAllowed",
    ]
    assert all(event.payload_digest == request.payload_digest for event in sink.events)
    assert "sensitive-value" not in repr(sink.events)


def test_profile_audit_retains_safe_revision_and_cost_only() -> None:
    sink = InMemoryEgressAuditSink()
    gate = EgressGate(CanonicalEgressPolicy(), audit_sink=sink)
    request = _request(DataClassification.PUBLIC, profile=_profile())

    asyncio.run(gate.enforce(request))

    assert sink.events[-1].profile_ref == "egress-profile:provider:test@1"
    assert sink.events[-1].cost_class is EgressCostClass.FREE_EXTERNAL
    assert "sensitive-value" not in repr(sink.events)


def test_gate_denial_contains_no_raw_payload() -> None:
    gate = EgressGate()
    request = _request(DataClassification.SECRET)

    with pytest.raises(ContractError) as captured:
        asyncio.run(gate.enforce(request))

    assert "sensitive-value" not in str(captured.value)
    assert captured.value.details["payload_digest"] == request.payload_digest


class _RedactingPolicy:
    async def evaluate(self, request: EgressRequest) -> EgressDecision:
        return EgressDecision(
            request_id=request.request_id,
            outcome=EgressOutcome.ALLOW,
            target_kind=request.target.kind,
            target_id=request.target.target_id,
            effective_classification=DataClassification.INTERNAL,
            reason_code=EgressReasonCode.ALLOWED,
            policy_version="test-redaction/v1",
            required_redactions=("secret", "nested.token"),
        )


def test_gate_applies_required_redaction_before_downgrade() -> None:
    request = replace(_request(DataClassification.SECRET), resource_type="connector_payload")
    payload = {"safe": "ok", "secret": "remove", "nested": {"token": "remove", "keep": 1}}

    transformed, decision = asyncio.run(
        EgressGate(_RedactingPolicy()).enforce_json(request, payload)
    )

    assert decision.effective_classification is DataClassification.INTERNAL
    assert transformed == {"safe": "ok", "nested": {"keep": 1}}
