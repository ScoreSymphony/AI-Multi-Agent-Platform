from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus, OperationContext
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    InMemoryCoordinatorRepository,
    StepCoordinationRecord,
)
from ai_multi_agent_platform.coordination.plan_step_coordinator import DurablePlanStepCoordinator
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.reference_multi_agent import ReferenceMultiAgentPlanner
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, StepStatus, TaskStatus, new_id
from ai_multi_agent_platform.handoffs import HandoffSourceKind
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
)
from ai_multi_agent_platform.planning import ProposalStatus
from ai_multi_agent_platform.planning.agent_matching import resolve_planning_steps
from ai_multi_agent_platform.planning.models import (
    PlanningAgentCandidate,
    PlanningInventory,
    PlanningRequest,
    PriorPlanSnapshot,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def _candidate(role: str, *, revision: int) -> PlanningAgentCandidate:
    return PlanningAgentCandidate(
        agent_id=new_id("agent"),
        revision=revision,
        role=role,
    )


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(
                content=f"Act as the canonical {role} for the reference multi-agent task.",
                version="1",
            )
        ),
    )


def _principal(agent: AgentRevisionRef) -> str:
    return f"agent:{agent.agent_id}@{agent.revision}"


def _install_local_model(deployment: Any) -> FakeModelProvider:
    provider = FakeModelProvider()
    deployment.models.register_provider(provider)
    deployment.models.register_model(
        ModelConfiguration(
            config_id="model-issue-889-reference-golden-path",
            display_name="Issue 889 reference golden-path model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )
    return provider


def test_reference_multi_agent_planner_builds_exact_parallel_fan_in_dag() -> None:
    async def scenario() -> None:
        research = _candidate("researcher", revision=3)
        execution = _candidate("developer", revision=5)
        review = _candidate("reviewer", revision=7)
        reused_step = new_id("step")
        request = PlanningRequest(
            task_id=new_id("task"),
            task_revision=4,
            objective="Research the repository, implement the change, and verify the result.",
            context=OperationContext(correlation_id="issue-889-reference-plan"),
            inventory=PlanningInventory(agents=(review, execution, research)),
            prior_plan=PriorPlanSnapshot(
                plan_id=new_id("plan"),
                revision=2,
                completed_step_ids=(reused_step,),
            ),
        )

        output = await ReferenceMultiAgentPlanner().propose(request)
        draft_steps = {step.key: step for step in output.draft.steps}

        assert tuple(step.key for step in output.draft.steps) == (
            "research",
            "approach",
            "execute",
            "review",
        )
        assert draft_steps["research"].depends_on == ()
        assert draft_steps["approach"].depends_on == ()
        assert draft_steps["execute"].depends_on == ("research", "approach")
        assert draft_steps["review"].depends_on == ("execute",)
        assert draft_steps["research"].assignment is not None
        assert draft_steps["research"].assignment.role_requirement == "researcher"
        assert draft_steps["execute"].assignment is not None
        assert draft_steps["execute"].assignment.role_requirement == "developer"
        assert draft_steps["review"].assignment is not None
        assert draft_steps["review"].assignment.role_requirement == "reviewer"
        assert draft_steps["execute"].reuse_step_ids == (reused_step,)

        resolved_steps = {
            step.key: step for step in resolve_planning_steps(output.draft.steps, request)
        }
        assert resolved_steps["research"].assignment is not None
        assert resolved_steps["research"].assignment.agent_id == research.agent_id
        assert resolved_steps["research"].assignment.agent_revision == 3
        assert resolved_steps["execute"].assignment is not None
        assert resolved_steps["execute"].assignment.agent_id == execution.agent_id
        assert resolved_steps["execute"].assignment.agent_revision == 5
        assert resolved_steps["review"].assignment is not None
        assert resolved_steps["review"].assignment.agent_id == review.agent_id
        assert resolved_steps["review"].assignment.agent_revision == 7

    asyncio.run(scenario())


class _ParallelProbeCoordinator(DurablePlanStepCoordinator):
    def __init__(self, repository: InMemoryCoordinatorRepository) -> None:
        super().__init__(
            repository=repository,
            kernel=cast(Any, object()),
            coordinator_id="issue-889-parallel-probe",
        )
        self._seen: set[str] = set()
        self._active = 0
        self.max_active = 0
        self._both_active = asyncio.Event()

    async def _start_attempt(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        del record, now
        if step.id in self._seen:
            return False
        self._seen.add(step.id)
        self._active += 1
        self.max_active = max(self.max_active, self._active)
        if self._active == 2:
            self._both_active.set()
        await asyncio.wait_for(self._both_active.wait(), timeout=1.0)
        await asyncio.sleep(0)
        self._active -= 1
        return True

    async def _aggregate_task(self, plan_id: str) -> None:
        del plan_id


def test_coordinator_dispatches_one_ready_frontier_concurrently() -> None:
    async def scenario() -> None:
        owner = OwnerRef(type="user", id="issue-889-user")
        task_id = new_id("task")
        project_id = new_id("project")
        plan = Plan(
            task_id=task_id,
            owner_ref=owner,
            project_id=project_id,
            active=True,
        )
        steps = tuple(
            Step(
                id=new_id("step"),
                plan_id=plan.id,
                title=f"parallel-{index}",
                owner_ref=owner,
                project_id=project_id,
                status=StepStatus.READY,
            )
            for index in range(2)
        )
        records = tuple(
            StepCoordinationRecord(
                task_id=task_id,
                plan_id=plan.id,
                plan_revision=plan.revision,
                step_id=step.id,
                phase=CoordinationPhase.READY,
            )
            for step in steps
        )
        repository = InMemoryCoordinatorRepository()
        repository.create_plan(plan, steps, records)
        coordinator = _ParallelProbeCoordinator(repository)

        await coordinator.advance(plan.id)

        assert coordinator.max_active == 2

    asyncio.run(scenario())


def test_public_single_node_runs_reference_multi_agent_golden_path_to_completion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        model_provider = _install_local_model(deployment)
        admin = deployment.bootstrap_admin(
            "issue-889-admin",
            "correct horse battery staple for issue 889",
        )
        owner = OwnerRef(type="user", id=admin.user_id)
        revisions = {
            "researcher": deployment.agents.create_agent(
                _profile("Issue 889 Research Agent", "researcher"),
                owner_ref=owner,
            ),
            "developer": deployment.agents.create_agent(
                _profile("Issue 889 Execution Agent", "developer"),
                owner_ref=owner,
            ),
            "reviewer": deployment.agents.create_agent(
                _profile("Issue 889 Review Agent", "reviewer"),
                owner_ref=owner,
            ),
        }
        agent_refs = {
            role: AgentRevisionRef(revision.agent_id, revision.revision)
            for role, revision in revisions.items()
        }
        for agent in agent_refs.values():
            deployment.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=_principal(agent),
                    actor_types=frozenset({ActorType.AGENT}),
                    allowed_actions=frozenset(
                        {
                            AuthorizationAction.READ,
                            AuthorizationAction.RESULT_READ,
                        }
                    ),
                    resource_types=frozenset(
                        {
                            ResourceType.ARTIFACT,
                            ResourceType.GENERIC,
                        }
                    ),
                )
            )

        task = await deployment.kernel.create_task(
            idempotency_key="issue-889:golden-path:create",
            title="Complete the reference multi-agent golden path",
            objective=(
                "Research the requested change, prepare an execution approach, produce the "
                "result, and review the exact produced result."
            ),
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="issue-889:golden-path:ready",
            task_id=task.task_id,
        )

        proposal = await deployment.planning.propose(
            task_id=task.task_id,
            idempotency_key="issue-889:golden-path:propose",
        )
        assert proposal.status is ProposalStatus.VALIDATED
        assert tuple(step.key for step in proposal.proposal.steps) == (
            "research",
            "approach",
            "execute",
            "review",
        )
        expected_roles = ("researcher", "developer", "developer", "reviewer")
        for draft, role in zip(proposal.proposal.steps, expected_roles, strict=True):
            assert draft.assignment is not None
            assert draft.assignment.agent_id == agent_refs[role].agent_id
            assert draft.assignment.agent_revision == agent_refs[role].revision

        activated = await deployment.planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key="issue-889:golden-path:activate",
            actor=ActorIdentity(actor_id=admin.user_id, actor_type=ActorType.HUMAN),
        )
        assert activated.status is ProposalStatus.ACTIVATED
        assert activated.activation_plan_id is not None

        completed_task = await deployment.kernel.get_task(task.task_id)
        assert completed_task.status is TaskStatus.SUCCEEDED
        state = deployment.coordination_repository.get_plan(activated.activation_plan_id)
        assert len(state.steps) == 4
        assert all(step.status is StepStatus.SUCCEEDED for step in state.steps)

        step_runs: dict[str, str] = {}
        for run_id in completed_task.run_ids:
            run = await deployment.kernel.get_run(task.task_id, run_id)
            if run.run.subject_type == "step":
                step_runs[run.run.subject_id] = run.run_id
        assert set(step_runs) == {step.id for step in state.steps}

        drafts_by_title = {draft.title: draft for draft in proposal.proposal.steps}
        steps_by_title = {step.title: step for step in state.steps}
        assert set(steps_by_title) == set(drafts_by_title)
        for title, step in steps_by_title.items():
            draft = drafts_by_title[title]
            assert draft.assignment is not None
            agent_runs = deployment.agents.repository.list_agent_runs(step_runs[step.id])
            assert len(agent_runs) == 1
            assert agent_runs[0].agent.agent_id == draft.assignment.agent_id
            assert agent_runs[0].agent.revision == draft.assignment.agent_revision
            assert len(agent_runs[0].result_ids) == 1

        research_step = steps_by_title["Gather authoritative evidence"]
        approach_step = steps_by_title["Prepare an independent execution approach"]
        execute_step = steps_by_title["Produce the requested result"]
        review_step = steps_by_title["Review the exact produced result"]
        execute_handoffs = tuple(
            handoff
            for handoff in deployment.handoffs.service.list_handoffs_for_step(execute_step.id)
            if handoff.content.consumer_step_id == execute_step.id
        )
        assert len(execute_handoffs) == 2
        assert {handoff.content.producer_step_id for handoff in execute_handoffs} == {
            research_step.id,
            approach_step.id,
        }
        assert all(
            source.kind is HandoffSourceKind.RESULT
            for handoff in execute_handoffs
            for source in handoff.content.source_refs
        )
        execute_consumptions = deployment.handoffs.repository.list_consumptions_for_run(
            step_runs[execute_step.id]
        )
        assert {item.handoff_id for item in execute_consumptions} == {
            handoff.handoff_id for handoff in execute_handoffs
        }

        review_handoffs = tuple(
            handoff
            for handoff in deployment.handoffs.service.list_handoffs_for_step(review_step.id)
            if handoff.content.consumer_step_id == review_step.id
        )
        assert len(review_handoffs) == 1
        assert review_handoffs[0].content.producer_step_id == execute_step.id
        review_consumptions = deployment.handoffs.repository.list_consumptions_for_run(
            step_runs[review_step.id]
        )
        assert {item.handoff_id for item in review_consumptions} == {review_handoffs[0].handoff_id}

        assert len(model_provider.calls) == 4
        assert all(run_id.startswith("run_") for run_id in step_runs.values())
        assert all(step.id.startswith("step_") for step in state.steps)
        assert activated.activation_plan_id.startswith("plan_")
        assert completed_task.task_id.startswith("task_")

    asyncio.run(scenario())
