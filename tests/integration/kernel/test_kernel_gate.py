from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.testing import FakeEventProvider, FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend
from ai_multi_agent_platform.verification import (
    CompletionState,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)


def _stack() -> tuple[
    PlatformKernel,
    FakeLifecycleBackend,
    VerificationService,
    VerificationCompletionAuthority,
    FakeEventProvider,
]:
    lifecycle = FakeLifecycleBackend()
    verification = VerificationService()
    authority = VerificationCompletionAuthority(verification)
    sink = FakeEventProvider()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        event_sink=sink,
        completion_authority=authority,
    )
    return kernel, lifecycle, verification, authority, sink


def _policy(service: VerificationService) -> VerificationPolicy:
    policy = VerificationPolicy(
        name="human completion review",
        stages=(
            VerificationStage(
                stage_id="human-review",
                verifier_kind=VerifierKind.HUMAN,
            ),
        ),
    )
    service.register_policy(policy)
    return policy


def _started(
    kernel: PlatformKernel,
    lifecycle: FakeLifecycleBackend,
) -> tuple[str, str]:
    task = asyncio.run(
        kernel.create_task(
            idempotency_key="create",
            title="Verify me",
            objective="Produce a reviewed result",
            owner_type="user",
            owner_id="issue-86",
        )
    )
    asyncio.run(kernel.ready_task(idempotency_key="ready", task_id=task.task_id))
    run = asyncio.run(kernel.start_task(idempotency_key="start", task_id=task.task_id))
    assert run.status is RunStatus.RUNNING
    assert len(lifecycle.start_calls) == 1
    return task.task_id, run.run_id


def _finish_success(
    kernel: PlatformKernel,
    lifecycle: FakeLifecycleBackend,
    task_id: str,
    run_id: str,
) -> None:
    lifecycle.complete(run_id, status=ExecutionStatus.SUCCEEDED, output={"answer": 42})
    run = asyncio.run(
        kernel.refresh_run(
            idempotency_key="refresh-success",
            task_id=task_id,
            run_id=run_id,
        )
    )
    assert run.status is RunStatus.SUCCEEDED


def test_successful_run_cannot_bypass_required_verification() -> None:
    kernel, lifecycle, verification, authority, sink = _stack()
    policy = _policy(verification)
    task_id, run_id = _started(kernel, lifecycle)
    authority.require_task(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )

    _finish_success(kernel, lifecycle, task_id, run_id)

    waiting = asyncio.run(kernel.get_task(task_id))
    assert waiting.status is TaskStatus.WAITING
    assert waiting.blocked is True
    assert waiting.wait_reason == "verification:waiting"
    history = asyncio.run(kernel.history(task_id))
    assert "run.succeeded" in [event.event_type for event in history]
    assert "task.succeeded" not in [event.event_type for event in history]
    assert tuple(sink.publish_calls) == history

    result_id = new_id("result")
    asyncio.run(
        kernel.attach_result(
            idempotency_key="attach-result",
            task_id=task_id,
            run_id=run_id,
            result_id=result_id,
        )
    )
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest="sha256:reviewed-v1",
    )
    request = authority.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human-review",
        subject=subject,
        correlation_id=task_id,
        run_id=run_id,
        result_id=result_id,
    )
    verification.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
        comment="accepted",
    )
    assert authority.assess_task_completion(task_id).state is CompletionState.ACCEPTED

    completed = asyncio.run(
        kernel.complete_task(
            idempotency_key="complete-after-review",
            task_id=task_id,
        )
    )
    assert completed.status is TaskStatus.SUCCEEDED
    event_types = [event.event_type for event in asyncio.run(kernel.history(task_id))]
    assert event_types[-2:] == ["task.resumed", "task.succeeded"]


def test_changed_subject_invalidates_old_verification_at_completion_gate() -> None:
    kernel, lifecycle, verification, authority, _sink = _stack()
    policy = _policy(verification)
    task_id, run_id = _started(kernel, lifecycle)
    authority.require_task(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    _finish_success(kernel, lifecycle, task_id, run_id)

    first_result_id = new_id("result")
    asyncio.run(
        kernel.attach_result(
            idempotency_key="result-v1",
            task_id=task_id,
            run_id=run_id,
            result_id=first_result_id,
        )
    )
    first_subject = VerificationSubject(
        subject_type="result",
        subject_id=first_result_id,
        revision="1",
        digest="sha256:v1",
    )
    first = authority.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human-review",
        subject=first_subject,
        correlation_id=task_id,
        run_id=run_id,
        result_id=first_result_id,
    )
    verification.record_human_review(
        first.verification_id,
        reviewer_ref="user:reviewer-a",
        outcome=VerificationOutcome.PASS,
    )
    assert authority.assess_task_completion(task_id).state is CompletionState.ACCEPTED

    second_result_id = new_id("result")
    asyncio.run(
        kernel.attach_result(
            idempotency_key="result-v2",
            task_id=task_id,
            run_id=run_id,
            result_id=second_result_id,
        )
    )
    second_subject = VerificationSubject(
        subject_type="result",
        subject_id=second_result_id,
        revision="2",
        digest="sha256:v2",
    )
    second = authority.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human-review",
        subject=second_subject,
        correlation_id=task_id,
        run_id=run_id,
        result_id=second_result_id,
    )

    blocked = asyncio.run(
        kernel.complete_task(
            idempotency_key="premature-complete-v2",
            task_id=task_id,
        )
    )
    assert blocked.status is TaskStatus.WAITING
    assert blocked.wait_reason == "verification:waiting"
    assert authority.assess_task_completion(task_id).state is CompletionState.WAITING

    verification.record_human_review(
        second.verification_id,
        reviewer_ref="user:reviewer-b",
        outcome=VerificationOutcome.PASS,
    )
    completed = asyncio.run(
        kernel.complete_task(
            idempotency_key="complete-v2",
            task_id=task_id,
        )
    )
    assert completed.status is TaskStatus.SUCCEEDED


def test_rejected_verification_blocks_completion_without_rewriting_run_outcome() -> None:
    kernel, lifecycle, verification, authority, _sink = _stack()
    policy = _policy(verification)
    task_id, run_id = _started(kernel, lifecycle)
    authority.require_task(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    _finish_success(kernel, lifecycle, task_id, run_id)

    result_id = new_id("result")
    asyncio.run(
        kernel.attach_result(
            idempotency_key="rejected-result",
            task_id=task_id,
            run_id=run_id,
            result_id=result_id,
        )
    )
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest="sha256:rejected",
    )
    request = authority.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human-review",
        subject=subject,
        correlation_id=task_id,
        run_id=run_id,
        result_id=result_id,
    )
    verification.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.FAIL,
        comment="reject",
    )

    blocked = asyncio.run(
        kernel.complete_task(
            idempotency_key="blocked-rejected-completion",
            task_id=task_id,
        )
    )
    assert blocked.status is TaskStatus.WAITING
    assert blocked.wait_reason == "verification:rejected"
    assert blocked.blocked is True
    assert asyncio.run(kernel.get_run(task_id, run_id)).status is RunStatus.SUCCEEDED
    assert authority.assess_task_completion(task_id).state is CompletionState.REJECTED
    assert "task.succeeded" not in [
        event.event_type for event in asyncio.run(kernel.history(task_id))
    ]


def test_kernel_without_verification_requirement_keeps_existing_success_semantics() -> None:
    kernel, lifecycle, _verification, _authority, _sink = _stack()
    task_id, run_id = _started(kernel, lifecycle)
    _finish_success(kernel, lifecycle, task_id, run_id)
    assert asyncio.run(kernel.get_task(task_id)).status is TaskStatus.SUCCEEDED
