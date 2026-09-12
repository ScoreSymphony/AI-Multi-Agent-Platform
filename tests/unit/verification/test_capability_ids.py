from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    VerificationRequest,
    VerificationScope,
    VerificationSubject,
    VerifierKind,
)


def test_verification_preserves_semantic_capability_ids() -> None:
    capability_id = "tool.workspace.write_artifact"
    result_id = new_id("result")
    task_id = new_id("task")

    scope = VerificationScope(capability_ids=(capability_id,))
    request = VerificationRequest(
        task_id=task_id,
        policy_id=new_id("verification_policy"),
        policy_version=1,
        stage_id="human-review",
        subject=VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision="1",
            digest="sha256:semantic-capability-id",
        ),
        requested_verifier_kind=VerifierKind.HUMAN,
        correlation_id=task_id,
        result_id=result_id,
        capability_ids=(capability_id,),
    )

    assert scope.capability_ids == (capability_id,)
    assert request.capability_ids == (capability_id,)
