from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.deployment.startup_recovery import reconcile_single_node_startup
from ai_multi_agent_platform.domain import OwnerRef, TaskStatus, new_id
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    CompletionState,
    ProducerIdentity,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ReviewerAssignment,
    ReviewerExecutionDecision,
    ReviewerRuntimeOptions,
)
from ai_multi_agent_platform.verification.persistence import SqliteVerificationService
from ai_multi_agent_platform.verification.reviewer_agent import ReviewerAgentRuntime
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
    ReviewerRecoveryDisposition,
    ReviewerRecoveryRecord,
)


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(content=f"Act as the {role} for the exact assigned work."),
        ),
    )


class MutableEvidence:
    def __init__(self, context: VerificationEvidenceContext) -> None:
        self.context = context
        self.fail_validation_once = False

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
        if self.fail_validation_once:
            self.fail_validation_once = False
            raise ContractError(ErrorCode.UNAVAILABLE, "transient evidence validation failure")
        return artifact_ids


class QueueReviewerExecutor:
    def __init__(self, *outcomes: VerificationOutcome) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def execute_review(self, *, request, agent_run) -> ReviewerExecutionDecision:
        del request, agent_run
        self.calls += 1
        return ReviewerExecutionDecision(outcome=self._outcomes.pop(0))


class EvidenceReviewerExecutor:
    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id
        self.calls = 0

    async def execute_review(self, *, request, agent_run) -> ReviewerExecutionDecision:
        del request, agent_run
        self.calls += 1
        return ReviewerExecutionDecision(
            outcome=VerificationOutcome.PASS,
            evidence_artifact_ids=(self.artifact_id,),
        )


@dataclass
class _TaskSnapshot:
    status: TaskStatus


class StaticTaskReader:
    def __init__(self, status: TaskStatus) -> None:
        self._status = status

    async def get_task(self, task_id: str) -> _TaskSnapshot:
        assert task_id.startswith("task_")
        return _TaskSnapshot(self._status)


class EmptyRecoveryKernel:
    async def recover_all(self):
        return ()


class FixedReviewerReconciler:
    def __init__(self, *records: ReviewerRecoveryRecord) -> None:
        self.records = tuple(records)
        self.calls = 0

    async def reconcile_startup(self) -> tuple[ReviewerRecoveryRecord, ...]:
        self.calls += 1
        return self.records


def _setup(tmp_path):
    agent_service = AgentService(InMemoryAgentRepository())
    producer = agent_service.create_agent(
        _profile("Developer", "developer"),
        owner_ref=OwnerRef(type="service", id="issue-758"),
    )
    reviewer = agent_service.create_agent(
        _profile("Reviewer", "reviewer"),
        owner_ref=OwnerRef(type="service", id="issue-758"),
    )
    agents = AgentRuntime(agent_service)

    verification = SqliteVerificationService(tmp_path / "verification.sqlite3")
    policy = verification.register_policy(
        VerificationPolicy(
            name="automatic-agent-review-recovery",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
        )
    )
    task_id = new_id("task")
    run_id = new_id("run")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:issue-758-result",
    )
    evidence = MutableEvidence(
        VerificationEvidenceContext(
            task_id=task_id,
            subject=subject,
            run_id=run_id,
            project_id=None,
            capability_ids=(),
            producer=ProducerIdentity(
                actor_ref=f"agent:{producer.agent_id}@1",
                agent_id=producer.agent_id,
                agent_revision=1,
            ),
        )
    )
    completion = VerificationCompletionAuthority(verification)
    runtime = CanonicalVerificationRuntime(completion, evidence)
    request = completion.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        run_id=run_id,
        result_id=subject.subject_id,
        producer=evidence.context.producer,
        correlation_id="issue-758-review",
    )
    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                agent_id=reviewer.agent_id,
                agent_revision=reviewer.revision,
            )
        }
    )
    return agents, reviewer, verification, evidence, completion, runtime, request, resolver


def test_startup_dispatches_missing_reviewer_once_and_repeated_recovery_is_idempotent(
    tmp_path,
) -> None:
    async def scenario() -> None:
        agents, _reviewer, verification, _evidence, completion, runtime, request, resolver = _setup(
            tmp_path
        )
        executor = QueueReviewerExecutor(VerificationOutcome.PASS)
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=executor,
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        first = await recovery.reconcile_startup()
        second = await recovery.reconcile_startup()

        assert first[0].disposition is ReviewerRecoveryDisposition.DISPATCHED
        assert second[0].disposition is ReviewerRecoveryDisposition.ALREADY_COMPLETED
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_abandoned_running_reviewer_is_failed_then_retried_once(tmp_path) -> None:
    async def scenario() -> None:
        agents, reviewer, verification, _evidence, completion, runtime, request, resolver = _setup(
            tmp_path
        )
        bridge = ReviewerAgentRuntime(verification, agents)
        abandoned = await bridge.start_review(
            request.verification_id,
            run_id=request.run_id,
            agent_id=reviewer.agent_id,
            revision=reviewer.revision,
        )
        executor = QueueReviewerExecutor(VerificationOutcome.PASS)
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=executor,
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        records = await recovery.reconcile_startup()

        assert records[0].disposition is ReviewerRecoveryDisposition.ABANDONED_RETRIED
        assert records[0].reviewer_agent_run_id == abandoned.agent_run_id
        assert records[0].replacement_agent_run_id is not None
        runs = agents.service.repository.list_agent_runs()
        assert len(runs) == 2
        assert runs[0].status is AgentRunStatus.FAILED
        assert runs[1].status is AgentRunStatus.SUCCEEDED
        assert executor.calls == 1

        with pytest.raises(ContractError) as stale:
            await bridge.complete_review(
                abandoned.agent_run_id,
                outcome=VerificationOutcome.PASS,
            )
        assert stale.value.code is ErrorCode.CONFLICT
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED

        repeated = await recovery.reconcile_startup()
        assert repeated[0].disposition is ReviewerRecoveryDisposition.ALREADY_COMPLETED
        assert len(agents.service.repository.list_agent_runs()) == 2

    asyncio.run(scenario())


def test_staged_reviewer_decision_is_reused_without_second_model_call(tmp_path) -> None:
    async def scenario() -> None:
        agents, _reviewer, verification, evidence, completion, runtime, request, resolver = _setup(
            tmp_path
        )
        evidence.fail_validation_once = True
        executor = EvidenceReviewerExecutor(new_id("artifact"))
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=executor,
        )

        with pytest.raises(ContractError) as transient:
            await workflow.run_request(request.verification_id)
        assert transient.value.code is ErrorCode.UNAVAILABLE
        staged = agents.service.repository.list_agent_runs()[0]
        assert staged.status is AgentRunStatus.SUCCEEDED
        assert "automatic_reviewer_decision" in staged.telemetry
        assert executor.calls == 1

        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )
        recovered = await recovery.reconcile_startup()

        assert recovered[0].disposition is ReviewerRecoveryDisposition.STAGED_DECISION_REUSED
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_cancelled_task_cancels_pending_verification_and_stale_reviewer(tmp_path) -> None:
    async def scenario() -> None:
        agents, reviewer, verification, _evidence, completion, runtime, request, resolver = _setup(
            tmp_path
        )
        bridge = ReviewerAgentRuntime(verification, agents)
        running = await bridge.start_review(
            request.verification_id,
            run_id=request.run_id,
            agent_id=reviewer.agent_id,
            revision=reviewer.revision,
        )
        executor = QueueReviewerExecutor(VerificationOutcome.PASS)
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=executor,
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
            tasks=StaticTaskReader(TaskStatus.CANCELLED),
        )

        records = await recovery.reconcile_startup()

        assert records[0].disposition is ReviewerRecoveryDisposition.CANCELLED_STALE_RUN
        assert verification.get_request(request.verification_id).status.value == "cancelled"
        assert agents.service.repository.get_agent_run(running.agent_run_id).status is AgentRunStatus.CANCELLED
        assert executor.calls == 0
        with pytest.raises(ContractError) as stale:
            await bridge.complete_review(running.agent_run_id, outcome=VerificationOutcome.PASS)
        assert stale.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_abandoned_reviewer_respects_retry_budget_across_restart(tmp_path) -> None:
    async def scenario() -> None:
        agents, reviewer, verification, _evidence, completion, runtime, request, resolver = _setup(
            tmp_path
        )
        bridge = ReviewerAgentRuntime(verification, agents)
        running = await bridge.start_review(
            request.verification_id,
            run_id=request.run_id,
            agent_id=reviewer.agent_id,
            revision=reviewer.revision,
        )
        executor = QueueReviewerExecutor(VerificationOutcome.PASS)
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=executor,
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        records = await recovery.reconcile_startup(
            options=ReviewerRuntimeOptions(max_reviewer_attempts=1)
        )

        assert records[0].disposition is ReviewerRecoveryDisposition.BLOCKED
        assert records[0].reviewer_agent_run_id == running.agent_run_id
        assert agents.service.repository.get_agent_run(running.agent_run_id).status is AgentRunStatus.FAILED
        assert executor.calls == 0
        assert len(agents.service.repository.list_agent_runs()) == 1
        assert completion.assess_task_completion(request.task_id).state is CompletionState.WAITING

    asyncio.run(scenario())


def test_startup_report_blocks_readiness_for_unresolved_reviewer(tmp_path) -> None:
    async def scenario() -> None:
        verification_id = new_id("verification")
        task_id = new_id("task")
        reviewer = FixedReviewerReconciler(
            ReviewerRecoveryRecord(
                verification_id=verification_id,
                task_id=task_id,
                disposition=ReviewerRecoveryDisposition.BLOCKED,
                reason="manual reviewer reconciliation required",
            )
        )

        result = await reconcile_single_node_startup(
            data_dir=tmp_path,
            kernel=EmptyRecoveryKernel(),  # type: ignore[arg-type]
            reviewer_reconciler=reviewer,
        )

        assert result.ready_for_service is False
        assert result.blocked_verification_ids == (verification_id,)
        assert reviewer.calls == 1
        payload = json.loads(result.report_path.read_text(encoding="utf-8"))
        assert payload["blocked_verification_ids"] == [verification_id]
        assert payload["reviewer_recoveries"][0]["disposition"] == "blocked"

    asyncio.run(scenario())
