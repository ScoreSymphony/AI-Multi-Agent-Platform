"""Canonical northbound Task Project reassignment commands for issue #157."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.task_reassignment import (
    DefaultTaskProjectCompatibilityPolicy,
    TaskProjectMoveRequest,
    TaskProjectReassignmentService,
)

from .approval_decision_composition import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    ControlPlaneHTTP,
)
from .approval_decision_composition import ControlPlane as _CurrentControlPlane
from .approval_decision_composition import build_openapi as _build_current_openapi
from .models import RequestContext

TASK_PROJECT_MOVE_COMMAND = "task.project.move"
TASK_PROJECT_BULK_MOVE_COMMAND = "task.project.bulk-move"
TASK_PROJECT_MOVE_COMMANDS = (TASK_PROJECT_MOVE_COMMAND, TASK_PROJECT_BULK_MOVE_COMMAND)
TASK_PROJECT_MOVE_ACTION = "task:move-project"


class ControlPlane(_CurrentControlPlane):
    """Current Control Plane plus canonical Task Project reassignment."""

    def __init__(
        self,
        *args: Any,
        task_project_reassignment: TaskProjectReassignmentService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._task_project_reassignment = (
            task_project_reassignment
            or TaskProjectReassignmentService(
                kernel=self._kernel,
                task_management=self.task_management,
                project_resolver=self.scopes.get_project,
                workspace_project_resolver=self._workspace_project_id,
                task_ids=self._task_ids,
                compatibility=DefaultTaskProjectCompatibilityPolicy(self.organization_service),
            )
        )
        self.register_command(TASK_PROJECT_MOVE_COMMAND, self._move_task_project_command)
        self.register_command(
            TASK_PROJECT_BULK_MOVE_COMMAND,
            self._bulk_move_task_project_command,
        )

    @property
    def task_project_reassignment(self) -> TaskProjectReassignmentService:
        return self._task_project_reassignment

    async def _move_task_project_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        request = _move_request(resource_ref, payload)
        await self._authorize_project_move(context, request)
        key = _require_key(context)
        await self._task_project_reassignment.move(
            request,
            idempotency_key=f"{key}:task-project-move:{request.task_id}",
            actor_ref=context.actor.principal_ref,
            source="control-plane.task-project-reassignment",
        )
        return await self.get_task(context, request.task_id)

    async def _bulk_move_task_project_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != "tasks":
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "bulk Task Project move resource_ref must be 'tasks'",
            )
        raw_moves = payload.get("moves")
        if not isinstance(raw_moves, list) or not raw_moves:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "moves must be a non-empty array",
            )
        requests: list[TaskProjectMoveRequest] = []
        for raw_move in raw_moves:
            if not isinstance(raw_move, dict):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "each bulk Task Project move must be an object",
                )
            task_id = raw_move.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "bulk Task Project move task_id must be a non-blank string",
                )
            requests.append(_move_request(task_id, raw_move))

        # All authorization and relationship checks happen before the first append.
        for request in requests:
            await self._authorize_project_move(context, request)
        prepared = await self._task_project_reassignment.prepare_batch(requests)

        key = _require_key(context)
        items: list[JsonValue] = []
        for item in prepared:
            moved = await self._task_project_reassignment.commit(
                item,
                idempotency_key=f"{key}:task-project-move:{item.task.task_id}",
                actor_ref=context.actor.principal_ref,
                source="control-plane.task-project-reassignment",
            )
            items.append(
                {
                    "task_id": moved.task_id,
                    "project_id": moved.task.project_id,
                    "revision": moved.revision,
                }
            )
        return {
            "id": f"bulk:{key}",
            "type": "task-project-bulk-move-result",
            "atomic": False,
            "authorization_preflighted": True,
            "relationship_preflighted": True,
            "count": len(items),
            "items": items,
        }

    async def _authorize_project_move(
        self,
        context: RequestContext,
        request: TaskProjectMoveRequest,
    ) -> None:
        task = await self._kernel.get_task(request.task_id)
        await self._authorize_for_task(
            context,
            TASK_PROJECT_MOVE_ACTION,
            request.task_id,
            task,
        )
        await self._authorize_project_scope(
            context,
            project_id=task.task.project_id,
            fallback_owner_type=task.task.owner_ref.type,
            fallback_owner_id=task.task.owner_ref.id,
            resource_suffix="source",
        )
        await self._authorize_project_scope(
            context,
            project_id=request.destination_project_id,
            fallback_owner_type=task.task.owner_ref.type,
            fallback_owner_id=task.task.owner_ref.id,
            resource_suffix="destination",
        )

    async def _authorize_project_scope(
        self,
        context: RequestContext,
        *,
        project_id: str | None,
        fallback_owner_type: str,
        fallback_owner_id: str,
        resource_suffix: str,
    ) -> None:
        if project_id is None:
            await self._authorize(
                context,
                TASK_PROJECT_MOVE_ACTION,
                f"personal:{resource_suffix}:{fallback_owner_type}:{fallback_owner_id}",
                owner_type=fallback_owner_type,
                owner_id=fallback_owner_id,
                project_id=None,
            )
            return
        project = self.scopes.get_project(project_id)
        await self._authorize(
            context,
            TASK_PROJECT_MOVE_ACTION,
            project.id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
            project_id=project.id,
        )


def _move_request(
    task_id: str,
    payload: dict[str, JsonValue],
) -> TaskProjectMoveRequest:
    if "destination_project_id" not in payload:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "destination_project_id is required and may be null for personal scope",
        )
    destination = payload.get("destination_project_id")
    if destination is not None and (
        not isinstance(destination, str) or not destination.strip()
    ):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "destination_project_id must be a canonical Project ID or null",
        )
    try:
        return TaskProjectMoveRequest(
            task_id=task_id,
            destination_project_id=destination if isinstance(destination, str) else None,
        )
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


def _require_key(context: RequestContext) -> str:
    if context.idempotency_key is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Idempotency-Key is required for Task Project reassignment",
        )
    return context.idempotency_key


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
    include_approval_decisions: bool = False,
) -> dict[str, Any]:
    commands = tuple(sorted(set((*extension_commands, *TASK_PROJECT_MOVE_COMMANDS))))
    specification = _build_current_openapi(
        extension_collections=extension_collections,
        extension_commands=commands,
        include_conversations=include_conversations,
        include_approval_decisions=include_approval_decisions,
    )
    specification["x-task-project-reassignment"] = {
        "commands": list(TASK_PROJECT_MOVE_COMMANDS),
        "event": "task.project_reassigned",
        "historical_scope": "retained",
        "future_execution_scope": "destination_project_id",
        "bulk_atomic": False,
        "connected_bulk_moves": "rejected_without_multi_stream_atomic_commit",
    }
    return specification


__all__ = [
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "TASK_PROJECT_BULK_MOVE_COMMAND",
    "TASK_PROJECT_MOVE_ACTION",
    "TASK_PROJECT_MOVE_COMMAND",
    "TASK_PROJECT_MOVE_COMMANDS",
    "build_openapi",
]
