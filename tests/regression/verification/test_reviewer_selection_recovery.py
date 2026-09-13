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
from ai_multi_agent_platform.domain import OwnerRef, new_id
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
from ai_multi_agent_platform.verification.agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ReviewerAssignment,
    ReviewerExecutionDecision,
)
from ai_multi_agent_platform.verification.persistence import SqliteVerificationService
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
    ReviewerRecoveryDisposition,
)


class Evidence:
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


class PassingReviewerExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_review(self, *, request, agent_run):
        del request, agent_run
        self.calls += 1
        return ReviewerExecutionDecision(outcome=VerificationOutcome.PASS)


def _profile() -> AgentProfile:
    return AgentProfile(
        name="Reviewer",
        role="reviewer",
        instructions=AgentInstructions(
            role=InstructionSource(content="Review the exact assigned output."),
        ),
    )


def _setup(tmp_path, reviewer_route):
    service = AgentService(InMemoryAgentRepository())
    reviewer = service.create_agent(
        _profile(),
        owner_ref=OwnerRef(type="service", id="issue-758-selection"),
    )
    agents = AgentRuntime(service)

    verification = SqliteVerificationService(tmp_path / "verification-selection.sqlite3")
    policy = verification.register_policy(
        VerificationPolicy(
            name="automatic-reviewer-selection-recovery",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            metadata={
                "automatic_reviewer": {
                    "enabled": True,
                    "subject_types": ["result"],
                    "stages": {"review": reviewer_route},
                }
            },
        )
    )
    task_id = new_id("task")
    producer_run_id = new_id("run")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:issue-758-selection",
    )
    evidence = Evidence(
        VerificationEvidenceContext(
            task_id=task_id,
            subject=subject,
            run_id=producer_run_id,
            project_id=None,
            capability_ids=(),
            producer=None,
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
        run_id=producer_run_id,
        result_id=subject.subject_id,
        correlation_id="issue-758-selection-review",
    )
    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                agent_id=reviewer.agent_id,
                agent_revision=reviewer.revision,
            )
        }
    )
    executor = PassingReviewerExecutor()
    workflow = AutomaticReviewerWorkflow(
        runtime=runtime,
        completion=completion,
        agents=agents,
        resolver=resolver,
        executor=executor,
    )
    return agents, reviewer, verification, completion, request, workflow, executor


def _persist_selection(agents, run, selection):
    context = dict(run.verification_context)
    context["reviewer_selection"] = selection
    updated = replace(run, verification_context=context)
    agents.service.repository.update_agent_run(updated)
    return updated


def test_exact_assignment_selection_provenance_survives_startup_recovery(tmp_path) -> None:
    async def scenario() -> None:
        agents, reviewer, verification, completion, request, workflow, executor = _setup(
            tmp_path,
            {
                "agent_id": "configured-reviewer",
                "agent_revision": 7,
            },
        )
        completed = await workflow.run_request(request.verification_id)
        assert completed.completion.state is CompletionState.ACCEPTED
        run = agents.service.repository.list_agent_runs()[0]
        _persist_selection(
            agents,
            run,
            {
                "schema": "reviewer-selection-v1",
                "mode": "exact_assignment",
                "selected_agent_id": reviewer.agent_id,
                "selected_agent_revision": reviewer.revision,
                "selected_team_id": None,
                "selected_team_revision": None,
                "configured_agent_id": "configured-reviewer",
                "configured_agent_revision": 7,
                "configured_team_id": None,
                "configured_team_revision": None,
                "configured_team_role": None,
            },
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        recovered = await recovery.reconcile_startup()

        assert recovered[0].disposition is ReviewerRecoveryDisposition.ALREADY_COMPLETED
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_scoped_discovery_selection_provenance_survives_startup_recovery(tmp_path) -> None:
    async def scenario() -> None:
        agents, reviewer, verification, completion, request, workflow, executor = _setup(
            tmp_path,
            {
                "candidate_agent_ids": ["agent-a", "agent-b"],
                "candidate_team_ids": ["team-a"],
                "reviewer_role": "reviewer_tester",
                "required_capability_ids": ["capability:file-read"],
            },
        )
        completed = await workflow.run_request(request.verification_id)
        assert completed.completion.state is CompletionState.ACCEPTED
        run = agents.service.repository.list_agent_runs()[0]
        _persist_selection(
            agents,
            run,
            {
                "schema": "reviewer-selection-v1",
                "mode": "scoped_discovery",
                "selected_agent_id": reviewer.agent_id,
                "selected_agent_revision": reviewer.revision,
                "selected_team_id": None,
                "selected_team_revision": None,
                "candidate_agent_ids": ["agent-a", "agent-b"],
                "candidate_team_ids": ["team-a"],
                "reviewer_role": "reviewer_tester",
                "required_capability_ids": ["capability:file-read"],
            },
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        recovered = await recovery.reconcile_startup()

        assert recovered[0].disposition is ReviewerRecoveryDisposition.ALREADY_COMPLETED
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())


def test_tampered_reviewer_selection_provenance_fails_closed(tmp_path) -> None:
    async def scenario() -> None:
        agents, reviewer, verification, completion, request, workflow, executor = _setup(
            tmp_path,
            {
                "agent_id": "configured-reviewer",
                "agent_revision": 7,
            },
        )
        completed = await workflow.run_request(request.verification_id)
        assert completed.completion.state is CompletionState.ACCEPTED
        run = agents.service.repository.list_agent_runs()[0]
        _persist_selection(
            agents,
            run,
            {
                "schema": "reviewer-selection-v1",
                "mode": "exact_assignment",
                "selected_agent_id": f"{reviewer.agent_id}-tampered",
                "selected_agent_revision": reviewer.revision,
                "selected_team_id": None,
                "selected_team_revision": None,
                "configured_agent_id": "configured-reviewer",
                "configured_agent_revision": 7,
                "configured_team_id": None,
                "configured_team_revision": None,
                "configured_team_role": None,
            },
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        recovered = await recovery.reconcile_startup()

        assert recovered[0].disposition is ReviewerRecoveryDisposition.BLOCKED
        assert "exact canonical Verification binding" in (recovered[0].reason or "")
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED
        assert executor.calls == 1
        assert len(agents.service.repository.list_agent_runs()) == 1

    asyncio.run(scenario())
