from __future__ import annotations

import asyncio
from dataclasses import replace

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
from ai_multi_agent_platform.domain import OwnerRef, new_id
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
    RepairOutput,
    ReviewerAssignment,
    ReviewerExecutionDecision,
)
from ai_multi_agent_platform.verification.persistence import SqliteVerificationService
from ai_multi_agent_platform.verification.repair import VerificationRepairExecution
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
    ReviewerRecoveryDisposition,
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


class StickyRepairRuntime:
    """Model one durable canonical repair execution across repeated recovery passes."""

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self.calls = 0
        self.execution: VerificationRepairExecution | None = None
        self.idempotency_keys: list[str] = []

    async def start_repair(
        self,
        verification_id: str,
        *,
        idempotency_key: str,
        step_id: str | None = None,
        actor_ref: str | None = None,
    ) -> VerificationRepairExecution:
        del step_id, actor_ref
        self.calls += 1
        self.idempotency_keys.append(idempotency_key)
        if self.execution is None:
            self.execution = VerificationRepairExecution(
                source_verification_id=verification_id,
                task_id=self._task_id,
                plan_id=new_id("plan"),
                step_id=new_id("step"),
                run_id=new_id("run"),
                repair_attempt=1,
            )
        assert self.execution.source_verification_id == verification_id
        return self.execution


class ReplacingRepairExecutor:
    def __init__(self, evidence: MutableEvidence) -> None:
        self._evidence = evidence
        self.calls = 0

    async def execute_repair(
        self,
        *,
        execution: VerificationRepairExecution,
        request,
        review_result,
    ) -> RepairOutput:
        del review_result
        self.calls += 1
        assert self.calls == 1, "staged repair output must prevent duplicate repair execution"
        assert execution.source_verification_id == request.verification_id
        repaired = VerificationSubject(
            subject_type="result",
            subject_id=new_id("result"),
            revision="2",
            digest="sha256:issue-758-repaired-result",
        )
        self._evidence.context = replace(
            self._evidence.context,
            subject=repaired,
            run_id=execution.run_id,
        )
        return RepairOutput(
            subject_type="result",
            subject_id=repaired.subject_id,
            correlation_id=f"{request.correlation_id}-repair",
            causation_id=execution.run_id,
        )


class FailOnceReverificationRuntime:
    """Crash boundary after durable repair output staging, before fresh Verification creation."""

    def __init__(self, delegate: CanonicalVerificationRuntime) -> None:
        self._delegate = delegate
        self.fail_reverification_once = True

    @property
    def evidence(self):
        return self._delegate.evidence

    async def submit_result(self, result):
        return await self._delegate.submit_result(result)

    async def request_reverification_after_repair(self, verification_id: str, **kwargs):
        if self.fail_reverification_once:
            self.fail_reverification_once = False
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "simulated process loss after staged repair output",
            )
        return await self._delegate.request_reverification_after_repair(verification_id, **kwargs)


def _setup(tmp_path, *, max_repairs: int = 0):
    agent_service = AgentService(InMemoryAgentRepository())
    producer = agent_service.create_agent(
        _profile("Developer", "developer"),
        owner_ref=OwnerRef(type="service", id="issue-758-boundaries"),
    )
    reviewer = agent_service.create_agent(
        _profile("Reviewer", "reviewer"),
        owner_ref=OwnerRef(type="service", id="issue-758-boundaries"),
    )
    agents = AgentRuntime(agent_service)

    verification = SqliteVerificationService(tmp_path / "verification-boundaries.sqlite3")
    policy = verification.register_policy(
        VerificationPolicy(
            name="automatic-agent-review-restart-boundaries",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            max_repair_attempts=max_repairs,
        )
    )
    task_id = new_id("task")
    run_id = new_id("run")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:issue-758-initial-result",
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
        correlation_id="issue-758-boundary-review",
    )
    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                agent_id=reviewer.agent_id,
                agent_revision=reviewer.revision,
            )
        }
    )
    return agents, verification, evidence, completion, runtime, request, resolver


def test_staged_decision_for_changed_subject_blocks_without_second_model_call(tmp_path) -> None:
    async def scenario() -> None:
        agents, verification, evidence, completion, runtime, request, resolver = _setup(tmp_path)
        evidence.fail_validation_once = True
        executor = EvidenceReviewerExecutor(new_id("artifact"))
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=executor,
        )

        with pytest.raises(ContractError) as interrupted:
            await workflow.run_request(request.verification_id)
        assert interrupted.value.code is ErrorCode.UNAVAILABLE
        assert executor.calls == 1
        staged = agents.service.repository.list_agent_runs()[0]
        assert staged.status is AgentRunStatus.SUCCEEDED
        assert "automatic_reviewer_decision" in staged.telemetry

        evidence.context = replace(
            evidence.context,
            subject=VerificationSubject(
                subject_type=request.subject.subject_type,
                subject_id=request.subject.subject_id,
                revision="2",
                digest="sha256:issue-758-superseded-result",
            ),
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        first = await recovery.reconcile_startup()
        second = await recovery.reconcile_startup()

        assert first[0].disposition is ReviewerRecoveryDisposition.BLOCKED
        assert second[0].disposition is ReviewerRecoveryDisposition.BLOCKED
        assert "subject differs from current canonical evidence" in (first[0].reason or "")
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1
        assert completion.assess_task_completion(request.task_id).state is CompletionState.WAITING

    asyncio.run(scenario())


def test_staged_repair_output_is_reused_after_restart_before_reverification(tmp_path) -> None:
    async def scenario() -> None:
        agents, verification, evidence, completion, runtime, request, resolver = _setup(
            tmp_path,
            max_repairs=1,
        )
        reviewer_executor = QueueReviewerExecutor(
            VerificationOutcome.NEEDS_CHANGES,
            VerificationOutcome.PASS,
        )
        repair_runtime = StickyRepairRuntime(request.task_id)
        repair_executor = ReplacingRepairExecutor(evidence)
        interrupted_runtime = FailOnceReverificationRuntime(runtime)
        workflow = AutomaticReviewerWorkflow(
            runtime=interrupted_runtime,  # type: ignore[arg-type]
            completion=completion,
            agents=agents,
            resolver=resolver,
            executor=reviewer_executor,
            repair_runtime=repair_runtime,  # type: ignore[arg-type]
            repair_executor=repair_executor,
        )

        with pytest.raises(ContractError) as interrupted:
            await workflow.run_request(request.verification_id)
        assert interrupted.value.code is ErrorCode.UNAVAILABLE
        assert reviewer_executor.calls == 1
        assert repair_executor.calls == 1
        first_reviewer = agents.service.repository.list_agent_runs()[0]
        assert first_reviewer.status is AgentRunStatus.SUCCEEDED
        assert "automatic_reviewer_repair_output" in first_reviewer.telemetry

        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )
        recovered = await recovery.reconcile_startup()

        assert recovered[0].disposition is ReviewerRecoveryDisposition.RECONCILED
        assert recovered[0].replacement_agent_run_id is not None
        assert repair_executor.calls == 1
        assert reviewer_executor.calls == 2
        assert len(set(repair_runtime.idempotency_keys)) == 1
        assert len(agents.service.repository.list_agent_runs()) == 2
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED

        repeated = await recovery.reconcile_startup()
        assert all(record.blocked is False for record in repeated)
        assert repair_executor.calls == 1
        assert reviewer_executor.calls == 2
        assert len(set(repair_runtime.idempotency_keys)) == 1
        assert len(agents.service.repository.list_agent_runs()) == 2

    asyncio.run(scenario())
