"""Idempotent Goal linkage reconciliation for canonical Task terminal events."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import PlatformEvent

from .models import GoalTaskState
from .service import GoalService

_TASK_TERMINAL_STATES = {
    "task.succeeded": GoalTaskState.SUCCEEDED,
    "task.failed": GoalTaskState.FAILED,
    "task.cancelled": GoalTaskState.CANCELLED,
}
_RECONCILER_ACTOR = "service:goal-task-reconciler"


async def reconcile_goal_task_terminal_event(
    goals: GoalService,
    event: PlatformEvent,
) -> tuple[str, ...]:
    """Project a canonical Task terminal event into every linked durable Goal.

    Task/Run state remains owned by the kernel. Event-ID command idempotency makes
    retries safe when Goal reconciliation commits before #18 advances its event cursor.
    """

    if event.subject_type != "task":
        return ()
    task_state = _TASK_TERMINAL_STATES.get(event.event_type)
    if task_state is None:
        return ()

    reconciled: list[str] = []
    for goal in await goals.list_goals():
        if not any(link.task_id == event.subject_id for link in goal.linked_tasks):
            continue
        await goals.record_task_outcome(
            goal_id=goal.goal_id,
            task_id=event.subject_id,
            task_state=task_state,
            idempotency_key=f"task-terminal-event:{event.id}",
            actor_ref=_RECONCILER_ACTOR,
        )
        reconciled.append(goal.goal_id)
    return tuple(reconciled)


__all__ = ["reconcile_goal_task_terminal_event"]
