"""Canonical Task-management attention projections for issue #75.

Task-management remains authoritative for planning/deadline/dependency state. This module only
projects user attention from canonical TaskState + TaskManagementView values and never mutates
Task planning or lifecycle state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, TaskStatus
from ai_multi_agent_platform.kernel import TaskState
from ai_multi_agent_platform.task_management import TaskManagementView

from .models import (
    NotificationAction,
    NotificationCandidate,
    NotificationCategory,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
)

DEFAULT_DEADLINE_APPROACHING_WINDOW = timedelta(hours=24)


def task_management_change_candidates(
    before: TaskManagementView,
    after: TaskManagementView,
    task: TaskState,
) -> tuple[NotificationCandidate, ...]:
    """Project explicit planning changes after the canonical #88 command succeeds."""

    recipient = _task_attention_recipient(after, task.task.owner_ref)
    if recipient is None:
        return ()
    candidates: list[NotificationCandidate] = []

    assignment_changed = (
        before.metadata.responsibility != after.metadata.responsibility
        or before.metadata.agent_assignment != after.metadata.agent_assignment
    )
    if assignment_changed:
        responsibility = after.metadata.responsibility
        agent_assignment = after.metadata.agent_assignment
        assignment_summary: dict[str, JsonValue] = {
            "task_id": task.task_id,
            "responsibility": (None if responsibility is None else responsibility.to_json()),
            "agent_assignment": (None if agent_assignment is None else agent_assignment.to_json()),
        }
        candidates.append(
            _candidate(
                task=task,
                view=after,
                recipient=recipient,
                category=NotificationCategory.ASSIGNMENT,
                severity=NotificationSeverity.INFO,
                title="Task assignment updated",
                summary=assignment_summary,
                aggregation_key=_aggregation_key(
                    "assignment",
                    task.task_id,
                    assignment_summary,
                ),
            )
        )

    dependency_changed = (
        before.metadata.dependencies != after.metadata.dependencies
        or before.blocking_task_ids != after.blocking_task_ids
        or before.failed_dependency_ids != after.failed_dependency_ids
    )
    if dependency_changed:
        candidates.append(_dependency_candidate(after, task, recipient))

    if before.metadata.due_at != after.metadata.due_at:
        due_at = after.metadata.due_at
        deadline_summary: dict[str, JsonValue] = {
            "task_id": task.task_id,
            "due_at": None if due_at is None else due_at.isoformat(),
            "deadline_timezone": after.metadata.deadline_timezone,
            "overdue": after.overdue,
        }
        candidates.append(
            _candidate(
                task=task,
                view=after,
                recipient=recipient,
                category=NotificationCategory.DEADLINE,
                severity=(
                    NotificationSeverity.WARNING if after.overdue else NotificationSeverity.INFO
                ),
                title="Task deadline removed" if due_at is None else "Task deadline updated",
                summary=deadline_summary,
                aggregation_key=_aggregation_key(
                    "deadline-change",
                    task.task_id,
                    deadline_summary,
                ),
            )
        )

    return tuple(candidates)


def task_attention_state_candidates(
    view: TaskManagementView,
    task: TaskState,
    *,
    now: datetime | None = None,
    approaching_window: timedelta = DEFAULT_DEADLINE_APPROACHING_WINDOW,
) -> tuple[NotificationCandidate, ...]:
    """Evaluate time/dependency attention without creating a second planning truth.

    Callers should persist these through ``NotificationService.create_once`` so repeated timer
    ticks are idempotent until the active notification is dismissed/archived or the canonical
    source state changes.
    """

    current = now or datetime.now(UTC)
    if current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if approaching_window <= timedelta(0):
        raise ValueError("approaching_window must be positive")
    if task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
        return ()

    recipient = _task_attention_recipient(view, task.task.owner_ref)
    if recipient is None:
        return ()
    candidates: list[NotificationCandidate] = []

    if view.blocking_task_ids:
        candidates.append(_dependency_candidate(view, task, recipient))

    due_at = view.metadata.due_at
    if due_at is not None:
        if view.overdue:
            summary: dict[str, JsonValue] = {
                "task_id": task.task_id,
                "phase": "overdue",
                "due_at": due_at.isoformat(),
                "deadline_timezone": view.metadata.deadline_timezone,
            }
            candidates.append(
                _candidate(
                    task=task,
                    view=view,
                    recipient=recipient,
                    category=NotificationCategory.DEADLINE,
                    severity=NotificationSeverity.ERROR,
                    title="Task is overdue",
                    summary=summary,
                    aggregation_key=_aggregation_key(
                        "deadline-overdue",
                        task.task_id,
                        summary,
                    ),
                )
            )
        else:
            remaining = due_at - current.astimezone(UTC)
            if timedelta(0) < remaining <= approaching_window:
                summary = {
                    "task_id": task.task_id,
                    "phase": "approaching",
                    "due_at": due_at.isoformat(),
                    "deadline_timezone": view.metadata.deadline_timezone,
                }
                candidates.append(
                    _candidate(
                        task=task,
                        view=view,
                        recipient=recipient,
                        category=NotificationCategory.DEADLINE,
                        severity=NotificationSeverity.WARNING,
                        title="Task deadline is approaching",
                        summary=summary,
                        aggregation_key=_aggregation_key(
                            "deadline-approaching",
                            task.task_id,
                            summary,
                        ),
                        expires_at=due_at,
                    )
                )

    return tuple(candidates)


def _dependency_candidate(
    view: TaskManagementView,
    task: TaskState,
    recipient: RecipientRef,
) -> NotificationCandidate:
    if view.failed_dependency_ids:
        title = "Task dependency failed"
        severity = NotificationSeverity.ERROR
        state = "failed"
    elif view.blocking_task_ids:
        title = "Task blocked by dependency"
        severity = NotificationSeverity.WARNING
        state = "blocked"
    else:
        title = "Task dependencies cleared"
        severity = NotificationSeverity.INFO
        state = "clear"
    summary: dict[str, JsonValue] = {
        "task_id": task.task_id,
        "state": state,
        "blocking_task_ids": list(view.blocking_task_ids),
        "failed_dependency_ids": list(view.failed_dependency_ids),
        "effective_blocking_reason": view.planning_resource().get("effective_blocking_reason"),
    }
    return _candidate(
        task=task,
        view=view,
        recipient=recipient,
        category=NotificationCategory.DEPENDENCY,
        severity=severity,
        title=title,
        summary=summary,
        aggregation_key=_aggregation_key("dependency", task.task_id, summary),
    )


def _candidate(
    *,
    task: TaskState,
    view: TaskManagementView,
    recipient: RecipientRef,
    category: NotificationCategory,
    severity: NotificationSeverity,
    title: str,
    summary: dict[str, JsonValue],
    aggregation_key: str,
    expires_at: datetime | None = None,
) -> NotificationCandidate:
    source = SourceRef("task", task.task_id)
    return NotificationCandidate(
        category=category,
        severity=severity,
        title=title,
        summary=summary,
        recipient=recipient,
        source=source,
        project_id=task.task.project_id,
        workspace_id=view.metadata.workspace_id,
        task_id=task.task_id,
        resource_ref=source,
        actions=(
            NotificationAction(
                action_id="open-task",
                label="Open task",
                resource_type="task",
                resource_id=task.task_id,
                href=f"/tasks/{task.task_id}",
            ),
        ),
        aggregation_key=aggregation_key,
        expires_at=expires_at,
        correlation_id=task.task.correlation_id,
        causation_id=task.task.causation_id,
    )


def _task_attention_recipient(
    view: TaskManagementView,
    owner: OwnerRef,
) -> RecipientRef | None:
    responsibility = view.metadata.responsibility
    if responsibility is not None:
        candidate = _recipient(responsibility.kind, responsibility.id)
        if candidate is not None:
            return candidate
    return _recipient(owner.type, owner.id)


def _recipient(kind: str, identifier: str) -> RecipientRef | None:
    try:
        recipient_type = RecipientType(kind)
        return RecipientRef(recipient_type, identifier)
    except ValueError:
        # #88 responsibility is planning metadata, not an identity mapping authority. A
        # non-canonical reference must therefore never be reinterpreted as a notification
        # recipient; callers fall back to the canonical Task owner when possible.
        return None


def _aggregation_key(kind: str, task_id: str, state: JsonValue) -> str:
    encoded = json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    return f"task:{task_id}:{kind}:{digest}"
