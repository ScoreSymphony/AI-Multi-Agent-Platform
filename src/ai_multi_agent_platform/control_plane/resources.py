"""Northbound resource serialization for the Control Plane."""

from __future__ import annotations

from typing import Literal

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Project
from ai_multi_agent_platform.kernel import RunState, TaskState

from .models import WorkspaceIdentity, json_object

ReferenceCollection = Literal["plans", "steps", "artifacts", "results"]


def project_resource(project: Project) -> dict[str, JsonValue]:
    return {
        "id": project.id,
        "type": "project",
        "name": project.name,
        "owner": {"type": project.owner_ref.type, "id": project.owner_ref.id},
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def workspace_resource(workspace: WorkspaceIdentity) -> dict[str, JsonValue]:
    return {
        "id": workspace.id,
        "type": "workspace",
        "project_id": workspace.project_id,
        "owner": {"type": workspace.owner_type, "id": workspace.owner_id},
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        "lifecycle": "identity_only",
    }


def task_resource(state: TaskState) -> dict[str, JsonValue]:
    task = state.task
    return {
        "id": state.task_id,
        "type": "task",
        "title": task.title,
        "objective": task.description,
        "status": state.status.value,
        "owner": {"type": task.owner_ref.type, "id": task.owner_ref.id},
        "project_id": task.project_id,
        "revision": state.revision,
        "plan_ref": state.plan_ref,
        "step_ids": list(state.step_ids),
        "run_ids": list(state.run_ids),
        "artifact_ids": list(state.artifact_ids),
        "result_ids": list(state.result_ids),
        "wait_reason": state.wait_reason,
        "blocked": state.blocked,
        "correlation_id": task.correlation_id,
        "causation_id": task.causation_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def run_resource(state: RunState) -> dict[str, JsonValue]:
    run = state.run
    return {
        "id": state.run_id,
        "type": "run",
        "task_id": state.task_id,
        "subject_type": run.subject_type,
        "subject_id": run.subject_id,
        "attempt": state.attempt,
        "status": state.status.value,
        "project_id": run.project_id,
        "correlation_id": run.correlation_id,
        "causation_id": run.causation_id,
        "trace_id": run.trace_id,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "output": dict(state.output),
        "artifact_ids": list(state.artifact_ids),
        "result_ids": list(state.result_ids),
        "recovery_required": state.recovery_required,
        "recovery_reason": state.recovery_reason,
    }


def references_for_task(
    task: TaskState,
    collection: ReferenceCollection,
) -> list[dict[str, JsonValue]]:
    task_id = task.task_id
    if collection == "plans":
        if task.plan_ref is None:
            return []
        return [
            {
                "id": task.plan_ref,
                "type": "plan",
                "task_id": task_id,
                "step_ids": list(task.step_ids),
            }
        ]
    if collection == "steps":
        return [
            {
                "id": step_id,
                "type": "step",
                "task_id": task_id,
                "plan_id": task.plan_ref,
            }
            for step_id in task.step_ids
        ]
    if collection == "artifacts":
        return [
            {
                "id": artifact_id,
                "type": "artifact",
                "task_id": task_id,
            }
            for artifact_id in task.artifact_ids
        ]
    return [
        {
            "id": result_id,
            "type": "result",
            "task_id": task_id,
        }
        for result_id in task.result_ids
    ]


def event_resource(event: object) -> dict[str, JsonValue]:
    resource = json_object(event)
    resource["type"] = "event"
    return resource


def deduplicate(items: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    by_id: dict[str, dict[str, JsonValue]] = {}
    for item in items:
        resource_id = item.get("id")
        if isinstance(resource_id, str):
            by_id[resource_id] = item
    return list(by_id.values())
