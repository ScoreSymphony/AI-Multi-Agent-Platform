from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    CompletionState,
    TaskVerificationRequirement,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)


class _FaultInjectingCompletionAuthority(VerificationCompletionAuthority):
    def __init__(self, verification: VerificationService) -> None:
        super().__init__(verification)
        self.fail_invalidation = False

    def invalidate_task_subject(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> TaskVerificationRequirement | None:
        if self.fail_invalidation:
            raise RuntimeError("simulated crash before verification invalidation")
        return super().invalidate_task_subject(task_id, now=now)


@pytest.mark.parametrize("attachment", ["result", "artifact"])
def test_output_change_is_not_committed_if_verification_invalidation_fails(
    attachment: str,
) -> None:
    async def scenario() -> None:
        verification = VerificationService()
        completion = _FaultInjectingCompletionAuthority(verification)
        policy = verification.register_policy(
            VerificationPolicy(
                name="crash-safe-output-invalidation",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
            )
        )
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
            completion_authority=completion,
        )
        task = await kernel.create_task(
            idempotency_key=f"crash-safe:{attachment}:create",
            title="Crash-safe verification invalidation",
            objective="Never commit changed output while an old verification binding remains accepted",
            owner_type="user",
            owner_id="issue-86",
        )
        old_subject = VerificationSubject(
            subject_type="result",
            subject_id=new_id("result"),
            revision="1",
            digest="sha256:old-output",
        )
        request = completion.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=old_subject,
            result_id=old_subject.subject_id,
            correlation_id=task.task_id,
        )
        verification.record_human_review(
            request.verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.PASS,
        )
        assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED

        completion.fail_invalidation = True
        with pytest.raises(RuntimeError, match="simulated crash"):
            if attachment == "result":
                await kernel.attach_result(
                    idempotency_key="crash-safe:result:attach",
                    task_id=task.task_id,
                    result_id=new_id("result"),
                )
            else:
                await kernel.attach_artifact(
                    idempotency_key="crash-safe:artifact:attach",
                    task_id=task.task_id,
                    artifact_id=new_id("artifact"),
                )

        history = await kernel.history(task.task_id)
        assert all(event.event_type != f"{attachment}.attached" for event in history)
        requirement = completion.requirement_for(task.task_id)
        assert requirement is not None
        assert requirement.subject == old_subject
        assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED

    asyncio.run(scenario())
