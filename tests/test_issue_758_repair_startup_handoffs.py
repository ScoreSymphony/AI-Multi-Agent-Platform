from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    CompletionState,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_repair import KernelAgentRepairExecutor
from ai_multi_agent_platform.verification.agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ReviewerAssignment,
    ReviewerExecutionDecision,
)
from ai_multi_agent_platform.verification.persistence import SqliteVerificationService
from ai_multi_agent_platform.verification.repair import (
    VERIFICATION_REPAIR_SOURCE,
    VerificationRepairRuntime,
)
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
    ReviewerRecoveryDisposition,
)


class MutableEvidence:
    def __init__(self, context: VerificationEvidenceContext) -> None:
        self.context = context

    async def resolve_subject(self, *, task_id, subject_type, subject_id):
        assert task_id == self.context.task_id
        assert subject_type == self.context.subject.subject_type
        assert subject_id == self.context.subject.subject_id
        return self.context.subject

    async def resolve_context(self, *, task_id, subject_type, subject_id):
        await self.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        return self.context

    async def validate_evidence_artifacts(self, *, task_id, artifact_ids):
        assert task_id == self.context.task_id
        return artifact_ids


class QueueReviewerExecutor:
    def __init__(self, *outcomes: VerificationOutcome) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def execute_review(self, *, request, agent_run):
        del request, agent_run
        self.calls += 1
        return ReviewerExecutionDecision(outcome=self._outcomes.pop(0))


class CompletingKernelRepairExecutor:
    def __init__(self, kernel, lifecycle, evidence: MutableEvidence) -> None:
        self._kernel = kernel
        self._lifecycle = lifecycle
        self._evidence = evidence
        self._delegate = KernelAgentRepairExecutor(kernel)
        self.calls = 0
        self.result_id: str | None = None

    async def execute_repair(self, *, execution, request, review_result):
        self.calls += 1
        if self.result_id is None:
            self.result_id = new_id("result")
        self._lifecycle.complete(
            execution.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"result_id": self.result_id},
        )
        await self._kernel.refresh_run(
            idempotency_key=f"issue-758-repair:{execution.run_id}:complete",
            task_id=execution.task_id,
            run_id=execution.run_id,
        )
        repaired = await self._delegate.execute_repair(
            execution=execution,
            request=request,
            review_result=review_result,
        )
        self._evidence.context = replace(
            self._evidence.context,
            subject=VerificationSubject(
                subject_type="result",
                subject_id=repaired.subject_id,
                revision=f"repair-attempt:{execution.repair_attempt}",
                digest=f"sha256:issue-758-repair-{execution.repair_attempt}",
            ),
            run_id=execution.run_id,
        )
        return repaired


def _profile() -> AgentProfile:
    return AgentProfile(
        name="Reviewer",
        role="reviewer",
        instructions=AgentInstructions(
            role=InstructionSource(content="Review the exact assigned output."),
        ),
    )


async def _needs_changes_stack(tmp_path):
    service = AgentService(InMemoryAgentRepository())
    reviewer = service.create_agent(
        _profile(),
        owner_ref=OwnerRef(type="service", id="issue-758-repair-startup"),
    )
    agents = AgentRuntime(service)

    verification = SqliteVerificationService(
        tmp_path / "verification-repair-startup.sqlite3"
    )
    completion = VerificationCompletionAuthority(verification)
    policy = verification.register_policy(
        VerificationPolicy(
            name="automatic-reviewer-repair-startup",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
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
        idempotency_key="issue-758-repair-startup:create",
        title="Reviewer repair startup handoff",
        objective="Produce an output, repair it after review, and re-review it exactly once.",
        owner_type="service",
        owner_id="issue-758-repair-startup",
    )
    await kernel.ready_task(
        idempotency_key="issue-758-repair-startup:ready",
        task_id=task.task_id,
    )
    producer_run = await kernel.start_task(
        idempotency_key="issue-758-repair-startup:start",
        task_id=task.task_id,
    )
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:issue-758-before-repair",
    )
    evidence = MutableEvidence(
        VerificationEvidenceContext(
            task_id=task.task_id,
            subject=subject,
            run_id=producer_run.run_id,
            project_id=None,
            capability_ids=(),
            producer=None,
        )
    )
    runtime = CanonicalVerificationRuntime(completion, evidence)
    request = completion.request_verification(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        correlation_id="issue-758-repair-startup",
        run_id=producer_run.run_id,
        result_id=subject.subject_id,
    )
    lifecycle.complete(
        producer_run.run_id,
        status=ExecutionStatus.SUCCEEDED,
        output={"result_id": subject.subject_id},
    )
    await kernel.refresh_run(
        idempotency_key="issue-758-repair-startup:producer-complete",
        task_id=task.task_id,
        run_id=producer_run.run_id,
    )

    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                agent_id=reviewer.agent_id,
                agent_revision=reviewer.revision,
            )
        }
    )
    initial_executor = QueueReviewerExecutor(VerificationOutcome.NEEDS_CHANGES)
    initial_workflow = AutomaticReviewerWorkflow(
        runtime=runtime,
        completion=completion,
        agents=agents,
        resolver=resolver,
        executor=initial_executor,
    )
    initial = await initial_workflow.run_request(request.verification_id)
    assert initial.completion.state is CompletionState.REPAIR_REQUIRED
    assert initial_executor.calls == 1

    return (
        agents,
        verification,
        completion,
        runtime,
        request,
        resolver,
        kernel,
        lifecycle,
        evidence,
    )


async def _repair_run_ids(kernel, task_id: str) -> tuple[str, ...]:
    return tuple(
        event.subject_id
        for event in await kernel.history(task_id)
        if event.event_type == "run.created"
        and event.provenance is not None
        and event.provenance.source == VERIFICATION_REPAIR_SOURCE
    )


def test_restart_after_needs_changes_before_repair_run_creates_one_lineage(
    tmp_path,
) -> None:
    async def scenario() -> None:
        (
            agents,
            verification,
            completion,
            runtime,
            request,
            resolver,
            kernel,
            lifecycle,
            evidence,
        ) = await _needs_changes_stack(tmp_path)
        assert await _repair_run_ids(kernel, request.task_id) == ()

        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        repair_executor = CompletingKernelRepairExecutor(kernel, lifecycle, evidence)
        reviewer_executor = QueueReviewerExecutor(VerificationOutcome.PASS)
        restarted_workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=reviewer_executor,
            repair_runtime=repair_runtime,
            repair_executor=repair_executor,
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=restarted_workflow,
            agents=agents,
            verification=verification,
        )

        first = await recovery.reconcile_startup()
        repeated = await recovery.reconcile_startup()

        assert first[0].disposition is ReviewerRecoveryDisposition.RECONCILED
        assert all(record.blocked is False for record in repeated)
        assert repair_executor.calls == 1
        assert reviewer_executor.calls == 1
        repair_runs = await _repair_run_ids(kernel, request.task_id)
        assert len(repair_runs) == 1
        assert len(verification.history(task_id=request.task_id)) == 2
        assert len(agents.service.repository.list_agent_runs()) == 2
        assert (
            completion.assess_task_completion(request.task_id).state
            is CompletionState.ACCEPTED
        )

        repaired_result_id = repair_executor.result_id
        assert repaired_result_id is not None
        attachments = [
            event
            for event in await kernel.history(request.task_id)
            if event.event_type == "result.attached"
            and event.payload.get("result_id") == repaired_result_id
        ]
        assert len(attachments) == 1

    asyncio.run(scenario())


def test_restart_after_fresh_reverification_before_dispatch_creates_one_reviewer_run(
    tmp_path,
) -> None:
    async def scenario() -> None:
        (
            agents,
            verification,
            completion,
            runtime,
            request,
            resolver,
            kernel,
            lifecycle,
            evidence,
        ) = await _needs_changes_stack(tmp_path)

        repair_runtime = VerificationRepairRuntime(verification, completion, kernel)
        execution = await repair_runtime.start_repair(
            request.verification_id,
            idempotency_key="issue-758-pre-dispatch-repair",
        )
        repair_executor = CompletingKernelRepairExecutor(kernel, lifecycle, evidence)
        source_result = verification.result_for(request.verification_id)
        assert source_result is not None
        repaired = await repair_executor.execute_repair(
            execution=execution,
            request=request,
            review_result=source_result,
        )
        descendant = await runtime.request_reverification_after_repair(
            request.verification_id,
            subject_type=repaired.subject_type,
            subject_id=repaired.subject_id,
            correlation_id=repaired.correlation_id,
            causation_id=repaired.causation_id,
        )
        source_reviewer_runs = agents.service.repository.list_agent_runs()
        assert len(source_reviewer_runs) == 1
        assert all(
            run.verification_context.get("verification_id") != descendant.verification_id
            for run in source_reviewer_runs
        )

        reviewer_executor = QueueReviewerExecutor(VerificationOutcome.PASS)
        restarted_workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=reviewer_executor,
            repair_runtime=repair_runtime,
            repair_executor=repair_executor,
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=restarted_workflow,
            agents=agents,
            verification=verification,
        )

        first = await recovery.reconcile_startup()
        repeated = await recovery.reconcile_startup()

        descendant_recovery = next(
            record for record in first if record.verification_id == descendant.verification_id
        )
        assert descendant_recovery.disposition is ReviewerRecoveryDisposition.DISPATCHED
        assert reviewer_executor.calls == 1
        assert repair_executor.calls == 1
        assert len(await _repair_run_ids(kernel, request.task_id)) == 1
        assert len(verification.history(task_id=request.task_id)) == 2
        reviewer_runs = agents.service.repository.list_agent_runs()
        assert len(reviewer_runs) == 2
        assert sum(
            run.verification_context.get("verification_id") == descendant.verification_id
            for run in reviewer_runs
        ) == 1
        assert all(record.blocked is False for record in repeated)
        assert len(agents.service.repository.list_agent_runs()) == 2
        assert (
            completion.assess_task_completion(request.task_id).state
            is CompletionState.ACCEPTED
        )

    asyncio.run(scenario())