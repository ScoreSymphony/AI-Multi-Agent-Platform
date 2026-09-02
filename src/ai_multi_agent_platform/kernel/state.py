"""Deterministic reducers for canonical task and run state."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import ExecutionStatus, JsonValue, PlatformEvent

from .models import RunView, TaskStatus, TaskView

_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.READY}),
    TaskStatus.CANCELLED: frozenset(),
}

# A backend may complete before the platform persists an intermediate running event.
# Recovery therefore permits queued -> terminal transitions while keeping all
# subsequent terminal transitions forbidden.
_RUN_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.QUEUED: frozenset(
        {
            ExecutionStatus.RUNNING,
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }
    ),
    ExecutionStatus.RUNNING: frozenset(
        {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMED_OUT,
        }
    ),
    ExecutionStatus.SUCCEEDED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.CANCELLED: frozenset(),
    ExecutionStatus.TIMED_OUT: frozenset(),
}

_TASK_EVENT_STATUS: dict[str, TaskStatus] = {
    "task.ready": TaskStatus.READY,
    "task.running": TaskStatus.RUNNING,
    "task.succeeded": TaskStatus.SUCCEEDED,
    "task.failed": TaskStatus.FAILED,
    "task.cancelled": TaskStatus.CANCELLED,
}

_RUN_EVENT_STATUS: dict[str, ExecutionStatus] = {
    "run.running": ExecutionStatus.RUNNING,
    "run.succeeded": ExecutionStatus.SUCCEEDED,
    "run.failed": ExecutionStatus.FAILED,
    "run.cancelled": ExecutionStatus.CANCELLED,
    "run.timed_out": ExecutionStatus.TIMED_OUT,
}


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(ErrorCode.BACKEND_ERROR, f"Event payload missing string {key!r}")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(ErrorCode.BACKEND_ERROR, f"Event payload has invalid {key!r}")
    return value


def _required_int(payload: dict[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(ErrorCode.BACKEND_ERROR, f"Event payload missing integer {key!r}")
    return value


def _transition_task(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if target == current:
        return current
    if target not in _TASK_TRANSITIONS[current]:
        raise ContractError(
            ErrorCode.CONFLICT,
            f"Invalid task transition: {current.value} -> {target.value}",
        )
    return target


def _transition_run(current: ExecutionStatus, target: ExecutionStatus) -> ExecutionStatus:
    if target == current:
        return current
    if target not in _RUN_TRANSITIONS[current]:
        raise ContractError(
            ErrorCode.CONFLICT,
            f"Invalid run transition: {current.value} -> {target.value}",
        )
    return target


def reduce_task(events: tuple[PlatformEvent, ...], task_id: str) -> TaskView:
    """Reconstruct one Task view from its canonical event history."""

    task: TaskView | None = None

    for event in events:
        if event.event_type == "task.created" and event.subject_id == task_id:
            if task is not None:
                raise ContractError(ErrorCode.CONFLICT, f"Duplicate task creation: {task_id}")
            task = TaskView(
                task_id=task_id,
                title=_required_string(event.payload, "title"),
                objective=_required_string(event.payload, "objective"),
                status=TaskStatus.DRAFT,
                owner_type=event.context.owner_type,
                owner_id=event.context.owner_id,
                project_id=event.context.project_id,
            )
            continue

        if task is None:
            continue

        if event.event_type == "task.updated" and event.subject_id == task_id:
            title = event.payload.get("title", task.title)
            objective = event.payload.get("objective", task.objective)
            if not isinstance(title, str) or not isinstance(objective, str):
                raise ContractError(ErrorCode.BACKEND_ERROR, "Invalid task.updated payload")
            task = replace(task, title=title, objective=objective)
            continue

        target_status = _TASK_EVENT_STATUS.get(event.event_type)
        if target_status is not None and event.subject_id == task_id:
            task = replace(task, status=_transition_task(task.status, target_status))
            continue

        if event.event_type == "plan.created" and event.subject_id == task_id:
            task = replace(task, plan_ref=_required_string(event.payload, "plan_ref"))
            continue

        if event.event_type == "run.queued":
            run_id = event.subject_id
            if run_id not in task.run_ids:
                task = replace(task, run_ids=(*task.run_ids, run_id))
            continue

        if event.event_type == "artifact.attached" and event.subject_id == task_id:
            artifact_ref = _required_string(event.payload, "artifact_ref")
            if artifact_ref not in task.artifact_refs:
                task = replace(task, artifact_refs=(*task.artifact_refs, artifact_ref))
            continue

        if event.event_type == "result.recorded" and event.subject_id == task_id:
            result_ref = _required_string(event.payload, "result_ref")
            if result_ref not in task.result_refs:
                task = replace(task, result_refs=(*task.result_refs, result_ref))

    if task is None:
        raise ContractError(ErrorCode.NOT_FOUND, f"Task not found: {task_id}")
    return task


def reduce_run(events: tuple[PlatformEvent, ...], run_id: str) -> RunView:
    """Reconstruct one Run view from canonical events."""

    run: RunView | None = None

    for event in events:
        if event.subject_type != "run" or event.subject_id != run_id:
            continue

        if event.event_type == "run.queued":
            task_id = _required_string(event.payload, "task_id")
            attempt = _required_int(event.payload, "attempt")
            if run is not None:
                if run.task_id != task_id or run.attempt != attempt:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        f"Conflicting run reservation replay: {run_id}",
                    )
                continue
            run = RunView(
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                status=ExecutionStatus.QUEUED,
            )
            continue

        if run is None:
            continue

        target_status = _RUN_EVENT_STATUS.get(event.event_type)
        if target_status is None:
            continue

        backend_ref = run.backend_ref
        if event.event_type == "run.running":
            backend_ref = _optional_string(event.payload, "backend_ref")

        output = run.output
        output_value = event.payload.get("output")
        if isinstance(output_value, dict):
            output = output_value

        run = replace(
            run,
            status=_transition_run(run.status, target_status),
            backend_ref=backend_ref,
            output=output,
        )

    if run is None:
        raise ContractError(ErrorCode.NOT_FOUND, f"Run not found: {run_id}")
    return run
