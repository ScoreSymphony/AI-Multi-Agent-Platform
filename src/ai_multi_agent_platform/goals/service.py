"""Application service for durable, bounded Goal lifecycle management."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, utc_now, validate_id
from ai_multi_agent_platform.kernel import PlatformKernel

from .criteria import evaluate_criteria, required_criteria_satisfied
from .models import (
    TERMINAL_GOAL_STATUSES,
    AutonomyPolicy,
    CriterionEvaluation,
    GoalConstraints,
    GoalCriterionState,
    GoalEvidence,
    GoalProgress,
    GoalReview,
    GoalState,
    GoalStatus,
    GoalTaskLink,
    GoalTaskState,
    ObservationPolicy,
    SuccessCriterion,
    TaskGenerationPolicy,
    TaskRevisionPolicy,
    constraints_to_json,
    deterministic_goal_task_id,
    deterministic_review_id,
    goal_revision_digest,
    make_goal,
    require_goal_transition,
)
from .repository import GoalRepository


@runtime_checkable
class GoalTaskCreator(Protocol):
    async def create_goal_task(
        self,
        *,
        goal: GoalState,
        review_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> str: ...


class KernelGoalTaskCreator(GoalTaskCreator):
    """Create executable work only through the canonical Task kernel."""

    def __init__(self, kernel: PlatformKernel) -> None:
        self._kernel = kernel

    async def create_goal_task(
        self,
        *,
        goal: GoalState,
        review_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> str:
        title = goal.task_generation_policy.task_title or f"Goal work: {goal.title}"
        metadata: dict[str, JsonValue] = {
            "goal_id": goal.goal_id,
            "goal_revision": goal.revision,
            "goal_digest": goal.digest,
            "goal_review_id": review_id,
            "goal_constraints": constraints_to_json(goal.constraints),
        }
        task = await self._kernel.create_task(
            idempotency_key=idempotency_key,
            title=title,
            objective=goal.objective,
            owner_type=goal.owner_ref.type,
            owner_id=goal.owner_ref.id,
            project_id=goal.project_id,
            task_id=task_id,
            actor_ref="service:goal-service",
            source="goal-service",
        )
        await self._kernel.update_task(
            idempotency_key=f"{idempotency_key}:goal-provenance",
            task_id=task_id,
            metadata=metadata,
            actor_ref="service:goal-service",
            source="goal-service",
        )
        return task.task_id


class GoalService:
    """Own long-lived Goal truth while delegating executable work to canonical Tasks."""

    def __init__(
        self,
        repository: GoalRepository,
        *,
        task_creator: GoalTaskCreator | None = None,
    ) -> None:
        self._repository = repository
        self._task_creator = task_creator

    async def create_goal(
        self,
        *,
        idempotency_key: str,
        title: str,
        objective: str,
        owner_ref: OwnerRef,
        success_criteria: tuple[SuccessCriterion, ...],
        project_id: str | None = None,
        constraints: GoalConstraints | None = None,
        observation_policy: ObservationPolicy | None = None,
        task_generation_policy: TaskGenerationPolicy | None = None,
        autonomy_policy: AutonomyPolicy | None = None,
        deadline: datetime | None = None,
        actor_ref: str | None = None,
        goal_id: str | None = None,
    ) -> GoalState:
        existing = await self._repository.find_command(
            "goal:create", idempotency_key, "goal.create"
        )
        if existing is not None:
            return await self._repository.get_goal(existing.result_id)
        if not title.strip() or not objective.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "Goal title/objective must not be blank")
        if not success_criteria:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Goal requires at least one explicit success criterion",
            )
        resolved_constraints = constraints or GoalConstraints()
        resolved_observation = observation_policy or ObservationPolicy()
        resolved_generation = task_generation_policy or TaskGenerationPolicy()
        resolved_autonomy = autonomy_policy or AutonomyPolicy()
        goal = make_goal(
            title=title,
            objective=objective,
            owner_ref=owner_ref,
            project_id=project_id,
            actor_ref=actor_ref,
            goal_id=goal_id,
        )
        digest = goal_revision_digest(
            title=title,
            objective=objective,
            success_criteria=success_criteria,
            constraints=resolved_constraints,
            observation_policy=resolved_observation,
            task_generation_policy=resolved_generation,
            autonomy_policy=resolved_autonomy,
            deadline=deadline,
        )
        state = GoalState(
            goal=goal,
            revision=1,
            digest=digest,
            status=GoalStatus.DRAFT,
            progress=GoalProgress.UNKNOWN,
            success_criteria=success_criteria,
            constraints=resolved_constraints,
            observation_policy=resolved_observation,
            task_generation_policy=resolved_generation,
            autonomy_policy=resolved_autonomy,
            deadline=deadline,
            created_actor_ref=actor_ref,
            updated_actor_ref=actor_ref,
        )
        return await self._repository.commit_snapshot(
            previous=None,
            current=state,
            event_type="goal.created",
            idempotency_key=idempotency_key,
            operation="goal.create",
            command_scope="goal:create",
            actor_ref=actor_ref,
        )

    async def get_goal(self, goal_id: str) -> GoalState:
        return await self._repository.get_goal(goal_id)

    async def get_goal_revision(self, goal_id: str, revision: int) -> GoalState:
        return await self._repository.get_goal_revision(goal_id, revision)

    async def list_goals(self) -> tuple[GoalState, ...]:
        return await self._repository.list_goals()

    async def activate_goal(
        self, *, goal_id: str, idempotency_key: str, actor_ref: str | None = None
    ) -> GoalState:
        return await self._transition(
            goal_id=goal_id,
            target=GoalStatus.ACTIVE,
            event_type="goal.activated",
            operation="goal.activate",
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            progress=GoalProgress.MONITORING,
        )

    async def pause_goal(
        self, *, goal_id: str, idempotency_key: str, actor_ref: str | None = None
    ) -> GoalState:
        return await self._transition(
            goal_id=goal_id,
            target=GoalStatus.PAUSED,
            event_type="goal.paused",
            operation="goal.pause",
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            progress=GoalProgress.BLOCKED,
        )

    async def resume_goal(
        self, *, goal_id: str, idempotency_key: str, actor_ref: str | None = None
    ) -> GoalState:
        return await self._transition(
            goal_id=goal_id,
            target=GoalStatus.ACTIVE,
            event_type="goal.resumed",
            operation="goal.resume",
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            progress=GoalProgress.MONITORING,
        )

    async def cancel_goal(
        self,
        *,
        goal_id: str,
        idempotency_key: str,
        reason: str,
        actor_ref: str | None = None,
    ) -> GoalState:
        if not reason.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "Goal cancellation requires a reason")
        return await self._transition(
            goal_id=goal_id,
            target=GoalStatus.CANCELLED,
            event_type="goal.cancelled",
            operation="goal.cancel",
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            progress=GoalProgress.BLOCKED,
            terminal_reason=reason,
        )

    async def fail_goal(
        self,
        *,
        goal_id: str,
        idempotency_key: str,
        reason: str,
        actor_ref: str | None = None,
    ) -> GoalState:
        """Mark active/waiting Goal pursuit as terminally failed with an explicit reason."""
        if not reason.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "Goal failure requires a reason")
        return await self._transition(
            goal_id=goal_id,
            target=GoalStatus.FAILED,
            event_type="goal.failed",
            operation="goal.fail",
            idempotency_key=idempotency_key,
            actor_ref=actor_ref,
            progress=GoalProgress.DEGRADED,
            terminal_reason=reason,
        )

    async def revise_goal(
        self,
        *,
        goal_id: str,
        idempotency_key: str,
        expected_revision: int,
        title: str | None = None,
        objective: str | None = None,
        success_criteria: tuple[SuccessCriterion, ...] | None = None,
        constraints: GoalConstraints | None = None,
        observation_policy: ObservationPolicy | None = None,
        task_generation_policy: TaskGenerationPolicy | None = None,
        autonomy_policy: AutonomyPolicy | None = None,
        deadline: datetime | None = None,
        replace_deadline: bool = False,
        active_task_policy: TaskRevisionPolicy = "retain",
        reopen_terminal: bool = False,
        actor_ref: str | None = None,
    ) -> GoalState:
        existing = await self._repository.find_command(
            f"goal:{goal_id}", idempotency_key, "goal.revise"
        )
        if existing is not None:
            return await self._repository.get_goal(existing.result_id)
        current = await self._repository.get_goal(goal_id)
        self._require_revision(current, expected_revision)
        if current.status in {GoalStatus.CANCELLED, GoalStatus.SUPERSEDED}:
            raise ContractError(ErrorCode.CONFLICT, f"Goal {goal_id} cannot be revised")
        if current.status in {GoalStatus.SATISFIED, GoalStatus.FAILED} and not reopen_terminal:
            raise ContractError(
                ErrorCode.CONFLICT,
                "terminal Goal revision requires explicit reopen_terminal=true",
            )
        resolved_title = title if title is not None else current.title
        resolved_objective = objective if objective is not None else current.objective
        resolved_criteria = success_criteria or current.success_criteria
        if not resolved_title.strip() or not resolved_objective.strip() or not resolved_criteria:
            raise ContractError(ErrorCode.INVALID_REQUEST, "invalid Goal revision content")
        resolved_constraints = constraints or current.constraints
        resolved_observation = observation_policy or current.observation_policy
        resolved_generation = task_generation_policy or current.task_generation_policy
        resolved_autonomy = autonomy_policy or current.autonomy_policy
        resolved_deadline = deadline if replace_deadline else (deadline or current.deadline)
        now = utc_now()
        revised_goal = replace(
            current.goal,
            title=resolved_title,
            description=resolved_objective,
            updated_at=now,
        )
        revised_links = current.linked_tasks
        if active_task_policy == "supersede":
            revised_links = tuple(
                replace(
                    link,
                    valid_for_current_revision=False,
                    task_state=(
                        GoalTaskState.SUPERSEDED
                        if link.task_state is GoalTaskState.ACTIVE
                        else link.task_state
                    ),
                )
                for link in current.linked_tasks
            )
        digest = goal_revision_digest(
            title=resolved_title,
            objective=resolved_objective,
            success_criteria=resolved_criteria,
            constraints=resolved_constraints,
            observation_policy=resolved_observation,
            task_generation_policy=resolved_generation,
            autonomy_policy=resolved_autonomy,
            deadline=resolved_deadline,
        )
        status = current.status
        progress = current.progress
        terminal_reason = current.terminal_reason
        if current.status in {GoalStatus.SATISFIED, GoalStatus.FAILED}:
            status = GoalStatus.ACTIVE
            progress = GoalProgress.MONITORING
            terminal_reason = None
        revised = replace(
            current,
            goal=revised_goal,
            revision=current.revision + 1,
            digest=digest,
            status=status,
            progress=progress,
            success_criteria=resolved_criteria,
            constraints=resolved_constraints,
            observation_policy=resolved_observation,
            task_generation_policy=resolved_generation,
            autonomy_policy=resolved_autonomy,
            deadline=resolved_deadline,
            linked_tasks=revised_links,
            next_review_at=None,
            terminal_reason=terminal_reason,
            updated_actor_ref=actor_ref,
        )
        return await self._repository.commit_snapshot(
            previous=current,
            current=revised,
            event_type="goal.revised",
            idempotency_key=idempotency_key,
            operation="goal.revise",
            command_scope=f"goal:{goal_id}",
            actor_ref=actor_ref,
            details={
                "previous_revision": current.revision,
                "new_revision": revised.revision,
                "active_task_policy": active_task_policy,
            },
        )

    async def attach_task(
        self,
        *,
        goal_id: str,
        task_id: str,
        idempotency_key: str,
        expected_revision: int,
        actor_ref: str | None = None,
    ) -> GoalState:
        validate_id(task_id, "task")
        existing = await self._repository.find_command(
            f"goal:{goal_id}", idempotency_key, "goal.attach_task"
        )
        if existing is not None:
            return await self._repository.get_goal(existing.result_id)
        current = await self._repository.get_goal(goal_id)
        self._require_revision(current, expected_revision)
        if any(link.task_id == task_id for link in current.linked_tasks):
            return current
        linked = replace(
            current,
            linked_tasks=current.linked_tasks
            + (GoalTaskLink(task_id=task_id, goal_revision=current.revision, review_id=None),),
            progress=GoalProgress.ACTIVE_WORK,
            updated_actor_ref=actor_ref,
            goal=replace(current.goal, updated_at=utc_now()),
        )
        return await self._repository.commit_snapshot(
            previous=current,
            current=linked,
            event_type="goal.task_linked",
            idempotency_key=idempotency_key,
            operation="goal.attach_task",
            command_scope=f"goal:{goal_id}",
            actor_ref=actor_ref,
            details={"task_id": task_id, "goal_revision": current.revision},
        )

    async def record_task_outcome(
        self,
        *,
        goal_id: str,
        task_id: str,
        task_state: GoalTaskState,
        idempotency_key: str,
        actor_ref: str | None = None,
    ) -> GoalState:
        if task_state is GoalTaskState.ACTIVE:
            raise ContractError(ErrorCode.INVALID_REQUEST, "recorded Task outcome must be terminal")
        existing = await self._repository.find_command(
            f"goal:{goal_id}", idempotency_key, "goal.record_task_outcome"
        )
        if existing is not None:
            return await self._repository.get_goal(existing.result_id)
        current = await self._repository.get_goal(goal_id)
        found = False
        links: list[GoalTaskLink] = []
        for link in current.linked_tasks:
            if link.task_id == task_id:
                found = True
                links.append(replace(link, task_state=task_state))
            else:
                links.append(link)
        if not found:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"Task {task_id} is not linked to Goal {goal_id}"
            )
        failed_cycles = current.consecutive_failed_cycles
        if task_state is GoalTaskState.FAILED:
            failed_cycles += 1
        elif task_state is GoalTaskState.SUCCEEDED:
            failed_cycles = 0
        if current.status in TERMINAL_GOAL_STATUSES or current.status is GoalStatus.PAUSED:
            progress = current.progress
        else:
            progress = (
                GoalProgress.DEGRADED
                if task_state is GoalTaskState.FAILED
                else GoalProgress.MONITORING
            )
        updated = replace(
            current,
            linked_tasks=tuple(links),
            consecutive_failed_cycles=failed_cycles,
            progress=progress,
            updated_actor_ref=actor_ref,
            goal=replace(current.goal, updated_at=utc_now()),
        )
        return await self._repository.commit_snapshot(
            previous=current,
            current=updated,
            event_type="goal.task_outcome_reconciled",
            idempotency_key=idempotency_key,
            operation="goal.record_task_outcome",
            command_scope=f"goal:{goal_id}",
            actor_ref=actor_ref,
            details={"task_id": task_id, "task_state": task_state.value},
        )

    async def review_goal(
        self,
        *,
        goal_id: str,
        idempotency_key: str,
        expected_revision: int,
        trigger_ref: str,
        evidence: tuple[GoalEvidence, ...] = (),
        next_review_at: datetime | None = None,
        actor_ref: str | None = None,
    ) -> GoalState:
        existing = await self._repository.find_command(
            f"goal:{goal_id}", idempotency_key, "goal.review"
        )
        if existing is not None:
            return await self._repository.get_goal(existing.result_id)
        if not trigger_ref.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST, "Goal review trigger_ref must not be blank"
            )
        current = await self._repository.get_goal(goal_id)
        self._require_revision(current, expected_revision)
        if current.status not in {GoalStatus.ACTIVE, GoalStatus.WAITING}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Goal {goal_id} cannot be reviewed while {current.status.value}",
            )
        merged_evidence = _merge_evidence(current.evidence, evidence)
        evaluations = evaluate_criteria(
            current.success_criteria,
            merged_evidence,
            current.linked_tasks,
        )
        changed_criterion_ids = _changed_criterion_ids(current, evaluations)
        review_id = deterministic_review_id(goal_id, current.revision, idempotency_key)
        all_satisfied = required_criteria_satisfied(current.success_criteria, evaluations)
        generated_task_ids: tuple[str, ...] = ()
        linked_tasks = current.linked_tasks
        status: GoalStatus
        progress: GoalProgress
        terminal_reason: str | None = None
        decision_reason: str

        if all_satisfied:
            status = GoalStatus.SATISFIED
            progress = GoalProgress.SATISFIED
            terminal_reason = (
                "all required success criteria satisfied by explicit verified evidence"
            )
            decision_reason = terminal_reason
            next_review_at = None
        elif (
            current.consecutive_failed_cycles
            >= current.autonomy_policy.max_consecutive_failed_cycles
        ):
            status = GoalStatus.PAUSED
            progress = GoalProgress.DEGRADED
            decision_reason = "bounded autonomy failure limit reached; human attention required"
        elif current.active_task_ids:
            status = GoalStatus.ACTIVE
            progress = GoalProgress.ACTIVE_WORK
            decision_reason = "equivalent Goal-linked work is already active"
        elif current.autonomy_policy.human_checkpoint_required:
            status = GoalStatus.PAUSED
            progress = GoalProgress.BLOCKED
            decision_reason = "Goal policy requires a human checkpoint before generating work"
        elif (
            not current.task_generation_policy.enabled
            or current.autonomy_policy.max_tasks_per_review == 0
            or self._task_creator is None
        ):
            status = GoalStatus.WAITING
            progress = GoalProgress.PARTIAL
            decision_reason = "criteria remain unmet but automatic Task generation is disabled"
        elif current.task_generation_policy.proposal_required:
            status = GoalStatus.PAUSED
            progress = GoalProgress.BLOCKED
            decision_reason = (
                "Goal policy requires Proposal/Specification mediation; "
                "automatic Proposal generation is not configured"
            )
        else:
            task_id = deterministic_goal_task_id(goal_id, current.revision, idempotency_key, 0)
            created_task_id = await self._task_creator.create_goal_task(
                goal=current,
                review_id=review_id,
                task_id=task_id,
                idempotency_key=f"goal:{goal_id}:{current.revision}:{idempotency_key}:task:0",
            )
            if created_task_id != task_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Goal Task creator returned a different canonical Task ID",
                )
            generated_task_ids = (task_id,)
            linked_tasks = current.linked_tasks + (
                GoalTaskLink(
                    task_id=task_id,
                    goal_revision=current.revision,
                    review_id=review_id,
                ),
            )
            status = GoalStatus.ACTIVE
            progress = GoalProgress.ACTIVE_WORK
            decision_reason = "unmet criteria require bounded canonical Task work"

        review = GoalReview(
            review_id=review_id,
            goal_revision=current.revision,
            trigger_ref=trigger_ref,
            criterion_evaluations=evaluations,
            generated_task_ids=generated_task_ids,
            work_required=not all_satisfied,
            decision_reason=decision_reason,
        )
        updated = replace(
            current,
            status=status,
            progress=progress,
            linked_tasks=linked_tasks,
            evidence=merged_evidence,
            reviews=current.reviews + (review,),
            next_review_at=next_review_at,
            terminal_reason=terminal_reason,
            updated_actor_ref=actor_ref,
            goal=replace(current.goal, updated_at=utc_now()),
        )
        additional_event_types = ["goal.review_started"]
        if changed_criterion_ids:
            additional_event_types.append("goal.progress_criterion_changed")
        if not review.work_required:
            additional_event_types.append("goal.work_not_needed")
        if status is GoalStatus.PAUSED and progress is GoalProgress.BLOCKED:
            additional_event_types.append("goal.blocked")
        if status is GoalStatus.PAUSED and progress is GoalProgress.DEGRADED:
            additional_event_types.append("goal.escalated")
        if generated_task_ids:
            additional_event_types.append("goal.task_generated")
        if status is GoalStatus.SATISFIED:
            additional_event_types.append("goal.satisfied")
        return await self._repository.commit_snapshot(
            previous=current,
            current=updated,
            event_type="goal.review_completed",
            idempotency_key=idempotency_key,
            operation="goal.review",
            command_scope=f"goal:{goal_id}",
            actor_ref=actor_ref,
            details={
                "review_id": review_id,
                "goal_revision": current.revision,
                "trigger_ref": trigger_ref,
                "generated_task_ids": list(generated_task_ids),
                "decision_reason": decision_reason,
                "work_required": review.work_required,
                "criterion_changes": list(changed_criterion_ids),
                "status": status.value,
                "progress": progress.value,
            },
            additional_event_types=tuple(additional_event_types),
        )

    async def _transition(
        self,
        *,
        goal_id: str,
        target: GoalStatus,
        event_type: str,
        operation: str,
        idempotency_key: str,
        actor_ref: str | None,
        progress: GoalProgress,
        terminal_reason: str | None = None,
    ) -> GoalState:
        existing = await self._repository.find_command(
            f"goal:{goal_id}", idempotency_key, operation
        )
        if existing is not None:
            return await self._repository.get_goal(existing.result_id)
        current = await self._repository.get_goal(goal_id)
        try:
            require_goal_transition(current.status, target)
        except ValueError as exc:
            raise ContractError(ErrorCode.CONFLICT, str(exc)) from exc
        updated = replace(
            current,
            status=target,
            progress=progress,
            terminal_reason=terminal_reason,
            next_review_at=None if target is not GoalStatus.WAITING else current.next_review_at,
            updated_actor_ref=actor_ref,
            goal=replace(current.goal, updated_at=utc_now()),
        )
        return await self._repository.commit_snapshot(
            previous=current,
            current=updated,
            event_type=event_type,
            idempotency_key=idempotency_key,
            operation=operation,
            command_scope=f"goal:{goal_id}",
            actor_ref=actor_ref,
        )

    @staticmethod
    def _require_revision(state: GoalState, expected_revision: int) -> None:
        if state.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "stale Goal revision",
                details={
                    "goal_id": state.goal_id,
                    "expected_revision": expected_revision,
                    "current_revision": state.revision,
                    "current_digest": state.digest,
                },
            )


def _merge_evidence(
    existing: tuple[GoalEvidence, ...], incoming: tuple[GoalEvidence, ...]
) -> tuple[GoalEvidence, ...]:
    by_id = {item.evidence_id: item for item in existing}
    for item in incoming:
        prior = by_id.get(item.evidence_id)
        if prior is not None and prior != item:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Goal evidence ID reused with different content: {item.evidence_id}",
            )
        by_id[item.evidence_id] = item
    return tuple(sorted(by_id.values(), key=lambda item: (item.observed_at, item.evidence_id)))


def _changed_criterion_ids(
    state: GoalState,
    evaluations: tuple[CriterionEvaluation, ...],
) -> tuple[str, ...]:
    if not state.reviews:
        return tuple(item.criterion_id for item in evaluations)
    previous = {item.criterion_id: item.state for item in state.reviews[-1].criterion_evaluations}
    return tuple(
        item.criterion_id
        for item in evaluations
        if previous.get(item.criterion_id, GoalCriterionState.UNKNOWN) != item.state
    )


__all__ = ["GoalService", "GoalTaskCreator", "KernelGoalTaskCreator"]
