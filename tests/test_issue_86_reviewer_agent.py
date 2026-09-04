from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRunStatus,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ExecutionStatus
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import (
    InMemoryKernelRepository,
    PlatformKernel,
    TaskStatus,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    CompletionState,
    ProducerIdentity,
    ReviewerIndependence,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.reviewer_agent import ReviewerAgentRuntime


def _profile(name: str = "Reviewer") -> AgentProfile:
    return AgentProfile(
        name=name,
        role="reviewer",
        instructions=AgentInstructions(
            role=InstructionSource(content="Review the exact bound result without modifying it."),
        ),
    )


def _agents() -> tuple[AgentService, AgentRuntime, str]:
    service = AgentService(InMemoryAgentRepository())
    reviewer = service.create_agent(
        _profile(),
        owner_ref=OwnerRef(type="service", id="verification"),
    )
    return service, AgentRuntime(service), reviewer.agent_id


def _policy(
    *,
    producer_must_differ: bool = False,
    read_only: bool = True,
) -> VerificationPolicy:
    return VerificationPolicy(
        name="reviewer-agent",
        stages=(VerificationStage("review", VerifierKind.AGENT),),
        independence=ReviewerIndependence(
            producer_agent_must_differ=producer_must_differ,
            agent_reviewer_must_be_read_only=read_only,
        ),
    )


def _subject() -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:reviewer-agent-result",
    )


def test_reviewer_agent_records_verification_without_task_lifecycle_authority() -> None:
    async def scenario() -> None:
        verification = VerificationService()
        completion = VerificationCompletionAuthority(verification)
        policy = verification.register_policy(_policy())
        repository = InMemoryKernelRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=repository,
            completion_authority=completion,
        )
        task = await kernel.create_task(
            idempotency_key="issue-86-reviewer-task",
            title="Review exact result",
            objective="Keep task blocked until reviewer Agent verification passes",
            owner_type="user",
            owner_id="review-owner",
        )
        await kernel.ready_task(
            idempotency_key="issue-86-reviewer-ready",
            task_id=task.task_id,
        )
        task_run = await kernel.start_task(
            idempotency_key="issue-86-reviewer-start",
            task_id=task.task_id,
        )
        exact = _subject()
        request = completion.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=exact,
            result_id=exact.subject_id,
            correlation_id="reviewer-agent-correlation",
        )
        lifecycle.complete(
            task_run.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"answer": "ready for review"},
        )
        await kernel.refresh_run(
            idempotency_key="issue-86-reviewer-run-success",
            task_id=task.task_id,
            run_id=task_run.run_id,
        )
        blocked = await kernel.get_task(task.task_id)
        assert blocked.status is TaskStatus.WAITING
        assert completion.assess_task_completion(task.task_id).state is CompletionState.WAITING

        agent_service, agent_runtime, reviewer_id = _agents()
        reviewer = ReviewerAgentRuntime(verification, agent_runtime)
        record = await reviewer.start_review(
            request.verification_id,
            run_id=new_id("run"),
            agent_id=reviewer_id,
        )
        assert record.status is AgentRunStatus.RUNNING
        assert record.verification_context["verification_id"] == request.verification_id
        assert record.verification_context["read_only"] is True
        assert record.verification_context["subject"] == {
            "type": exact.subject_type,
            "id": exact.subject_id,
            "revision": exact.revision,
            "digest": exact.digest,
        }

        result = reviewer.complete_review(
            record.agent_run_id,
            outcome=VerificationOutcome.PASS,
        )
        assert result.outcome is VerificationOutcome.PASS
        assert result.verifier.kind is VerifierKind.AGENT
        assert result.verifier.agent_id == reviewer_id
        assert result.verifier.read_only is True
        assert (
            agent_service.repository.get_agent_run(record.agent_run_id).status
            is AgentRunStatus.SUCCEEDED
        )
        assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED

        # Reviewer execution/result recording is not Task lifecycle authority.
        still_waiting = await kernel.get_task(task.task_id)
        assert still_waiting.status is TaskStatus.WAITING
        completed = await kernel.complete_task(
            idempotency_key="issue-86-reviewer-finalize",
            task_id=task.task_id,
        )
        assert completed.status is TaskStatus.SUCCEEDED

    asyncio.run(scenario())


def test_reviewer_agent_independence_is_enforced_before_agent_run_creation() -> None:
    async def scenario() -> None:
        agent_service, agent_runtime, reviewer_id = _agents()
        verification = VerificationService()
        policy = verification.register_policy(_policy(producer_must_differ=True))
        subject = _subject()
        request = verification.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id="same-agent-producer",
            producer=ProducerIdentity(
                actor_ref="agent:producer",
                agent_id=reviewer_id,
                agent_revision=1,
            ),
        )
        reviewer = ReviewerAgentRuntime(verification, agent_runtime)
        with pytest.raises(ContractError) as exc_info:
            await reviewer.start_review(
                request.verification_id,
                run_id=new_id("run"),
                agent_id=reviewer_id,
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert agent_service.repository.list_agent_runs() == ()

    asyncio.run(scenario())


def test_read_only_reviewer_policy_fails_closed_when_capability_safety_cannot_be_proved() -> None:
    async def scenario() -> None:
        agent_service, agent_runtime, reviewer_id = _agents()
        verification = VerificationService()
        policy = verification.register_policy(_policy(read_only=True))
        subject = _subject()
        request = verification.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id="unknown-capability-safety",
        )
        reviewer = ReviewerAgentRuntime(verification, agent_runtime)
        with pytest.raises(ContractError) as exc_info:
            await reviewer.start_review(
                request.verification_id,
                run_id=new_id("run"),
                agent_id=reviewer_id,
                requested_capability_ids=("review.lookup",),
                available_capability_ids=frozenset({"review.lookup"}),
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert agent_service.repository.list_agent_runs() == ()

    asyncio.run(scenario())


def test_reviewer_agent_rejects_caller_override_of_canonical_verification_context() -> None:
    async def scenario() -> None:
        _, agent_runtime, reviewer_id = _agents()
        verification = VerificationService()
        policy = verification.register_policy(_policy())
        subject = _subject()
        request = verification.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id="context-spoof",
        )
        reviewer = ReviewerAgentRuntime(verification, agent_runtime)
        with pytest.raises(ContractError) as exc_info:
            await reviewer.start_review(
                request.verification_id,
                run_id=new_id("run"),
                agent_id=reviewer_id,
                task_context={"verification": {"verification_id": "fake"}},
            )
        assert exc_info.value.code is ErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_agent_runtime_verification_context_is_immutable_after_start() -> None:
    async def scenario() -> None:
        _, agent_runtime, reviewer_id = _agents()
        verification = VerificationService()
        policy = verification.register_policy(_policy())
        subject = _subject()
        request = verification.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id="immutable-context",
        )
        reviewer = ReviewerAgentRuntime(verification, agent_runtime)
        record = await reviewer.start_review(
            request.verification_id,
            run_id=new_id("run"),
            agent_id=reviewer_id,
        )
        with pytest.raises(ContractError) as exc_info:
            agent_runtime.finish_agent_run(
                record.agent_run_id,
                status=AgentRunStatus.SUCCEEDED,
                verification_context={"schema": "forged"},
            )
        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
        assert (
            agent_runtime.service.repository.get_agent_run(record.agent_run_id).status
            is AgentRunStatus.RUNNING
        )

    asyncio.run(scenario())
