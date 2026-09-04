from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionStatus,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
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
from ai_multi_agent_platform.verification.repair import VerificationRepairRuntime


class TwoStepRepairOrchestrator(FakeOrchestrator):
    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        return PlanResponse(
            summary=f"Two-step plan for {request.objective}",
            steps=(
                PlanStepProposal(
                    key="repair-a",
                    title="Repair A",
                    objective=request.objective,
                ),
                PlanStepProposal(
                    key="repair-b",
                    title="Repair B",
                    objective=request.objective,
                    depends_on=("repair-a",),
                ),
            ),
        )


def _policy(max_repair_attempts: int = 1) -> VerificationPolicy:
    return VerificationPolicy(
        name="human repair policy",
        stages=(
            VerificationStage(
                stage_id="review",
                verifier_kind=VerifierKind.HUMAN,
            ),
        ),
        max_repair_attempts=max_repair_attempts,
    )


def _subject(*, revision: str = "1", digest: str = "sha256:original") -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision=revision,
        digest=digest,
    )


async def _needs_changes_stack(
    *,
    max_repair_attempts: int = 1,
    orchestrator: FakeOrchestrator | None = None,
) -> tuple[
    VerificationService,
    VerificationCompletionAuthority,
    PlatformKernel,
    FakeLifecycleBackend,
    str,
    str,
    str,
]:
    verification = VerificationService()
    completion = VerificationCompletionAuthority(verification)
    policy = verification.register_policy(_policy(max_repair_attempts))
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=orchestrator or FakeOrchestrator(),
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
        completion_authority=completion,
    )
    task = await kernel.create_task(
        idempotency_key="repair:create",
        title="Repair reviewed output",
        objective="Produce an output and repair it when verification requests changes",
        owner_type="user",
        owner_id="issue-86-repair",
    )
    await kernel.ready_task(idempotency_key="repair:ready", task_id=task.task_id)
    task_run = await kernel.start_task(idempotency_key="repair:start", task_id=task.task_id)
    planned = await kernel.get_task(task.task_id)
    assert planned.plan_ref is not None
    original_plan_id = planned.plan_ref

    subject = _subject()
    request = completion.request_verification(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        correlation_id=task.task_id,
        run_id=task_run.run_id,
        result_id=subject.subject_id,
    )
    lifecycle.complete(
        task_run.run_id,
        status=ExecutionStatus.SUCCEEDED,
        output={"answer": "first revision"},
    )
    await kernel.refresh_run(
        idempotency_key="repair:initial-run-success",
        task_id=task.task_id,
        run_id=task_run.run_id,
    )
    verification.record_human_review(
        request.verification_id,
        reviewer_ref="user:repair-reviewer",
        outcome=VerificationOutcome.NEEDS_CHANGES,
        comment="Revise the output.",
    )
    await kernel.complete_task(
        idempotency_key="repair:apply-needs-changes-gate",
        task_id=task.task_id,
    )
    expected_state = (
        CompletionState.REPAIR_REQUIRED if max_repair_attempts > 0 else CompletionState.REJECTED
    )
    assert completion.assess_task_completion(task.task_id).state is expected_state
    return (
        verification,
        completion,
        kernel,
        lifecycle,
        task.task_id,
        request.verification_id,
        original_plan_id,
    )


def test_needs_changes_starts_canonical_repair_plan_step_and_run() -> None:
    async def scenario() -> None:
        (
            verification,
            completion,
            kernel,
            _lifecycle,
            task_id,
            verification_id,
            original_plan_id,
        ) = await _needs_changes_stack()
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)

        execution = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="repair-cycle-1",
            actor_ref="service:verification",
        )
        task = await kernel.get_task(task_id)
        run = await kernel.get_run(task_id, execution.run_id)

        assert execution.repair_attempt == 1
        assert execution.plan_id != original_plan_id
        assert task.plan_ref == execution.plan_id
        assert task.status is TaskStatus.RUNNING
        assert execution.step_id in task.step_ids
        assert run.run.subject_type == "step"
        assert run.run.subject_id == execution.step_id
        assert run.status is RunStatus.RUNNING
        assert completion.assess_task_completion(task_id).state is CompletionState.REPAIR_REQUIRED

        history = await kernel.history(task_id)
        repair_events = [
            event
            for event in history
            if event.provenance is not None and event.provenance.source == "verification-repair"
        ]
        assert repair_events
        assert {event.event_type for event in repair_events}.issuperset(
            {"plan.created", "task.resumed", "run.created", "run.running"}
        )
        assert all(verification_id in (event.causation_id or "") for event in repair_events)

    asyncio.run(scenario())


def test_repair_start_is_retry_safe_for_same_idempotency_key() -> None:
    async def scenario() -> None:
        (
            verification,
            completion,
            kernel,
            _lifecycle,
            _task_id,
            verification_id,
            _plan,
        ) = await _needs_changes_stack()
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        first = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="same-repair",
        )
        second = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="same-repair",
        )
        assert second == first

    asyncio.run(scenario())


def test_multi_step_repair_plan_requires_explicit_scheduler_choice() -> None:
    async def scenario() -> None:
        orchestrator = TwoStepRepairOrchestrator()
        (
            verification,
            completion,
            kernel,
            _lifecycle,
            task_id,
            verification_id,
            _plan,
        ) = await _needs_changes_stack(orchestrator=orchestrator)
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)

        with pytest.raises(ContractError) as exc_info:
            await repair_runtime.start_repair(
                verification_id,
                idempotency_key="multi-step-repair",
            )
        assert exc_info.value.code is ErrorCode.INVALID_REQUEST

        replanned = await kernel.get_task(task_id)
        assert replanned.status is TaskStatus.WAITING
        assert len(replanned.step_ids) == 2
        execution = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="multi-step-repair",
            step_id=replanned.step_ids[0],
        )
        assert execution.step_id == replanned.step_ids[0]
        assert (await kernel.get_run(task_id, execution.run_id)).status is RunStatus.RUNNING

    asyncio.run(scenario())


def test_successful_repair_rebinds_exact_subject_and_preserves_old_review_history() -> None:
    async def scenario() -> None:
        (
            verification,
            completion,
            kernel,
            lifecycle,
            task_id,
            verification_id,
            _plan,
        ) = await _needs_changes_stack()
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        execution = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="complete-repair",
        )

        lifecycle.complete(
            execution.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"answer": "repaired revision"},
        )
        repaired_run = await kernel.refresh_run(
            idempotency_key="complete-repair:refresh",
            task_id=task_id,
            run_id=execution.run_id,
        )
        assert repaired_run.status is RunStatus.SUCCEEDED
        assert (await kernel.get_task(task_id)).status is TaskStatus.RUNNING

        repaired_result_id = new_id("result")
        await kernel.attach_result(
            idempotency_key="complete-repair:attach-result",
            task_id=task_id,
            run_id=execution.run_id,
            result_id=repaired_result_id,
        )
        new_subject = VerificationSubject(
            subject_type="result",
            subject_id=repaired_result_id,
            revision="2",
            digest="sha256:repaired-v2",
        )
        next_request = completion.request_reverification_after_repair(
            verification_id,
            new_subject=new_subject,
            correlation_id=task_id,
            run_id=execution.run_id,
            result_id=repaired_result_id,
            causation_id="complete-repair:reverify",
        )
        assert next_request.repair_attempt == 1
        assert next_request.subject == new_subject
        assert completion.requirement_for(task_id).subject == new_subject

        waiting = await kernel.complete_task(
            idempotency_key="complete-repair:wait-for-reverification",
            task_id=task_id,
        )
        assert waiting.status is TaskStatus.WAITING
        assert waiting.wait_reason == "verification:waiting"

        verification.record_human_review(
            next_request.verification_id,
            reviewer_ref="user:repair-reviewer-2",
            outcome=VerificationOutcome.PASS,
        )
        completed = await kernel.complete_task(
            idempotency_key="complete-repair:accepted",
            task_id=task_id,
        )
        assert completed.status is TaskStatus.SUCCEEDED

        history = verification.history(task_id=task_id)
        assert len(history) == 2
        assert history[0][0].verification_id == verification_id
        assert history[0][1] is not None
        assert history[0][1].outcome is VerificationOutcome.NEEDS_CHANGES
        assert history[1][0].verification_id == next_request.verification_id
        assert history[1][1] is not None
        assert history[1][1].outcome is VerificationOutcome.PASS

    asyncio.run(scenario())


def test_exhausted_repair_budget_cannot_start_another_repair_run() -> None:
    async def scenario() -> None:
        (
            verification,
            completion,
            kernel,
            _lifecycle,
            _task_id,
            verification_id,
            _plan,
        ) = await _needs_changes_stack(max_repair_attempts=0)
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        with pytest.raises(ContractError) as exc_info:
            await repair_runtime.start_repair(
                verification_id,
                idempotency_key="repair-beyond-budget",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_repair_round_reuses_same_execution_even_with_new_caller_key() -> None:
    async def scenario() -> None:
        (
            verification,
            completion,
            kernel,
            lifecycle,
            task_id,
            verification_id,
            _plan,
        ) = await _needs_changes_stack()
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        first = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="first-caller-key",
        )
        lifecycle.complete(
            first.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"answer": "repair finished"},
        )
        await kernel.refresh_run(
            idempotency_key="first-caller-key:refresh",
            task_id=task_id,
            run_id=first.run_id,
        )
        second = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="different-caller-key",
        )
        assert second == first
        repair_runs = [
            event
            for event in await kernel.history(task_id)
            if event.event_type == "run.created"
            and event.provenance is not None
            and event.provenance.source == "verification-repair"
        ]
        assert len(repair_runs) == 1

    asyncio.run(scenario())
