from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_multi_agent_platform.automation import (
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerType,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.approval_portability_composition import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.goals import (
    GOAL_OBSERVATION_PAYLOAD_KEY,
    GoalCriterionKind,
    GoalProgress,
    GoalStatus,
    GoalTaskState,
    ObservationPolicy,
    SuccessCriterion,
    TaskGenerationPolicy,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

_OWNER_ID = "goal-completion-owner"
_OWNER = OwnerRef(type="user", id=_OWNER_ID)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _stack() -> tuple[ControlPlane, PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=FakeAuthorizationProvider(),
    )
    return control_plane, kernel, repository


def _context(key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request:{key}",
        correlation_id=f"correlation:{key}",
        actor=ActorContext(
            principal_ref=f"user:{_OWNER_ID}",
            owner_type="user",
            owner_id=_OWNER_ID,
        ),
        idempotency_key=key,
    )


def test_direct_review_cannot_self_promote_verified_evidence_and_binds_human_acceptance() -> None:
    async def scenario() -> None:
        control_plane, _, _ = _stack()
        metric = await control_plane.goals.create_goal(
            idempotency_key="create-metric",
            title="Metric Goal",
            objective="Reach an explicit metric",
            owner_ref=_OWNER,
            success_criteria=(
                SuccessCriterion(
                    criterion_id="quality",
                    kind=GoalCriterionKind.METRIC,
                    description="Quality reaches 90",
                    operator="gte",
                    target=90,
                ),
            ),
            task_generation_policy=TaskGenerationPolicy(enabled=False),
        )
        await control_plane.goals.activate_goal(
            goal_id=metric.goal_id,
            idempotency_key="activate-metric",
        )

        with pytest.raises(ContractError) as raised:
            await control_plane.execute_command(
                _context("untrusted-metric-review"),
                "goal.review",
                metric.goal_id,
                {
                    "expected_revision": 1,
                    "trigger_ref": "manual:test",
                    "evidence": [
                        {
                            "criterion_id": "quality",
                            "kind": "metric",
                            "value": 100,
                            "source_ref": "client:claim",
                            "verified": True,
                        }
                    ],
                },
            )
        assert raised.value.code is ErrorCode.INVALID_REQUEST
        unchanged = await control_plane.goals.get_goal(metric.goal_id)
        assert unchanged.evidence == ()
        assert unchanged.reviews == ()

        acceptance = await control_plane.goals.create_goal(
            idempotency_key="create-acceptance",
            title="Human acceptance Goal",
            objective="Require an authenticated human acceptance",
            owner_ref=_OWNER,
            success_criteria=(
                SuccessCriterion(
                    criterion_id="accepted",
                    kind=GoalCriterionKind.HUMAN_ACCEPTANCE,
                    description="Authenticated user accepts the outcome",
                    target=True,
                ),
            ),
            task_generation_policy=TaskGenerationPolicy(enabled=False),
        )
        await control_plane.goals.activate_goal(
            goal_id=acceptance.goal_id,
            idempotency_key="activate-acceptance",
        )
        result = await control_plane.execute_command(
            _context("human-acceptance-review"),
            "goal.review",
            acceptance.goal_id,
            {
                "expected_revision": 1,
                "trigger_ref": "manual:test",
                "evidence": [
                    {
                        "criterion_id": "accepted",
                        "kind": "human_acceptance",
                        "value": True,
                    }
                ],
            },
        )
        assert result["status"] == GoalStatus.SATISFIED.value
        accepted = await control_plane.goals.get_goal(acceptance.goal_id)
        assert accepted.evidence[0].verified is True
        assert accepted.evidence[0].actor_ref == f"user:{_OWNER_ID}"
        assert accepted.evidence[0].source_ref == f"human-acceptance:user:{_OWNER_ID}"

    _run(scenario())


def test_proposal_required_fails_closed_before_direct_task_generation() -> None:
    async def scenario() -> None:
        control_plane, _, _ = _stack()
        created = await control_plane.goals.create_goal(
            idempotency_key="create-proposal-goal",
            title="Governed Goal",
            objective="Require Proposal mediation before work",
            owner_ref=_OWNER,
            success_criteria=(
                SuccessCriterion(
                    criterion_id="quality",
                    kind=GoalCriterionKind.METRIC,
                    description="Quality reaches 90",
                    operator="gte",
                    target=90,
                ),
            ),
            task_generation_policy=TaskGenerationPolicy(
                enabled=True,
                proposal_required=True,
            ),
        )
        active = await control_plane.goals.activate_goal(
            goal_id=created.goal_id,
            idempotency_key="activate-proposal-goal",
        )
        reviewed = await control_plane.goals.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-proposal-goal",
            expected_revision=1,
            trigger_ref="manual:test",
        )
        assert reviewed.status is GoalStatus.PAUSED
        assert reviewed.progress is GoalProgress.BLOCKED
        assert reviewed.active_task_ids == ()
        assert reviewed.reviews[-1].generated_task_ids == ()
        assert "Proposal/Specification" in reviewed.reviews[-1].decision_reason

    _run(scenario())


def test_task_terminal_event_reconciles_before_event_driven_goal_review() -> None:
    async def scenario() -> None:
        control_plane, kernel, _ = _stack()
        goal_id = new_id("goal")
        automation = await control_plane.automation_service.create_automation(
            name="Re-evaluate Goal after Task success",
            description="Use the canonical task.succeeded event",
            identity=IdentityContext(
                principal_ref=f"user:{_OWNER_ID}",
                owner_type="user",
                owner_id=_OWNER_ID,
            ),
            trigger=TriggerDefinition(
                type=TriggerType.PLATFORM_EVENT,
                event_type="task.succeeded",
            ),
            task_template=TaskTemplate(
                title="Review Goal after Task success",
                objective="Re-evaluate the linked Task criterion",
                payload={
                    GOAL_OBSERVATION_PAYLOAD_KEY: {
                        "goal_id": goal_id,
                        "goal_revision": 1,
                    }
                },
            ),
        )
        created = await control_plane.goals.create_goal(
            idempotency_key="create-linked-task-goal",
            goal_id=goal_id,
            title="Finish linked work",
            objective="Satisfy the Goal when one linked Task succeeds",
            owner_ref=_OWNER,
            success_criteria=(
                SuccessCriterion(
                    criterion_id="linked-work",
                    kind=GoalCriterionKind.LINKED_TASKS,
                    description="At least one linked Task succeeds",
                    target=1,
                ),
            ),
            observation_policy=ObservationPolicy(
                automation_id=automation.id,
                event_types=("task.succeeded",),
            ),
        )
        active = await control_plane.goals.activate_goal(
            goal_id=created.goal_id,
            idempotency_key="activate-linked-task-goal",
        )
        working = await control_plane.goals.review_goal(
            goal_id=active.goal_id,
            idempotency_key="seed-linked-task-work",
            expected_revision=1,
            trigger_ref="manual:seed",
        )
        assert len(working.active_task_ids) == 1
        task_id = working.active_task_ids[0]

        await kernel.ready_task(idempotency_key="goal-task-ready", task_id=task_id)
        run = await kernel.create_run(idempotency_key="goal-task-run", task_id=task_id)
        await kernel.start_run(
            idempotency_key="goal-task-start",
            task_id=task_id,
            run_id=run.run_id,
        )
        await kernel.record_run_outcome(
            idempotency_key="goal-task-success",
            task_id=task_id,
            run_id=run.run_id,
            status=RunStatus.SUCCEEDED,
        )

        before_runtime = await control_plane.goals.get_goal(goal_id)
        assert before_runtime.linked_tasks[0].task_state is GoalTaskState.ACTIVE

        first_tick = await control_plane.run_automation_runtime_once()
        after_runtime = await control_plane.goals.get_goal(goal_id)
        assert first_tick.failed_event_ids == ()
        assert after_runtime.linked_tasks[0].task_state is GoalTaskState.SUCCEEDED
        assert after_runtime.status is GoalStatus.SATISFIED
        assert len(after_runtime.reviews) == 2
        assert after_runtime.reviews[-1].generated_task_ids == ()

        await control_plane.run_automation_runtime_once()
        replayed = await control_plane.goals.get_goal(goal_id)
        assert len(replayed.reviews) == 2
        assert replayed.consecutive_failed_cycles == 0

    _run(scenario())
