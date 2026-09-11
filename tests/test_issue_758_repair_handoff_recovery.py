from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    CompletionState,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_repair import KernelAgentRepairExecutor
from ai_multi_agent_platform.verification.repair import (
    VERIFICATION_REPAIR_SOURCE,
    VerificationRepairRuntime,
)


class StaticEvidence:
    def __init__(self, context: VerificationEvidenceContext) -> None:
        self.context = context

    async def resolve_subject(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationSubject:
        assert task_id == self.context.task_id
        assert subject_type == self.context.subject.subject_type
        assert subject_id == self.context.subject.subject_id
        return self.context.subject

    async def resolve_context(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationEvidenceContext:
        await self.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        return self.context

    async def validate_evidence_artifacts(
        self,
        *,
        task_id: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        assert task_id == self.context.task_id
        return artifact_ids


async def _needs_changes_stack():
    verification = VerificationService()
    completion = VerificationCompletionAuthority(verification)
    policy = verification.register_policy(
        VerificationPolicy(
            name="issue-758-repair-restart",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            max_repair_attempts=1,
        )
    )
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
        completion_authority=completion,
    )
    task = await kernel.create_task(
        idempotency_key="issue-758:create",
        title="Repair restart handoff",
        objective="Produce one output and repair it after review requests changes",
        owner_type="user",
        owner_id="issue-758",
    )
    await kernel.ready_task(idempotency_key="issue-758:ready", task_id=task.task_id)
    original_run = await kernel.start_task(
        idempotency_key="issue-758:start",
        task_id=task.task_id,
    )
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:issue-758-original",
    )
    request = completion.request_verification(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        correlation_id="issue-758-repair-review",
        run_id=original_run.run_id,
        result_id=subject.subject_id,
    )
    lifecycle.complete(
        original_run.run_id,
        status=ExecutionStatus.SUCCEEDED,
        output={"result_id": subject.subject_id},
    )
    await kernel.refresh_run(
        idempotency_key="issue-758:original-success",
        task_id=task.task_id,
        run_id=original_run.run_id,
    )
    verification.record_human_review(
        request.verification_id,
        reviewer_ref="user:issue-758-reviewer",
        outcome=VerificationOutcome.NEEDS_CHANGES,
        comment="repair this output",
    )
    await kernel.complete_task(
        idempotency_key="issue-758:project-repair-required",
        task_id=task.task_id,
    )
    assert completion.assess_task_completion(task.task_id).state is CompletionState.REPAIR_REQUIRED
    return verification, completion, kernel, lifecycle, task.task_id, request.verification_id


def test_restart_before_and_after_repair_run_creation_reuses_one_canonical_run() -> None:
    async def scenario() -> None:
        verification, completion, kernel, _lifecycle, task_id, verification_id = (
            await _needs_changes_stack()
        )

        before_restart = VerificationRepairRuntime(verification, completion, kernel)
        first = await before_restart.start_repair(
            verification_id,
            idempotency_key="process-a-repair",
        )

        after_restart = VerificationRepairRuntime(verification, completion, kernel)
        recovered = await after_restart.start_repair(
            verification_id,
            idempotency_key="process-b-recovery",
        )

        assert recovered == first
        repair_runs = [
            event
            for event in await kernel.history(task_id)
            if event.event_type == "run.created"
            and event.provenance is not None
            and event.provenance.source == VERIFICATION_REPAIR_SOURCE
        ]
        assert len(repair_runs) == 1
        assert repair_runs[0].subject_id == first.run_id

    asyncio.run(scenario())


def test_restart_after_repair_execution_before_result_attachment_reuses_attachment() -> None:
    async def scenario() -> None:
        verification, completion, kernel, lifecycle, task_id, verification_id = (
            await _needs_changes_stack()
        )
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        execution = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="issue-758-repair-execution",
        )
        repaired_result_id = new_id("result")
        lifecycle.complete(
            execution.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"result_id": repaired_result_id},
        )
        await kernel.refresh_run(
            idempotency_key="issue-758-repair-execution:terminal",
            task_id=task_id,
            run_id=execution.run_id,
        )
        request = verification.get_request(verification_id)
        review_result = verification.result_for(verification_id)
        assert review_result is not None

        recovered_executor = KernelAgentRepairExecutor(kernel)
        first_output = await recovered_executor.execute_repair(
            execution=execution,
            request=request,
            review_result=review_result,
        )
        repeated_after_restart = await KernelAgentRepairExecutor(kernel).execute_repair(
            execution=execution,
            request=request,
            review_result=review_result,
        )

        assert repeated_after_restart == first_output
        assert first_output.subject_id == repaired_result_id
        attachments = [
            event
            for event in await kernel.history(task_id)
            if event.event_type == "result.attached"
            and event.payload.get("result_id") == repaired_result_id
        ]
        assert len(attachments) == 1
        assert (await kernel.get_task(task_id)).result_ids.count(repaired_result_id) == 1

    asyncio.run(scenario())


def test_restart_after_fresh_reverification_request_reuses_exact_descendant() -> None:
    async def scenario() -> None:
        verification, completion, kernel, lifecycle, task_id, verification_id = (
            await _needs_changes_stack()
        )
        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        execution = await repair_runtime.start_repair(
            verification_id,
            idempotency_key="issue-758-reverification",
        )
        repaired_result_id = new_id("result")
        lifecycle.complete(
            execution.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"result_id": repaired_result_id},
        )
        await kernel.refresh_run(
            idempotency_key="issue-758-reverification:terminal",
            task_id=task_id,
            run_id=execution.run_id,
        )
        request = verification.get_request(verification_id)
        review_result = verification.result_for(verification_id)
        assert review_result is not None
        await KernelAgentRepairExecutor(kernel).execute_repair(
            execution=execution,
            request=request,
            review_result=review_result,
        )

        repaired_subject = VerificationSubject(
            subject_type="result",
            subject_id=repaired_result_id,
            revision="2",
            digest="sha256:issue-758-repaired",
        )
        evidence = StaticEvidence(
            VerificationEvidenceContext(
                task_id=task_id,
                subject=repaired_subject,
                run_id=execution.run_id,
                project_id=None,
                capability_ids=(),
                producer=None,
            )
        )
        before_restart = CanonicalVerificationRuntime(completion, evidence)
        first = await before_restart.request_reverification_after_repair(
            verification_id,
            subject_type="result",
            subject_id=repaired_result_id,
            correlation_id="issue-758-repaired-review",
            causation_id=execution.run_id,
        )

        after_restart = CanonicalVerificationRuntime(completion, evidence)
        recovered = await after_restart.request_reverification_after_repair(
            verification_id,
            subject_type="result",
            subject_id=repaired_result_id,
            correlation_id="issue-758-repaired-review",
            causation_id=execution.run_id,
        )

        assert recovered == first
        history = verification.history(task_id=task_id)
        descendants = [
            persisted
            for persisted, _result in history
            if persisted.repair_attempt == 1
            and persisted.causation_id == execution.run_id
        ]
        assert len(descendants) == 1
        assert descendants[0].verification_id == first.verification_id

    asyncio.run(scenario())
