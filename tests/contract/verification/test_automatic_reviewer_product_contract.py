from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.agents import (
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    AgentTeamRevisionRef,
)
from ai_multi_agent_platform.agents.control_plane import _agent_run_resource
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    CompletionState,
    VerificationCompletionAuthority,
    VerificationFinding,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from ai_multi_agent_platform.verification.control_plane import (
    _requirement_resource,
    _verification_resource,
)


def _agent_verifier(agent_id: str, revision: int = 1) -> VerifierIdentity:
    return VerifierIdentity(
        verifier_ref=f"agent:{agent_id}@{revision}",
        kind=VerifierKind.AGENT,
        agent_id=agent_id,
        agent_revision=revision,
        model_config_id="reviewer-model",
        provider_id="local-reviewer-provider",
        read_only=True,
    )


def test_artifact_repair_budget_and_client_correlation_are_canonical() -> None:
    verification = VerificationService()
    completion = VerificationCompletionAuthority(verification)
    policy = verification.register_policy(
        VerificationPolicy(
            name="Artifact repair product contract",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            max_repair_attempts=1,
        )
    )
    task_id = new_id("task")
    producer_run_id = new_id("run")
    repair_run_id = new_id("run")
    artifact_v1 = new_id("artifact")
    artifact_v2 = new_id("artifact")
    reviewer_agent_id = new_id("agent")
    reviewer_team_id = new_id("team")

    subject_v1 = VerificationSubject(
        subject_type="artifact",
        subject_id=artifact_v1,
        revision=new_id("file"),
        digest="sha256:artifact-v1",
    )
    first = completion.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject_v1,
        correlation_id="issue-759-product-contract",
        run_id=producer_run_id,
        artifact_ids=(artifact_v1,),
    )
    first_result = verification.submit_result(
        VerificationResult(
            verification_id=first.verification_id,
            verifier=_agent_verifier(reviewer_agent_id),
            outcome=VerificationOutcome.NEEDS_CHANGES,
            subject=subject_v1,
            findings=(
                VerificationFinding(
                    code="artifact-needs-repair",
                    message="Artifact requires one repair iteration.",
                    severity="error",
                ),
            ),
        )
    )

    subject_v2 = VerificationSubject(
        subject_type="artifact",
        subject_id=artifact_v2,
        revision=new_id("file"),
        digest="sha256:artifact-v2",
    )
    second = completion.request_reverification_after_repair(
        first.verification_id,
        new_subject=subject_v2,
        correlation_id="issue-759-product-contract",
        run_id=repair_run_id,
        artifact_ids=(artifact_v2,),
        causation_id=first.verification_id,
    )
    second_result = verification.submit_result(
        VerificationResult(
            verification_id=second.verification_id,
            verifier=_agent_verifier(reviewer_agent_id),
            outcome=VerificationOutcome.NEEDS_CHANGES,
            subject=subject_v2,
            findings=(
                VerificationFinding(
                    code="artifact-still-incomplete",
                    message="Artifact remains incomplete after the bounded repair.",
                    severity="error",
                ),
            ),
        )
    )

    decision = completion.assess_task_completion(task_id)
    assert decision.state is CompletionState.REJECTED
    assert decision.reason == "verification repair limit exhausted"
    assert decision.repair_attempts_remaining == 0
    assert decision.blocking_verification_ids == (second.verification_id,)

    # Historical Verification remains exact to Artifact v1; it cannot certify v2.
    assert first.subject == first_result.subject == subject_v1
    assert second.subject == second_result.subject == subject_v2
    assert first.subject != second.subject
    assert second.repair_attempt == 1

    reviewer_run = AgentRunRecord(
        agent_run_id=new_id("agent_run"),
        run_id=repair_run_id,
        task_id=task_id,
        agent=AgentRevisionRef(reviewer_agent_id, 1),
        team=AgentTeamRevisionRef(reviewer_team_id, 3),
        status=AgentRunStatus.SUCCEEDED,
        selected_model_config_id="reviewer-model",
        selected_provider_id="local-reviewer-provider",
        verification_context={
            "schema": "verification-reviewer-agent-v1",
            "verification_id": second.verification_id,
            "task_id": task_id,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "stage_id": second.stage_id,
            "repair_attempt": second.repair_attempt,
            "subject": {
                "type": subject_v2.subject_type,
                "id": subject_v2.subject_id,
                "revision": subject_v2.revision,
                "digest": subject_v2.digest,
            },
        },
    )

    verification_view = _verification_resource(second, second_result)
    agent_run_view = _agent_run_resource(reviewer_run)
    requirement_view = _requirement_resource(completion, task_id)

    assert verification_view["repair_attempt"] == 1
    assert verification_view["subject"] == {
        "type": "artifact",
        "id": artifact_v2,
        "revision": subject_v2.revision,
        "digest": subject_v2.digest,
    }
    result_view = verification_view["verification_result"]
    assert isinstance(result_view, Mapping)
    assert result_view["outcome"] == "needs_changes"
    assert result_view["findings"] == [
        {
            "code": "artifact-still-incomplete",
            "message": "Artifact remains incomplete after the bounded repair.",
            "severity": "error",
            "location_ref": None,
        }
    ]

    run_context = agent_run_view["verification_context"]
    assert isinstance(run_context, Mapping)
    assert run_context["verification_id"] == verification_view["id"]
    assert run_context["subject"] == verification_view["subject"]
    assert agent_run_view["agent_run_id"] == reviewer_run.agent_run_id
    assert agent_run_view["agent"] == {
        "agent_id": reviewer_agent_id,
        "revision": 1,
    }
    assert agent_run_view["team"] == {
        "team_id": reviewer_team_id,
        "revision": 3,
    }

    assert requirement_view["subject"] == verification_view["subject"]
    completion_view = requirement_view["completion"]
    assert isinstance(completion_view, Mapping)
    assert completion_view == {
        "state": "rejected",
        "reason": "verification repair limit exhausted",
        "blocking_verification_ids": [second.verification_id],
        "repair_attempts_remaining": 0,
    }


def test_routing_failure_is_explicit_instead_of_silent_waiting() -> None:
    error = ContractError(
        ErrorCode.INVALID_CONFIGURATION,
        "scoped reviewer discovery must resolve exactly one enabled candidate",
        details={
            "policy_id": new_id("verification_policy"),
            "policy_version": 1,
            "stage_id": "review",
            "match_count": 2,
            "candidate_agent_count": 2,
            "candidate_team_count": 0,
            "reviewer_role": "reviewer",
            "required_capability_ids": [],
        },
    )

    assert error.code is ErrorCode.INVALID_CONFIGURATION
    assert error.message == "scoped reviewer discovery must resolve exactly one enabled candidate"
    assert error.details["match_count"] == 2
    assert error.details["stage_id"] == "review"
