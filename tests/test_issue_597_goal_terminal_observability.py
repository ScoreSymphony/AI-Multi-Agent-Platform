from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.goals import (
    AutonomyPolicy,
    EventSourcedGoalRepository,
    GoalCriterionKind,
    GoalEvidence,
    GoalProgress,
    GoalService,
    GoalState,
    GoalStatus,
    SuccessCriterion,
    TaskGenerationPolicy,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository

OWNER = OwnerRef(type="user", id="goal-observability-test-user")
METRIC = SuccessCriterion(
    criterion_id="quality",
    kind=GoalCriterionKind.METRIC,
    description="Quality reaches 90",
    operator="gte",
    target=90,
)


@dataclass
class RecordingTaskCreator:
    task_ids: list[str]

    def __init__(self) -> None:
        self.task_ids = []

    async def create_goal_task(
        self,
        *,
        goal: GoalState,
        review_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> str:
        del goal, review_id, idempotency_key
        self.task_ids.append(task_id)
        return task_id


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _active_goal(
    service: GoalService,
    *,
    key: str,
    task_generation_policy: TaskGenerationPolicy | None = None,
    autonomy_policy: AutonomyPolicy | None = None,
) -> GoalState:
    created = await service.create_goal(
        idempotency_key=f"{key}:create",
        title="Maintain quality",
        objective="Maintain the configured quality target",
        owner_ref=OWNER,
        success_criteria=(METRIC,),
        task_generation_policy=task_generation_policy,
        autonomy_policy=autonomy_policy,
    )
    return await service.activate_goal(
        goal_id=created.goal_id,
        idempotency_key=f"{key}:activate",
    )


def test_explicit_failure_is_terminal_reasoned_and_reopen_requires_revision() -> None:
    async def scenario() -> None:
        events = InMemoryKernelRepository()
        service = GoalService(EventSourcedGoalRepository(events))
        active = await _active_goal(service, key="failure")

        failed = await service.fail_goal(
            goal_id=active.goal_id,
            idempotency_key="failure:terminal",
            reason="Required source can no longer be recovered",
        )
        assert failed.status is GoalStatus.FAILED
        assert failed.progress is GoalProgress.DEGRADED
        assert failed.terminal_reason == "Required source can no longer be recovered"
        assert [event.event_type for event in await events.read_events(active.goal_id)][-1] == (
            "goal.failed"
        )

        with pytest.raises(ContractError):
            await service.review_goal(
                goal_id=active.goal_id,
                idempotency_key="failure:review-after-terminal",
                expected_revision=1,
                trigger_ref="manual:test",
            )
        with pytest.raises(ContractError):
            await service.revise_goal(
                goal_id=active.goal_id,
                idempotency_key="failure:revise-without-reopen",
                expected_revision=1,
                objective="Try a new path",
            )

        reopened = await service.revise_goal(
            goal_id=active.goal_id,
            idempotency_key="failure:reopen",
            expected_revision=1,
            objective="Try a new path",
            reopen_terminal=True,
        )
        assert reopened.revision == 2
        assert reopened.status is GoalStatus.ACTIVE
        assert reopened.progress is GoalProgress.MONITORING
        assert reopened.terminal_reason is None

    _run(scenario())


def test_satisfied_review_emits_started_progress_work_not_needed_outcome_and_completed() -> None:
    async def scenario() -> None:
        events = InMemoryKernelRepository()
        service = GoalService(EventSourcedGoalRepository(events))
        active = await _active_goal(
            service,
            key="satisfied-events",
            task_generation_policy=TaskGenerationPolicy(enabled=False),
        )

        satisfied = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="satisfied-events:review",
            expected_revision=1,
            trigger_ref="manual:test",
            evidence=(
                GoalEvidence(
                    criterion_id="quality",
                    kind="metric",
                    value=95,
                    source_ref="evaluation:quality",
                    verified=True,
                ),
            ),
        )
        assert satisfied.status is GoalStatus.SATISFIED
        event_types = [event.event_type for event in await events.read_events(active.goal_id)]
        assert event_types[-5:] == [
            "goal.review_started",
            "goal.progress_criterion_changed",
            "goal.work_not_needed",
            "goal.satisfied",
            "goal.review_completed",
        ]

        before_replay = len(event_types)
        replay = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="satisfied-events:review",
            expected_revision=1,
            trigger_ref="manual:test",
        )
        assert replay.status is GoalStatus.SATISFIED
        assert len(await events.read_events(active.goal_id)) == before_replay

    _run(scenario())


def test_task_generation_and_human_checkpoint_emit_domain_review_events() -> None:
    async def scenario() -> None:
        task_events = InMemoryKernelRepository()
        creator = RecordingTaskCreator()
        task_service = GoalService(
            EventSourcedGoalRepository(task_events),
            task_creator=creator,
        )
        active = await _active_goal(task_service, key="task-events")
        reviewed = await task_service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="task-events:review",
            expected_revision=1,
            trigger_ref="automation-delivery:test",
        )
        assert reviewed.progress is GoalProgress.ACTIVE_WORK
        task_event_types = [
            event.event_type for event in await task_events.read_events(active.goal_id)
        ]
        assert task_event_types[-4:] == [
            "goal.review_started",
            "goal.progress_criterion_changed",
            "goal.task_generated",
            "goal.review_completed",
        ]

        blocked_events = InMemoryKernelRepository()
        blocked_service = GoalService(EventSourcedGoalRepository(blocked_events))
        blocked_active = await _active_goal(
            blocked_service,
            key="blocked-events",
            autonomy_policy=AutonomyPolicy(human_checkpoint_required=True),
        )
        blocked = await blocked_service.review_goal(
            goal_id=blocked_active.goal_id,
            idempotency_key="blocked-events:review",
            expected_revision=1,
            trigger_ref="manual:test",
        )
        assert blocked.status is GoalStatus.PAUSED
        assert blocked.progress is GoalProgress.BLOCKED
        blocked_event_types = [
            event.event_type for event in await blocked_events.read_events(blocked_active.goal_id)
        ]
        assert blocked_event_types[-4:] == [
            "goal.review_started",
            "goal.progress_criterion_changed",
            "goal.blocked",
            "goal.review_completed",
        ]

    _run(scenario())
