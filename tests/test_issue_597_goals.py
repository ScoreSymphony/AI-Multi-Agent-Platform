from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.goals import (
    AutonomyPolicy,
    EventSourcedGoalRepository,
    GoalCriterionKind,
    GoalEvidence,
    GoalProgress,
    GoalService,
    GoalState,
    GoalStatus,
    GoalTaskState,
    SuccessCriterion,
    TaskGenerationPolicy,
)
from ai_multi_agent_platform.goals.repository import GoalRepository
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, SqliteKernelRepository
from ai_multi_agent_platform.kernel.repository import CommandRecord

OWNER = OwnerRef(type="user", id="goal-test-user")
METRIC = SuccessCriterion(
    criterion_id="coverage",
    kind=GoalCriterionKind.METRIC,
    description="Coverage reaches 90 percent",
    operator="gte",
    target=90,
)
HUMAN = SuccessCriterion(
    criterion_id="acceptance",
    kind=GoalCriterionKind.HUMAN_ACCEPTANCE,
    description="A human explicitly accepts the result",
)
LINKED_TASK = SuccessCriterion(
    criterion_id="linked-work",
    kind=GoalCriterionKind.LINKED_TASKS,
    description="At least one linked Task succeeds",
    target=1,
)


@dataclass
class RecordingTaskCreator:
    calls: list[tuple[str, int, str]]
    created: set[str]

    def __init__(self) -> None:
        self.calls = []
        self.created = set()

    async def create_goal_task(
        self,
        *,
        goal: GoalState,
        review_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> str:
        del review_id
        self.calls.append((task_id, goal.revision, idempotency_key))
        self.created.add(task_id)
        return task_id


class FailOnceCommitRepository(GoalRepository):
    def __init__(self, inner: GoalRepository) -> None:
        self.inner = inner
        self.fail_review_once = True

    async def get_goal(self, goal_id: str) -> GoalState:
        return await self.inner.get_goal(goal_id)

    async def get_goal_revision(self, goal_id: str, revision: int) -> GoalState:
        return await self.inner.get_goal_revision(goal_id, revision)

    async def list_goals(self) -> tuple[GoalState, ...]:
        return await self.inner.list_goals()

    async def find_command(
        self, scope: str, idempotency_key: str, operation: str
    ) -> CommandRecord | None:
        return await self.inner.find_command(scope, idempotency_key, operation)

    async def commit_snapshot(self, **kwargs: Any) -> GoalState:
        if kwargs["operation"] == "goal.review" and self.fail_review_once:
            self.fail_review_once = False
            raise RuntimeError("simulated crash after Task creation before Goal commit")
        return await self.inner.commit_snapshot(**kwargs)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _created_active(
    *,
    service: GoalService,
    criterion: SuccessCriterion = METRIC,
    key: str = "create",
    autonomy: AutonomyPolicy | None = None,
    task_generation: TaskGenerationPolicy | None = None,
) -> GoalState:
    state = await service.create_goal(
        idempotency_key=key,
        title="Keep quality healthy",
        objective="Maintain explicit measurable quality",
        owner_ref=OWNER,
        success_criteria=(criterion,),
        autonomy_policy=autonomy,
        task_generation_policy=task_generation,
    )
    return await service.activate_goal(
        goal_id=state.goal_id,
        idempotency_key=f"{key}:activate",
    )


def test_create_activate_and_deterministic_metric_satisfaction() -> None:
    async def scenario() -> None:
        service = GoalService(EventSourcedGoalRepository(InMemoryKernelRepository()))
        active = await _created_active(
            service=service,
            task_generation=TaskGenerationPolicy(enabled=False),
        )
        assert active.status is GoalStatus.ACTIVE
        satisfied = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-1",
            expected_revision=1,
            trigger_ref="manual:test",
            evidence=(
                GoalEvidence(
                    criterion_id="coverage",
                    kind="metric",
                    value=93,
                    source_ref="evaluation:coverage",
                    verified=True,
                ),
            ),
        )
        assert satisfied.status is GoalStatus.SATISFIED
        assert satisfied.progress is GoalProgress.SATISFIED
        with pytest.raises(ContractError):
            await service.review_goal(
                goal_id=active.goal_id,
                idempotency_key="review-after-satisfied",
                expected_revision=1,
                trigger_ref="manual:test",
            )

    _run(scenario())


def test_human_acceptance_requires_verified_human_evidence() -> None:
    async def scenario() -> None:
        service = GoalService(EventSourcedGoalRepository(InMemoryKernelRepository()))
        active = await _created_active(
            service=service,
            criterion=HUMAN,
            task_generation=TaskGenerationPolicy(enabled=False),
        )
        waiting = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-unverified",
            expected_revision=1,
            trigger_ref="manual:test",
            evidence=(
                GoalEvidence(
                    criterion_id="acceptance",
                    kind="agent_self_report",
                    value=True,
                    source_ref="agent:claim",
                    verified=False,
                ),
            ),
        )
        assert waiting.status is GoalStatus.WAITING
        accepted = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-human",
            expected_revision=1,
            trigger_ref="manual:test",
            evidence=(
                GoalEvidence(
                    criterion_id="acceptance",
                    kind="human_acceptance",
                    value=True,
                    source_ref="approval:human",
                    verified=True,
                ),
            ),
        )
        assert accepted.status is GoalStatus.SATISFIED

    _run(scenario())


def test_goal_generates_one_deduplicated_task_and_links_exact_revision() -> None:
    async def scenario() -> None:
        creator = RecordingTaskCreator()
        repository = EventSourcedGoalRepository(InMemoryKernelRepository())
        service = GoalService(repository, task_creator=creator)
        active = await _created_active(service=service)
        first = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-generate",
            expected_revision=1,
            trigger_ref="automation-delivery:1",
        )
        replay = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-generate",
            expected_revision=1,
            trigger_ref="automation-delivery:1",
        )
        assert first.active_task_ids == replay.active_task_ids
        assert len(first.active_task_ids) == 1
        assert len(creator.created) == 1
        link = first.linked_tasks[0]
        assert link.goal_revision == 1
        assert link.review_id == first.reviews[-1].review_id

    _run(scenario())


def test_linked_task_success_can_satisfy_goal() -> None:
    async def scenario() -> None:
        creator = RecordingTaskCreator()
        service = GoalService(
            EventSourcedGoalRepository(InMemoryKernelRepository()),
            task_creator=creator,
        )
        active = await _created_active(service=service, criterion=LINKED_TASK)
        working = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-work",
            expected_revision=1,
            trigger_ref="manual:test",
        )
        task_id = working.active_task_ids[0]
        await service.record_task_outcome(
            goal_id=active.goal_id,
            task_id=task_id,
            task_state=GoalTaskState.SUCCEEDED,
            idempotency_key="task-success",
        )
        satisfied = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-after-success",
            expected_revision=1,
            trigger_ref="task-terminal:event",
        )
        assert satisfied.status is GoalStatus.SATISFIED

    _run(scenario())


def test_failed_cycles_generate_bounded_followup_then_escalate() -> None:
    async def scenario() -> None:
        creator = RecordingTaskCreator()
        service = GoalService(
            EventSourcedGoalRepository(InMemoryKernelRepository()),
            task_creator=creator,
        )
        active = await _created_active(
            service=service,
            autonomy=AutonomyPolicy(max_tasks_per_review=1, max_consecutive_failed_cycles=2),
        )
        first = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="cycle-1",
            expected_revision=1,
            trigger_ref="manual:test",
        )
        await service.record_task_outcome(
            goal_id=active.goal_id,
            task_id=first.active_task_ids[0],
            task_state=GoalTaskState.FAILED,
            idempotency_key="cycle-1-failed",
        )
        second = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="cycle-2",
            expected_revision=1,
            trigger_ref="task-terminal:event",
        )
        await service.record_task_outcome(
            goal_id=active.goal_id,
            task_id=second.active_task_ids[0],
            task_state=GoalTaskState.FAILED,
            idempotency_key="cycle-2-failed",
        )
        escalated = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="cycle-3",
            expected_revision=1,
            trigger_ref="task-terminal:event",
        )
        assert escalated.status is GoalStatus.PAUSED
        assert escalated.progress is GoalProgress.DEGRADED
        assert len(creator.created) == 2

    _run(scenario())


def test_pause_resume_stops_and_restores_review() -> None:
    async def scenario() -> None:
        service = GoalService(EventSourcedGoalRepository(InMemoryKernelRepository()))
        active = await _created_active(service=service)
        paused = await service.pause_goal(goal_id=active.goal_id, idempotency_key="pause")
        assert paused.status is GoalStatus.PAUSED
        with pytest.raises(ContractError):
            await service.review_goal(
                goal_id=active.goal_id,
                idempotency_key="blocked-review",
                expected_revision=1,
                trigger_ref="automation:tick",
            )
        resumed = await service.resume_goal(goal_id=active.goal_id, idempotency_key="resume")
        assert resumed.status is GoalStatus.ACTIVE

    _run(scenario())


def test_revision_preserves_old_task_provenance_and_rejects_stale_evaluator() -> None:
    async def scenario() -> None:
        creator = RecordingTaskCreator()
        repository = EventSourcedGoalRepository(InMemoryKernelRepository())
        service = GoalService(repository, task_creator=creator)
        active = await _created_active(service=service)
        working = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="review-before-revision",
            expected_revision=1,
            trigger_ref="manual:test",
        )
        old_task = working.active_task_ids[0]
        revised = await service.revise_goal(
            goal_id=active.goal_id,
            idempotency_key="revise",
            expected_revision=1,
            objective="Maintain explicit measurable quality with stricter constraints",
            active_task_policy="supersede",
        )
        assert revised.revision == 2
        assert revised.digest != active.digest
        old_link = next(link for link in revised.linked_tasks if link.task_id == old_task)
        assert old_link.goal_revision == 1
        assert not old_link.valid_for_current_revision
        historical = await repository.get_goal_revision(active.goal_id, 1)
        assert historical.revision == 1
        with pytest.raises(ContractError):
            await service.review_goal(
                goal_id=active.goal_id,
                idempotency_key="stale-review",
                expected_revision=1,
                trigger_ref="stale:evaluator",
            )

    _run(scenario())


def test_sqlite_restart_preserves_wait_and_duplicate_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "goals.sqlite3"
        first_repo = EventSourcedGoalRepository(SqliteKernelRepository(database))
        first_service = GoalService(first_repo)
        active = await _created_active(
            service=first_service,
            task_generation=TaskGenerationPolicy(enabled=False),
        )
        due = datetime.now(UTC) + timedelta(hours=1)
        waiting = await first_service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="durable-review",
            expected_revision=1,
            trigger_ref="automation-delivery:42",
            next_review_at=due,
        )
        assert waiting.status is GoalStatus.WAITING

        second_repo = EventSourcedGoalRepository(SqliteKernelRepository(database))
        second_service = GoalService(second_repo)
        recovered = await second_service.get_goal(active.goal_id)
        assert recovered.status is GoalStatus.WAITING
        assert recovered.next_review_at == due
        before_revision = recovered.stream_revision
        replay = await second_service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="durable-review",
            expected_revision=1,
            trigger_ref="automation-delivery:42",
            next_review_at=due,
        )
        assert replay.stream_revision == before_revision

    _run(scenario())


def test_restart_between_task_creation_and_goal_commit_reuses_same_task_id() -> None:
    async def scenario() -> None:
        creator = RecordingTaskCreator()
        inner = EventSourcedGoalRepository(InMemoryKernelRepository())
        repository = FailOnceCommitRepository(inner)
        service = GoalService(repository, task_creator=creator)
        active = await _created_active(service=service)
        with pytest.raises(RuntimeError):
            await service.review_goal(
                goal_id=active.goal_id,
                idempotency_key="crash-window",
                expected_revision=1,
                trigger_ref="automation-delivery:crash",
            )
        recovered = await service.review_goal(
            goal_id=active.goal_id,
            idempotency_key="crash-window",
            expected_revision=1,
            trigger_ref="automation-delivery:crash",
        )
        assert len(creator.created) == 1
        assert recovered.active_task_ids[0] in creator.created

    _run(scenario())


def test_direct_human_task_can_be_linked_to_goal_revision() -> None:
    async def scenario() -> None:
        service = GoalService(EventSourcedGoalRepository(InMemoryKernelRepository()))
        active = await _created_active(service=service)
        task_id = new_id("task")
        linked = await service.attach_task(
            goal_id=active.goal_id,
            task_id=task_id,
            idempotency_key="attach",
            expected_revision=1,
        )
        assert linked.linked_tasks[-1].task_id == task_id
        assert linked.linked_tasks[-1].goal_revision == 1

    _run(scenario())
