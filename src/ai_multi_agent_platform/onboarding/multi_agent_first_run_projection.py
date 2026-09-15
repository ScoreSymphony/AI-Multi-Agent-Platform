"""Product projection for the official multi-agent first-run workflow."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.contracts.types import JsonValue

from .multi_agent_first_run_support import FIRST_RUN_WORKFLOW

_EXECUTION_STEP_TITLE = "Produce the requested result"
_REVIEW_STEP_TITLE = "Review the exact produced result"


async def project_first_run_result(
    *,
    kernel: Any,
    coordination: Any,
    verification: Any,
    agents: dict[str, AgentRevisionRef],
    task_id: str,
    plan_id: str,
    project_id: str,
    workspace_id: str,
    goal_artifact_id: str,
) -> dict[str, JsonValue]:
    state = coordination.get_plan(plan_id)
    steps: list[dict[str, JsonValue]] = []
    result_ids: list[str] = []
    artifact_ids: list[str] = [goal_artifact_id]
    execution_result_id: str | None = None
    review_status = "incomplete"

    for step in state.steps:
        record = coordination.get_step_record(step.id)
        projected = await _project_step(kernel, task_id, step, record)
        steps.append(projected)
        result_ids.extend(_string_list(projected.get("result_ids")))
        artifact_ids.extend(_string_list(projected.get("artifact_ids")))
        if step.title == _EXECUTION_STEP_TITLE:
            ids = _string_list(projected.get("result_ids"))
            execution_result_id = ids[0] if ids else None
        if step.title == _REVIEW_STEP_TITLE:
            review_status = (
                "passed" if projected["status"] == "succeeded" else str(projected["status"])
            )

    verification_items = _verification_projection(
        verification.history(task_id=task_id), execution_result_id=execution_result_id
    )
    exact_review = next(
        (item for item in verification_items if item.get("is_final_result_review") is True),
        None,
    )
    task = await kernel.get_task(task_id)
    return {
        "id": task_id,
        "type": "multi_agent_first_run_result",
        "workflow": FIRST_RUN_WORKFLOW,
        "task_id": task_id,
        "task_status": task.status.value,
        "plan_id": plan_id,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "agents": {
            role: {"agent_id": ref.agent_id, "revision": ref.revision}
            for role, ref in agents.items()
        },
        "steps": steps,
        "result_id": execution_result_id,
        "result_ids": list(dict.fromkeys(result_ids)),
        "artifact_ids": list(dict.fromkeys(artifact_ids)),
        "review": {
            "step_status": review_status,
            "verification_status": (
                "missing" if exact_review is None else exact_review.get("outcome") or "pending"
            ),
            "verification_id": (
                None if exact_review is None else exact_review.get("verification_id")
            ),
        },
        "verification": verification_items,
        "trace": {
            "task_id": task_id,
            "plan_id": plan_id,
            "step_ids": [step.id for step in state.steps],
        },
    }


async def _project_step(
    kernel: Any,
    task_id: str,
    step: Any,
    record: Any,
) -> dict[str, JsonValue]:
    run_id = record.latest_run_id
    run_result_ids: list[str] = []
    run_artifact_ids: list[str] = []
    run_status: str | None = None
    if run_id is not None:
        run = await kernel.get_run(task_id, run_id)
        run_result_ids = list(run.result_ids)
        run_artifact_ids = list(run.artifact_ids)
        run_status = run.status.value
    assignment = step.assignment
    return {
        "step_id": step.id,
        "title": step.title,
        "status": step.status.value,
        "phase": record.phase.value,
        "depends_on": list(record.dependency_ids),
        "satisfied_dependencies": list(record.satisfied_dependency_ids),
        "agent_id": None if assignment is None else assignment.agent_id,
        "agent_revision": None if assignment is None else assignment.agent_revision,
        "run_id": run_id,
        "run_status": run_status,
        "result_ids": run_result_ids,
        "artifact_ids": run_artifact_ids,
    }


def _verification_projection(
    history: Any,
    *,
    execution_result_id: str | None,
) -> list[dict[str, JsonValue]]:
    payload: list[dict[str, JsonValue]] = []
    for request, result in history:
        payload.append(
            {
                "verification_id": request.verification_id,
                "stage_id": request.stage_id,
                "status": request.status.value,
                "subject_type": request.subject.subject_type,
                "subject_id": request.subject.subject_id,
                "outcome": None if result is None else result.outcome.value,
                "is_final_result_review": (
                    execution_result_id is not None
                    and request.subject.subject_type == "result"
                    and request.subject.subject_id == execution_result_id
                ),
            }
        )
    return payload


def _string_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
