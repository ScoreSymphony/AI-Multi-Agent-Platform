"""Deterministic reducers from canonical event history to TaskState and RunState."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import (
    OwnerRef,
    Provenance,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    require_transition,
)

from .models import RunState, TaskState

OwnerType = Literal["user", "organization", "team", "service"]

_TASK_EVENT_STATUS: dict[str, TaskStatus] = {
    "task.ready": TaskStatus.READY,
    "task.running": TaskStatus.RUNNING,
    "task.waiting": TaskStatus.WAITING,
    "task.resumed": TaskStatus.RUNNING,
    "task.succeeded": TaskStatus.SUCCEEDED,
    "task.failed": TaskStatus.FAILED,
    "task.cancelled": TaskStatus.CANCELLED,
}

_RUN_EVENT_STATUS: dict[str, RunStatus] = {
    "run.starting": RunStatus.STARTING,
    "run.running": RunStatus.RUNNING,
    "run.succeeded": RunStatus.SUCCEEDED,
    "run.failed": RunStatus.FAILED,
    "run.cancelled": RunStatus.CANCELLED,
    "run.timed_out": RunStatus.TIMED_OUT,
}


def _timestamp(event: PlatformEvent) -> datetime:
    try:
        return datetime.fromisoformat(event.occurred_at)
    except ValueError as exc:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid event timestamp") from exc


def _string(event: PlatformEvent, key: str) -> str:
    value = event.payload.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"event {event.event_type} missing non-empty {key}",
        )
    return value


def _optional_string(event: PlatformEvent, key: str) -> str | None:
    value = event.payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"event {event.event_type} has invalid {key}",
        )
    return value


def _integer(event: PlatformEvent, key: str) -> int:
    value = event.payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"event {event.event_type} missing integer {key}",
        )
    return value


def _event_provenance(event: PlatformEvent) -> Provenance:
    return Provenance(
        source=_optional_string(event, "source") or "platform-kernel",
        actor_ref=_optional_string(event, "actor_ref"),
    )


def _transition_task(task: Task, target: TaskStatus, event: PlatformEvent) -> Task:
    if task.status is target:
        return task
    try:
        require_transition(task.status, target, TASK_TRANSITIONS)
    except ValueError as exc:
        raise ContractError(ErrorCode.CONFLICT, str(exc)) from exc
    return replace(
        task,
        status=target,
        updated_at=_timestamp(event),
        causation_id=event.context.causation_id,
        provenance=_event_provenance(event),
    )


def _transition_run(run: Run, target: RunStatus, event: PlatformEvent) -> Run:
    if run.status is target:
        return run
    try:
        require_transition(run.status, target, RUN_TRANSITIONS)
    except ValueError as exc:
        raise ContractError(ErrorCode.CONFLICT, str(exc)) from exc
    occurred = _timestamp(event)
    changes: dict[str, object] = {
        "status": target,
        "updated_at": occurred,
        "causation_id": event.context.causation_id,
        "provenance": _event_provenance(event),
    }
    if target is RunStatus.RUNNING and run.started_at is None:
        changes["started_at"] = occurred
    if target in {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    }:
        changes["finished_at"] = occurred
    return replace(run, **changes)


def reduce_task(events: tuple[PlatformEvent, ...], task_id: str) -> TaskState:
    task: Task | None = None
    plan_ref: str | None = None
    run_ids: list[str] = []
    artifact_ids: list[str] = []
    result_ids: list[str] = []
    wait_reason: str | None = None
    blocked = False

    for event in events:
        if event.context.correlation_id != task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"event {event.event_id} is in the wrong task stream",
            )

        if event.event_type == "task.created" and event.subject_id == task_id:
            if task is not None:
                raise ContractError(ErrorCode.CONFLICT, f"duplicate task creation: {task_id}")
            owner_type = cast(OwnerType, _string(event, "owner_type"))
            owner = OwnerRef(type=owner_type, id=_string(event, "owner_id"))
            occurred = _timestamp(event)
            task = Task(
                id=task_id,
                title=_string(event, "title"),
                description=_string(event, "objective"),
                owner_ref=owner,
                project_id=event.context.project_id,
                correlation_id=event.context.correlation_id,
                causation_id=event.context.causation_id,
                created_at=occurred,
                updated_at=occurred,
                provenance=_event_provenance(event),
            )
            continue

        if task is None:
            continue

        if event.event_type == "task.updated" and event.subject_id == task_id:
            title = event.payload.get("title", task.title)
            objective = event.payload.get("objective", task.description)
            if not isinstance(title, str) or not isinstance(objective, str):
                raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid task.updated payload")
            metadata_patch = event.payload.get("metadata")
            if metadata_patch is None:
                metadata = dict(task.metadata)
            elif isinstance(metadata_patch, dict):
                metadata = {**dict(task.metadata), **metadata_patch}
            else:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "invalid task.updated metadata payload",
                )
            task = replace(
                task,
                title=title,
                description=objective,
                metadata=metadata,
                updated_at=_timestamp(event),
                causation_id=event.context.causation_id,
                provenance=_event_provenance(event),
            )
            continue

        target = _TASK_EVENT_STATUS.get(event.event_type)
        if target is not None and event.subject_id == task_id:
            task = _transition_task(task, target, event)
            if target is not TaskStatus.WAITING:
                wait_reason = None
                blocked = False
            elif target is TaskStatus.WAITING:
                wait_reason = _optional_string(event, "reason")
                blocked = bool(event.payload.get("blocked", False))
            continue

        if event.event_type == "plan.created" and event.subject_id == task_id:
            plan_ref = _string(event, "plan_ref")
            continue

        if event.event_type == "run.created" and _string(event, "task_id") == task_id:
            if event.subject_id not in run_ids:
                run_ids.append(event.subject_id)
            continue

        attached_task_id = event.payload.get("task_id")
        belongs = event.subject_id == task_id or attached_task_id == task_id
        if event.event_type == "artifact.attached" and belongs:
            artifact_id = _string(event, "artifact_id")
            if artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            continue
        if event.event_type == "result.attached" and belongs:
            result_id = _string(event, "result_id")
            if result_id not in result_ids:
                result_ids.append(result_id)

    if task is None:
        raise ContractError(ErrorCode.NOT_FOUND, f"task not found: {task_id}")
    return TaskState(
        task=task,
        revision=len(events),
        plan_ref=plan_ref,
        run_ids=tuple(run_ids),
        artifact_ids=tuple(artifact_ids),
        result_ids=tuple(result_ids),
        wait_reason=wait_reason,
        blocked=blocked,
    )


def reduce_run(events: tuple[PlatformEvent, ...], run_id: str) -> RunState:
    run: Run | None = None
    backend_ref: str | None = None
    output: dict[str, JsonValue] = {}
    artifact_ids: list[str] = []
    result_ids: list[str] = []
    dispatch_attempts = 0
    recovery_required = False
    recovery_reason: str | None = None

    for event in events:
        if event.subject_type != "run" or event.subject_id != run_id:
            continue

        if event.event_type == "run.created":
            if run is not None:
                raise ContractError(ErrorCode.CONFLICT, f"duplicate run creation: {run_id}")
            owner_type = cast(OwnerType, _string(event, "owner_type"))
            owner = OwnerRef(type=owner_type, id=_string(event, "owner_id"))
            occurred = _timestamp(event)
            subject_type_value = _string(event, "subject_type")
            if subject_type_value not in {"task", "step"}:
                raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid run subject_type")
            subject_type = cast(Literal["task", "step"], subject_type_value)
            run = Run(
                id=run_id,
                subject_type=subject_type,
                subject_id=_string(event, "subject_id"),
                owner_ref=owner,
                correlation_id=event.context.correlation_id,
                attempt=_integer(event, "attempt"),
                project_id=event.context.project_id,
                causation_id=event.context.causation_id,
                created_at=occurred,
                updated_at=occurred,
                provenance=_event_provenance(event),
            )
            continue

        if run is None:
            continue

        if event.event_type == "run.dispatch_attempted":
            dispatch_attempts += 1
            continue
        if event.event_type == "run.recovery_required":
            recovery_required = True
            recovery_reason = _optional_string(event, "reason")
            continue
        if event.event_type == "run.recovery_cleared":
            recovery_required = False
            recovery_reason = None
            continue

        target = _RUN_EVENT_STATUS.get(event.event_type)
        if target is not None:
            run = _transition_run(run, target, event)
            if event.event_type == "run.running":
                backend_ref = _optional_string(event, "backend_ref") or backend_ref
            maybe_output = event.payload.get("output")
            if isinstance(maybe_output, dict):
                output = cast(dict[str, JsonValue], dict(maybe_output))
            if target in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.TIMED_OUT,
            }:
                recovery_required = False
                recovery_reason = None
            continue

        if event.event_type == "artifact.attached":
            artifact_id = _string(event, "artifact_id")
            if artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            continue
        if event.event_type == "result.attached":
            result_id = _string(event, "result_id")
            if result_id not in result_ids:
                result_ids.append(result_id)

    if run is None:
        raise ContractError(ErrorCode.NOT_FOUND, f"run not found: {run_id}")
    return RunState(
        run=run,
        revision=len(events),
        backend_ref=backend_ref,
        output=output,
        artifact_ids=tuple(artifact_ids),
        result_ids=tuple(result_ids),
        dispatch_attempts=dispatch_attempts,
        recovery_required=recovery_required,
        recovery_reason=recovery_reason,
    )
