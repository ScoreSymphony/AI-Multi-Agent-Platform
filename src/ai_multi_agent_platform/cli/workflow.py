"""Task-scoped CLI views for canonical durable Plan/Step coordination progress."""

from __future__ import annotations

import argparse
from collections import Counter
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .client import ClientResponse, ControlPlaneClient, TransportError
from .profiles import ProfileError


def add_task_workflow_parser(
    task_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    workflow = task_commands.add_parser(
        "workflow",
        help="inspect durable Plan/Step workflow progress",
    )
    workflow.set_defaults(command="workflow")
    commands = workflow.add_subparsers(dest="workflow_command", required=True)
    for name, help_text in (
        ("show", "show workflow summary"),
        ("steps", "show all workflow steps"),
        ("waits", "show waiting workflow steps"),
        ("retries", "show retrying or retried workflow steps"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("task_id")


def execute_task_workflow(
    args: argparse.Namespace,
    client: ControlPlaneClient,
) -> ClientResponse:
    task_response = client.get(f"/tasks/{_segment(args.task_id)}")
    task = _require_object(task_response.body, "Task")
    task_id = task.get("id")
    if task_id != args.task_id:
        raise TransportError("Control Plane returned a Task with a mismatched canonical ID")

    plan_ref = task.get("plan_ref")
    if plan_ref is None:
        return _without_plan(task_response, args.task_id, str(args.workflow_command))
    if not isinstance(plan_ref, str) or not plan_ref:
        raise TransportError("Control Plane Task plan_ref must be a canonical string ID or null")

    projection_response = client.get(f"/plan-coordination/{_segment(plan_ref)}")
    projection = _require_object(projection_response.body, "Plan coordination projection")
    projection_task_id = projection.get("task_id")
    projection_plan_id = projection.get("id")
    if projection_task_id != args.task_id:
        raise TransportError(
            "Control Plane Plan coordination projection belongs to a different Task"
        )
    if projection_plan_id != plan_ref:
        raise TransportError(
            "Control Plane Plan coordination projection has a mismatched canonical Plan ID"
        )

    steps = _steps(projection)
    command = str(args.workflow_command)
    if command == "show":
        body = _summary(projection, steps)
    elif command == "steps":
        body = _page(projection, steps)
    elif command == "waits":
        body = _page(
            projection,
            [
                step
                for step in steps
                if step.get("wait_type") is not None or step.get("wait_deadline_at") is not None
            ],
        )
    elif command == "retries":
        body = _page(
            projection,
            [step for step in steps if step.get("retry_due_at") is not None or _attempt(step) > 1],
        )
    else:
        raise ProfileError(f"unsupported task workflow command: {command}")

    return ClientResponse(
        status=projection_response.status,
        body=body,
        request_id=projection_response.request_id,
        correlation_id=projection_response.correlation_id,
        api_version=projection_response.api_version,
    )


def _without_plan(
    response: ClientResponse,
    task_id: str,
    command: str,
) -> ClientResponse:
    if command == "show":
        body: JsonValue = {
            "task_id": task_id,
            "plan_id": None,
            "plan_revision": None,
            "step_count": 0,
            "status_counts": {},
            "waiting_step_count": 0,
            "retry_step_count": 0,
        }
    elif command in {"steps", "waits", "retries"}:
        body = {
            "task_id": task_id,
            "plan_id": None,
            "plan_revision": None,
            "items": [],
            "total": 0,
        }
    else:
        raise ProfileError(f"unsupported task workflow command: {command}")
    return ClientResponse(
        status=response.status,
        body=body,
        request_id=response.request_id,
        correlation_id=response.correlation_id,
        api_version=response.api_version,
    )


def _summary(
    projection: dict[str, JsonValue],
    steps: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    statuses = Counter(
        cast(str, step["status"]) for step in steps if isinstance(step.get("status"), str)
    )
    return {
        "task_id": projection.get("task_id"),
        "plan_id": projection.get("id"),
        "plan_revision": projection.get("plan_revision"),
        "step_count": len(steps),
        "status_counts": dict(sorted(statuses.items())),
        "waiting_step_count": sum(
            step.get("wait_type") is not None or step.get("wait_deadline_at") is not None
            for step in steps
        ),
        "retry_step_count": sum(
            step.get("retry_due_at") is not None or _attempt(step) > 1 for step in steps
        ),
    }


def _page(
    projection: dict[str, JsonValue],
    steps: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    return {
        "task_id": projection.get("task_id"),
        "plan_id": projection.get("id"),
        "plan_revision": projection.get("plan_revision"),
        "items": cast(list[JsonValue], steps),
        "total": len(steps),
    }


def _steps(projection: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    raw = projection.get("steps")
    if not isinstance(raw, list):
        raise TransportError("Control Plane Plan coordination projection must contain a step list")
    steps: list[dict[str, JsonValue]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TransportError("Control Plane Plan coordination step must be a JSON object")
        steps.append(cast(dict[str, JsonValue], item))
    return steps


def _attempt(step: dict[str, JsonValue]) -> int:
    value = step.get("current_attempt")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _require_object(value: JsonValue, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise TransportError(f"Control Plane {label} response must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _segment(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
