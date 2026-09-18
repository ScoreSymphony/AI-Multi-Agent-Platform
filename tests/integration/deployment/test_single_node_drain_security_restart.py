from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import AuthorizationOutcome, OperationContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import ApprovalStatus, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
)
from ai_multi_agent_platform.verification import (
    VerificationPolicy,
    VerificationRequestStatus,
    VerificationStage,
    VerifierKind,
)


def test_pending_approval_and_authorization_binding_survive_drain_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "approval"
        first = build_single_node_deployment(SingleNodeConfig(data_dir=root, secure_cookie=False))
        agent_id = new_id("agent")
        first.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=agent_id,
                actor_types=frozenset({ActorType.AGENT}),
                approval_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.RUN}),
            )
        )
        action = ProposedAction(
            AuthorizationContext(
                actor=ActorIdentity(agent_id, ActorType.AGENT),
                action=AuthorizationAction.EXECUTE,
                resource_type=ResourceType.RUN,
                resource_id=new_id("run"),
                operation=OperationContext(
                    correlation_id="drain-approval",
                    owner_type="agent",
                    owner_id=agent_id,
                ),
            ),
            payload={"operation": "sensitive"},
        )
        decision = await first.approval_gate.decide(action)
        assert decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
        approval_id = decision.constraints["approval_id"]
        assert isinstance(approval_id, str)
        pending = first.approval_gate.approvals.get(approval_id)
        assert pending.status is ApprovalStatus.PENDING

        await first.drain.begin(reason="approval_pending_shutdown")

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        restored = restarted.approval_gate.approvals.get(approval_id)
        assert restored.approval_id == pending.approval_id
        assert restored.status is ApprovalStatus.PENDING
        assert restored.requester_ref == pending.requester_ref
        assert restored.resource_id == pending.resource_id
        assert restored.action == pending.action
        assert restored.requested_action_digest == action.digest
        assert restored.policy_id == pending.policy_id

        repeated = await restarted.approval_gate.decide(action)
        assert repeated.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
        assert repeated.constraints["approval_id"] == approval_id

    asyncio.run(scenario())


def test_pending_verification_exact_subject_binding_survives_drain_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "verification"
        first = build_single_node_deployment(SingleNodeConfig(data_dir=root, secure_cookie=False))
        first.bootstrap_admin("drain-verification", "correct horse battery staple")
        smoke = await first.run_reference_smoke()
        run = await first.kernel.get_run(smoke.task_id, smoke.run_id)
        assert len(run.result_ids) == 1
        result_id = run.result_ids[0]

        stage_id = "drain-human-review"
        policy = first.verification.register_policy(
            VerificationPolicy(
                name="Drain restart exact verification binding",
                stages=(
                    VerificationStage(
                        stage_id=stage_id,
                        verifier_kind=VerifierKind.HUMAN,
                    ),
                ),
            )
        )
        request = await first.verification_runtime.request_verification(
            task_id=smoke.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id=stage_id,
            subject_type="result",
            subject_id=result_id,
            correlation_id="drain-verification",
        )
        assert request.status is VerificationRequestStatus.PENDING

        await first.drain.begin(reason="verification_pending_shutdown")

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        restored = restarted.verification.get_request(request.verification_id)
        assert restored.verification_id == request.verification_id
        assert restored.status is VerificationRequestStatus.PENDING
        assert restored.task_id == smoke.task_id
        assert restored.policy_id == policy.policy_id
        assert restored.policy_version == policy.version
        assert restored.stage_id == stage_id
        assert restored.subject == request.subject
        assert restored.result_id == result_id
        assert restarted.verification.get_policy(policy.policy_id, policy.version) == policy

    asyncio.run(scenario())
