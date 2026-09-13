from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, cast

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    InMemoryCoordinatorRepository,
    StepCoordinationRecord,
)
from ai_multi_agent_platform.coordination.plan_step_coordinator import DurablePlanStepCoordinator
from ai_multi_agent_platform.deployment.reference_multi_agent import ReferenceMultiAgentPlanner
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, StepStatus, new_id
from ai_multi_agent_platform.planning.agent_matching import resolve_planning_steps
from ai_multi_agent_platform.planning.models import (
    PlanningAgentCandidate,
    PlanningInventory,
    PlanningRequest,
    PriorPlanSnapshot,
)


def _candidate(role: str, *, revision: int) -> PlanningAgentCandidate:
    return PlanningAgentCandidate(
        agent_id=new_id("agent"),
        revision=revision,
        role=role,
    )


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

        resolved_steps = {step.key: step for step in resolve_planning_steps(output.draft.steps, request)}
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
