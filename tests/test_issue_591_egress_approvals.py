from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    EgressCostClass,
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
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationGate,
    CanonicalEgressPolicy,
    EgressApprovalBridge,
    EgressApprovalExceptionPolicy,
    EgressGate,
)
from ai_multi_agent_platform.testing import FakeAuthorizationProvider


def _actor() -> ActorIdentity:
    return ActorIdentity(new_id("user"), ActorType.HUMAN)


def _profile(*, revision: int = 1, cost: EgressCostClass = EgressCostClass.PAID_EXTERNAL) -> EgressProfile:
    return EgressProfile(
        profile_id="profile:model:paid-591",
        revision=revision,
        target_kind=EgressTargetKind.MODEL_PROVIDER,
        target_id="model-paid-591",
        posture=EgressTargetPosture.EXTERNAL,
        network_egress_required=True,
        cost_class=cost,
        policy_source="deployment-policy",
        source_revision=f"deployment-policy-{revision}",
        trust=EgressProfileTrust.CONFIGURED,
    )


def _request(
    *,
    classification: DataClassification = DataClassification.CONFIDENTIAL,
    payload: str = "protected-a",
    profile_revision: int = 1,
) -> EgressRequest:
    return EgressRequest(
        request_id="egress-approval-591",
        target=EgressTarget(
            kind=EgressTargetKind.MODEL_PROVIDER,
            target_id="model-paid-591",
            profile=_profile(revision=profile_revision),
        ),
        context=OperationContext(
            correlation_id="corr-egress-approval-591",
            owner_type="user",
            owner_id="request-owner-591",
            project_id=new_id("project"),
        ),
        classification=classification,
        resource_type="model_request",
        payload_digest=digest_egress_payload(payload),
        task_id=new_id("task"),
        run_id=new_id("run"),
    )


def _gate(
    authorization: AuthorizationGate,
    *,
    reasons: frozenset[EgressReasonCode],
) -> EgressGate:
    bridge = EgressApprovalBridge(
        authorization,
        EgressApprovalExceptionPolicy(approvable_reason_codes=reasons),
    )
    return EgressGate(CanonicalEgressPolicy(), approval_resolver=bridge)


def test_paid_external_exception_requires_then_accepts_exact_approved_action() -> None:
    actor = _actor()
    authorization = AuthorizationGate(FakeAuthorizationProvider())
    gate = _gate(
        authorization,
        reasons=frozenset({EgressReasonCode.PAID_EXTERNAL_DENIED}),
    )
    request = _request()

    with pytest.raises(ContractError) as required:
        asyncio.run(gate.enforce(request, actor=actor))

    assert required.value.details["outcome"] == EgressOutcome.REQUIRE_APPROVAL.value
    assert required.value.details["reason_code"] == EgressReasonCode.APPROVAL_REQUIRED.value
    approval_id = required.value.details["approval_ref"]
    assert isinstance(approval_id, str)

    asyncio.run(
        authorization.decide_approval(
            approval_id,
            approver=_actor(),
            approve=True,
            operation=request.context,
        )
    )
    decision = asyncio.run(gate.enforce(request, actor=actor, approval_id=approval_id))

    assert decision.outcome is EgressOutcome.ALLOW
    assert decision.approval_ref == approval_id
    assert decision.audit_metadata["approval_exception_for_reason"] == "paid_external_denied"
    assert decision.audit_metadata["approval_reuse_semantics"] == "exact_digest_until_expiry"


def test_changed_payload_invalidates_previously_approved_egress_exception() -> None:
    actor = _actor()
    authorization = AuthorizationGate(FakeAuthorizationProvider())
    gate = _gate(
        authorization,
        reasons=frozenset({EgressReasonCode.PAID_EXTERNAL_DENIED}),
    )
    original = _request(payload="protected-a")

    with pytest.raises(ContractError) as required:
        asyncio.run(gate.enforce(original, actor=actor))
    approval_id = required.value.details["approval_ref"]
    assert isinstance(approval_id, str)
    asyncio.run(
        authorization.decide_approval(
            approval_id,
            approver=_actor(),
            approve=True,
            operation=original.context,
        )
    )

    changed = replace(original, payload_digest=digest_egress_payload("protected-b"))
    with pytest.raises(ContractError) as stale:
        asyncio.run(gate.enforce(changed, actor=actor, approval_id=approval_id))

    assert stale.value.details["outcome"] == EgressOutcome.REQUIRE_APPROVAL.value
    assert stale.value.details["approval_ref"] != approval_id


def test_changed_profile_revision_invalidates_previously_approved_exception() -> None:
    actor = _actor()
    authorization = AuthorizationGate(FakeAuthorizationProvider())
    gate = _gate(
        authorization,
        reasons=frozenset({EgressReasonCode.PAID_EXTERNAL_DENIED}),
    )
    original = _request(profile_revision=1)

    with pytest.raises(ContractError) as required:
        asyncio.run(gate.enforce(original, actor=actor))
    approval_id = required.value.details["approval_ref"]
    assert isinstance(approval_id, str)
    asyncio.run(
        authorization.decide_approval(
            approval_id,
            approver=_actor(),
            approve=True,
            operation=original.context,
        )
    )

    changed_target = replace(original.target, profile=_profile(revision=2))
    changed = replace(original, target=changed_target)
    with pytest.raises(ContractError) as stale:
        asyncio.run(gate.enforce(changed, actor=actor, approval_id=approval_id))

    assert stale.value.details["outcome"] == EgressOutcome.REQUIRE_APPROVAL.value
    assert stale.value.details["approval_ref"] != approval_id


def test_secret_egress_remains_non_overridable_even_if_reason_is_declared_approvable() -> None:
    actor = _actor()
    authorization = AuthorizationGate(FakeAuthorizationProvider())
    gate = _gate(
        authorization,
        reasons=frozenset({EgressReasonCode.SENSITIVE_EXTERNAL_DENIED}),
    )
    request = _request(classification=DataClassification.SECRET)

    with pytest.raises(ContractError) as denied:
        asyncio.run(gate.enforce(request, actor=actor))

    assert denied.value.details["outcome"] == EgressOutcome.DENY.value
    assert denied.value.details["reason_code"] == "paid_external_denied"
    assert "approval_ref" not in denied.value.details
    assert authorization.approvals.all() == ()
