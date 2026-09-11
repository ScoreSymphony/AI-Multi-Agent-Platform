from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunStatus,
    AgentRuntime,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    CompletionState,
    ProducerIdentity,
    ReviewerIndependence,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
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
    ReviewerRuntimeOptions,
)
from ai_multi_agent_platform.verification.repair import VerificationRepairExecution
from ai_multi_agent_platform.verification.reviewer_agent import ReviewerAgentRuntime


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
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "transient evidence backend failure",
            )
        return artifact_ids


class QueueReviewerExecutor:
    def __init__(self, *outcomes: VerificationOutcome) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def execute_review(self, *, request, agent_run) -> ReviewerExecutionDecision:
        del request, agent_run
        self.calls += 1
        return ReviewerExecutionDecision(outcome=self._outcomes.pop(0))


class BlockingReviewerExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute_review(self, *, request, agent_run) -> ReviewerExecutionDecision:
        del request, agent_run
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return ReviewerExecutionDecision(outcome=VerificationOutcome.PASS)


class CancelThenPassReviewerExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self._never = asyncio.Event()

    async def execute_review(self, *, request, agent_run) -> ReviewerExecutionDecision:
        del request, agent_run
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self._never.wait()
        return ReviewerExecutionDecision(outcome=VerificationOutcome.PASS)


class FakeRepairRuntime:
    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self.calls = 0

    async def start_repair(
        self,
        verification_id: str,
        *,
        idempotency_key: str,
        step_id: str | None = None,
        actor_ref: str | None = None,
    ) -> VerificationRepairExecution:
        del idempotency_key, step_id, actor_ref
        self.calls += 1
        return VerificationRepairExecution(
            source_verification_id=verification_id,
            task_id=self._task_id,
            plan_id=new_id("plan"),
            step_id=new_id("step"),
            run_id=new_id("run"),
            repair_attempt=self.calls,
        )


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
        assert execution.source_verification_id == request.verification_id
        repaired = VerificationSubject(
            subject_type="result",
            subject_id=new_id("result"),
            revision="2",
            digest="sha256:repaired-result",
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


def _setup(*, max_repairs: int = 0, independent: bool = False):
    agent_service = AgentService(InMemoryAgentRepository())
    producer = agent_service.create_agent(
        _profile("Developer", "developer"),
        owner_ref=OwnerRef(type="service", id="issue-711"),
    )
    reviewer = agent_service.create_agent(
        _profile("Reviewer", "reviewer"),
        owner_ref=OwnerRef(type="service", id="issue-711"),
    )
    agents = AgentRuntime(agent_service)

    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="automatic-agent-review",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            independence=ReviewerIndependence(
                producer_agent_must_differ=independent,
                agent_reviewer_must_be_read_only=True,
            ),
            max_repair_attempts=max_repairs,
        )
    )
    task_id = new_id("task")
    run_id = new_id("run")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:initial-result",
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
        correlation_id="issue-711-review",
    )
    return (
        agents,
        producer,
        reviewer,
        verification,
        policy,
        evidence,
        completion,
        runtime,
        request,
    )


def _resolver(policy, reviewer) -> ConfiguredReviewerResolver:
    return ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                agent_id=reviewer.agent_id,
                agent_revision=reviewer.revision,
            )
        }
    )


def test_automatic_reviewer_dispatch_records_canonical_result_and_is_idempotent() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            reviewer,
            _verification,
            policy,
            _evidence,
            completion,
            runtime,
            request,
        ) = _setup(independent=True)
        executor = QueueReviewerExecutor(VerificationOutcome.PASS)
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=_resolver(policy, reviewer),
            executor=executor,
        )

        result = await workflow.run_request(request.verification_id)

        assert result.completion.state is CompletionState.ACCEPTED
        assert result.latest.verification_result is not None
        assert result.latest.verification_result.outcome is VerificationOutcome.PASS
        assert result.latest.reviewer_run is not None
        assert result.latest.reviewer_run.agent.agent_id == reviewer.agent_id
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

        repeated = await workflow.run_request(request.verification_id)
        assert repeated.completion.state is CompletionState.ACCEPTED
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_concurrent_dispatch_is_serialized_across_workflow_instances() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            reviewer,
            _verification,
            policy,
            _evidence,
            completion,
            runtime,
            request,
        ) = _setup()
        executor = BlockingReviewerExecutor()
        first_workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=_resolver(policy, reviewer),
            executor=executor,
        )
        second_workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=_resolver(policy, reviewer),
            executor=executor,
        )

        first = asyncio.create_task(first_workflow.run_request(request.verification_id))
        await executor.started.wait()
        second = asyncio.create_task(second_workflow.run_request(request.verification_id))
        await asyncio.sleep(0)
        assert executor.calls == 1

        executor.release.set()
        first_result, second_result = await asyncio.gather(first, second)

        assert first_result.completion.state is CompletionState.ACCEPTED
        assert second_result.completion.state is CompletionState.ACCEPTED
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_cancellation_marks_unstaged_run_cancelled_and_retry_can_complete() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            reviewer,
            _verification,
            policy,
            _evidence,
            completion,
            runtime,
            request,
        ) = _setup()
        executor = CancelThenPassReviewerExecutor()
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=_resolver(policy, reviewer),
            executor=executor,
        )

        first = asyncio.create_task(workflow.run_request(request.verification_id))
        await executor.started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        runs = agents.service.repository.list_agent_runs()
        assert len(runs) == 1
        assert runs[0].status is AgentRunStatus.CANCELLED

        recovered = await workflow.run_request(request.verification_id)
        assert recovered.completion.state is CompletionState.ACCEPTED
        assert executor.calls == 2
        runs = agents.service.repository.list_agent_runs()
        assert len(runs) == 2
        assert runs[-1].status is AgentRunStatus.SUCCEEDED

    asyncio.run(scenario())


def test_staged_decision_reconciles_submission_failure_without_rerunning_reviewer() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            reviewer,
            _verification,
            policy,
            evidence,
            completion,
            runtime,
            request,
        ) = _setup()
        evidence.fail_validation_once = True
        evidence_id = new_id("artifact")

        class EvidenceReviewerExecutor:
            def __init__(self) -> None:
                self.calls = 0

            async def execute_review(self, *, request, agent_run):
                del request, agent_run
                self.calls += 1
                return ReviewerExecutionDecision(
                    outcome=VerificationOutcome.PASS,
                    evidence_artifact_ids=(evidence_id,),
                    findings=(),
                )

        executor = EvidenceReviewerExecutor()
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=_resolver(policy, reviewer),
            executor=executor,
        )

        with pytest.raises(ContractError) as exc_info:
            await workflow.run_request(request.verification_id)
        assert exc_info.value.code is ErrorCode.UNAVAILABLE
        runs = agents.service.repository.list_agent_runs()
        assert len(runs) == 1
        assert runs[0].status is AgentRunStatus.SUCCEEDED
        assert "automatic_reviewer_decision" in runs[0].telemetry
        assert executor.calls == 1

        recovered = await workflow.run_request(request.verification_id)
        assert recovered.completion.state is CompletionState.ACCEPTED
        assert recovered.latest.verification_result is not None
        assert recovered.latest.verification_result.evidence_artifact_ids == (evidence_id,)
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_reviewer_retry_budget_stops_repeated_failed_execution() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            reviewer,
            _verification,
            policy,
            _evidence,
            completion,
            runtime,
            request,
        ) = _setup()

        class FailingExecutor:
            def __init__(self) -> None:
                self.calls = 0

            async def execute_review(self, *, request, agent_run):
                del request, agent_run
                self.calls += 1
                raise RuntimeError("review backend unavailable")

        executor = FailingExecutor()
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=_resolver(policy, reviewer),
            executor=executor,
        )
        options = ReviewerRuntimeOptions(max_reviewer_attempts=2)

        for _ in range(2):
            with pytest.raises(RuntimeError, match="backend unavailable"):
                await workflow.run_request(request.verification_id, options=options)

        with pytest.raises(ContractError) as exc_info:
            await workflow.run_request(request.verification_id, options=options)
        assert exc_info.value.code is ErrorCode.UNAVAILABLE
        assert executor.calls == 2
        assert len(agents.service.repository.list_agent_runs()) == 2
        assert all(
            run.status is AgentRunStatus.FAILED
            for run in agents.service.repository.list_agent_runs()
        )

    asyncio.run(scenario())


def test_team_role_resolves_exact_reviewer_member_and_enforces_independence() -> None:
    async def scenario() -> None:
        (
            agents,
            producer,
            reviewer,
            _verification,
            policy,
            _evidence,
            completion,
            runtime,
            request,
        ) = _setup(independent=True)
        team = agents.service.create_team(
            AgentTeamProfile(
                name="Software Development Team",
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(
                            producer.agent_id,
                            producer.revision,
                        ),
                        role="developer",
                    ),
                    AgentTeamMember(
                        agent=AgentRevisionRef(
                            reviewer.agent_id,
                            reviewer.revision,
                        ),
                        role="reviewer",
                    ),
                ),
            ),
            owner_ref=OwnerRef(type="service", id="issue-711"),
        )
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=ConfiguredReviewerResolver(
                {
                    (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                        team_id=team.team_id,
                        team_revision=team.revision,
                        team_role="reviewer",
                    )
                }
            ),
            executor=QueueReviewerExecutor(VerificationOutcome.PASS),
        )

        result = await workflow.run_request(request.verification_id)

        assert result.completion.state is CompletionState.ACCEPTED
        assert result.latest.reviewer_run is not None
        assert result.latest.reviewer_run.agent.agent_id == reviewer.agent_id
        assert result.latest.reviewer_run.team is not None
        assert result.latest.reviewer_run.team.team_id == team.team_id
        assert result.latest.verification_result is not None
        assert result.latest.verification_result.verifier.read_only is True

    asyncio.run(scenario())


def test_needs_changes_starts_canonical_repair_before_fresh_reverification() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            reviewer,
            verification,
            policy,
            evidence,
            completion,
            runtime,
            request,
        ) = _setup(max_repairs=1)
        executor = QueueReviewerExecutor(
            VerificationOutcome.NEEDS_CHANGES,
            VerificationOutcome.PASS,
        )
        repair_runtime = FakeRepairRuntime(request.task_id)
        repair_executor = ReplacingRepairExecutor(evidence)
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=_resolver(policy, reviewer),
            executor=executor,
            repair_runtime=repair_runtime,  # type: ignore[arg-type]
            repair_executor=repair_executor,
        )

        result = await workflow.run_request(request.verification_id)

        assert result.completion.state is CompletionState.ACCEPTED
        assert len(result.cycles) == 2
        first, second = result.cycles
        assert first.verification_result is not None
        assert first.verification_result.outcome is VerificationOutcome.NEEDS_CHANGES
        assert first.repair_execution is not None
        assert second.verification_result is not None
        assert second.verification_result.outcome is VerificationOutcome.PASS
        assert first.request.subject != second.request.subject
        assert second.request.repair_attempt == 1
        assert second.request.run_id == first.repair_execution.run_id
        assert repair_runtime.calls == 1
        assert repair_executor.calls == 1
        assert executor.calls == 2
        assert len(agents.service.repository.list_agent_runs()) == 2
        assert verification.result_for(first.request.verification_id) is first.verification_result

    asyncio.run(scenario())


def test_missing_reviewer_configuration_fails_closed_before_agent_run() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            _reviewer,
            _verification,
            _policy,
            _evidence,
            completion,
            runtime,
            request,
        ) = _setup()
        workflow = AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=ConfiguredReviewerResolver({}),
            executor=QueueReviewerExecutor(VerificationOutcome.PASS),
        )

        with pytest.raises(ContractError) as exc_info:
            await workflow.run_request(request.verification_id)

        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert agents.service.repository.list_agent_runs() == ()
        assert completion.assess_task_completion(request.task_id).state is CompletionState.WAITING

    asyncio.run(scenario())


def test_existing_running_review_is_reconciled_without_duplicate_dispatch() -> None:
    async def scenario() -> None:
        (
            agents,
            _producer,
            reviewer,
            verification,
            policy,
            _evidence,
            completion,
            runtime,
            request,
        ) = _setup()
        bridge = ReviewerAgentRuntime(verification, agents)
        existing = await bridge.start_review(
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
            resolver=_resolver(policy, reviewer),
            executor=executor,
        )

        result = await workflow.run_request(request.verification_id)

        assert result.completion.state is CompletionState.WAITING
        assert result.latest.reviewer_run == existing
        assert result.latest.verification_result is None
        assert executor.calls == 0
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_partial_automatic_repair_configuration_is_rejected() -> None:
    (
        agents,
        _producer,
        _reviewer,
        _verification,
        _policy,
        evidence,
        completion,
        runtime,
        request,
    ) = _setup(max_repairs=1)

    with pytest.raises(ValueError, match="requires both"):
        AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=ConfiguredReviewerResolver({}),
            executor=QueueReviewerExecutor(VerificationOutcome.PASS),
            repair_runtime=FakeRepairRuntime(request.task_id),  # type: ignore[arg-type]
            repair_executor=None,
        )

    with pytest.raises(ValueError, match="requires both"):
        AutomaticReviewerWorkflow(
            runtime=runtime,
            completion=completion,
            agents=agents,
            resolver=ConfiguredReviewerResolver({}),
            executor=QueueReviewerExecutor(VerificationOutcome.PASS),
            repair_runtime=None,
            repair_executor=ReplacingRepairExecutor(evidence),
        )
