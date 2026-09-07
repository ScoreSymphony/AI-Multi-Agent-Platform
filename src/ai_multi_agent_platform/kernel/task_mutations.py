"""Supported narrow Task mutation commands layered over kernel internals.

This module is part of the kernel implementation package, so it may compose private
kernel primitives. Callers outside ``ai_multi_agent_platform.kernel`` receive only
fixed domain commands and never arbitrary event specifications or repository access.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .kernel import PlatformKernel
from .models import TaskState
from .repository import CommandRecord

_TASK_MANAGEMENT_METADATA_KEY = "task_management"
_TASK_PLANNING_FIELDS = frozenset(
    {
        "priority",
        "due_at",
        "deadline_timezone",
        "not_before",
        "responsibility",
        "agent_assignment",
        "labels",
        "workspace_id",
        "parent_task_id",
        "dependencies",
        "blocking_reason",
        "effort_hint",
        "resource_hints",
        "archived",
        "hidden",
    }
)
_UPDATE_TASK_OPERATION = "update_task"
_UPDATE_TASK_EVENT = "task.updated"
_MOVE_OPERATION = "move_task_project"
_MOVE_EVENT = "task.project_reassigned"
_BULK_SCOPE = "task-project-reassignment:bulk"
_BULK_OPERATION = "move_task_project_bulk"
_BULK_EVENT = "task.project_bulk_move_reserved"


def _json_equivalent(left: object, right: object) -> bool:
    """Compare mutable JSON with the kernel's recursively frozen event representation."""

    if isinstance(left, Mapping):
        if not isinstance(right, Mapping):
            return False
        left_mapping = cast(Mapping[object, object], left)
        right_mapping = cast(Mapping[object, object], right)
        if len(left_mapping) != len(right_mapping):
            return False
        return all(
            key in right_mapping and _json_equivalent(value, right_mapping[key])
            for key, value in left_mapping.items()
        )
    if isinstance(left, (list, tuple)):
        if not isinstance(right, (list, tuple)) or len(left) != len(right):
            return False
        return all(_json_equivalent(a, b) for a, b in zip(left, right, strict=True))
    return left == right


class TaskMutationBoundary:
    """Expose supported canonical Task mutations without arbitrary event access."""

    def __init__(self, kernel: PlatformKernel) -> None:
        self._kernel = kernel

    async def update_planning_metadata(
        self,
        *,
        task_id: str,
        planning_metadata: Mapping[str, JsonValue],
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-management",
    ) -> TaskState:
        """Commit schema-owned planning metadata, including on lifecycle-terminal Tasks.

        The command can only emit ``task.updated`` with the canonical
        ``task_management`` metadata namespace. Lifecycle fields such as title,
        objective and status are therefore not representable at this boundary.
        """

        validate_id(task_id, "task")
        self._validate_planning_metadata(planning_metadata)
        metadata: dict[str, JsonValue] = {_TASK_MANAGEMENT_METADATA_KEY: dict(planning_metadata)}
        duplicate = await self._replayed_planning_metadata(
            task_id=task_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        if duplicate is not None:
            return duplicate

        task = await self._kernel.get_task(task_id)
        await self._kernel._commit_task_command(
            task=task,
            key=idempotency_key,
            operation=_UPDATE_TASK_OPERATION,
            event_specs=(
                (
                    _UPDATE_TASK_EVENT,
                    "task",
                    task_id,
                    {"metadata": metadata},
                    (),
                ),
            ),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        committed = await self._replayed_planning_metadata(
            task_id=task_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        if committed is None:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "Task planning metadata commit produced no canonical command record",
            )
        return committed

    async def replayed_project_reassignment(
        self,
        *,
        task_id: str,
        destination_project_id: str | None,
        idempotency_key: str,
    ) -> TaskState | None:
        """Return the canonical Task when an identical Project move already committed."""

        validate_id(task_id, "task")
        if destination_project_id is not None:
            validate_id(destination_project_id, "project")
        record = await self._kernel._task_command(task_id, idempotency_key, _MOVE_OPERATION)
        if record is None:
            return None
        event = await self._command_event(record, _MOVE_EVENT)
        if event.payload.get("destination_project_id") != destination_project_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Idempotency-Key is already bound to a different Task Project destination",
            )
        return await self._kernel.get_task(task_id)

    async def reassign_project(
        self,
        *,
        task_id: str,
        source_project_id: str | None,
        destination_project_id: str | None,
        expected_revision: int,
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-project-reassignment",
    ) -> TaskState:
        """Commit one preflighted canonical Project reassignment.

        Authorization, ownership, relationship and Workspace compatibility stay in the
        reassignment application service. This boundary owns only the fixed canonical
        event shape, optimistic stream commit and provenance snapshot.
        """

        replayed = await self.replayed_project_reassignment(
            task_id=task_id,
            destination_project_id=destination_project_id,
            idempotency_key=idempotency_key,
        )
        if replayed is not None:
            return replayed

        if source_project_id is not None:
            validate_id(source_project_id, "project")
        task = await self._kernel.get_task(task_id)
        if task.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Task changed after Project reassignment preflight; retry the move",
            )
        if task.task.project_id != source_project_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Task Project scope changed after reassignment preflight; retry the move",
            )

        retained_history: dict[str, JsonValue] = {
            "plan_ref": task.plan_ref,
            "step_ids": list(task.step_ids),
            "run_ids": list(task.run_ids),
            "artifact_ids": list(task.artifact_ids),
            "result_ids": list(task.result_ids),
        }
        await self._kernel._commit_task_command(
            task=task,
            key=idempotency_key,
            operation=_MOVE_OPERATION,
            event_specs=(
                (
                    _MOVE_EVENT,
                    "task",
                    task_id,
                    {
                        "source_project_id": source_project_id,
                        "destination_project_id": destination_project_id,
                        "historical_scope_policy": "retain_original_event_and_run_scope",
                        "future_execution_scope": destination_project_id,
                        "retained_history": retained_history,
                    },
                    (),
                ),
            ),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        moved = await self.replayed_project_reassignment(
            task_id=task_id,
            destination_project_id=destination_project_id,
            idempotency_key=idempotency_key,
        )
        if moved is None or moved.task.project_id != destination_project_id:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "canonical Task Project move did not project the requested destination",
            )
        return moved

    async def bulk_project_reassignment_reserved(
        self,
        *,
        batch_digest: str,
        idempotency_key: str,
    ) -> bool:
        """Check the fixed bulk-move reservation idempotency contract."""

        if not batch_digest.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "bulk move digest must not be blank")
        record = await self._kernel._existing_command(
            _BULK_SCOPE,
            idempotency_key,
            _BULK_OPERATION,
        )
        if record is None:
            return False
        if record.result_id != batch_digest:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Idempotency-Key is already bound to a different Task Project bulk move set",
            )
        event = await self._command_event(record, _BULK_EVENT)
        if event.payload.get("batch_digest") != batch_digest:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "Task Project bulk idempotency record has no matching reservation event",
            )
        return True

    async def reserve_bulk_project_reassignment(
        self,
        *,
        anchor_task_id: str,
        batch_digest: str,
        moves: Sequence[Mapping[str, JsonValue]],
        idempotency_key: str,
        actor_ref: str | None,
        source: str = "task-project-reassignment",
    ) -> None:
        """Append the fixed non-atomic bulk preflight reservation event."""

        validate_id(anchor_task_id, "task")
        normalized = self._validate_bulk_moves(moves)
        first_task_id = normalized[0]["task_id"]
        assert isinstance(first_task_id, str)
        if first_task_id != anchor_task_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "bulk move anchor must match the first canonical move entry",
            )
        if await self.bulk_project_reassignment_reserved(
            batch_digest=batch_digest,
            idempotency_key=idempotency_key,
        ):
            return

        anchor = await self._kernel.get_task(anchor_task_id)
        events = self._kernel._build_events(
            task=anchor,
            causation_id=idempotency_key,
            actor_ref=actor_ref,
            source=source,
            event_specs=(
                (
                    _BULK_EVENT,
                    "task",
                    anchor_task_id,
                    {
                        "batch_digest": batch_digest,
                        "moves": cast(JsonValue, normalized),
                        "atomic": False,
                    },
                    (),
                ),
            ),
        )
        command = self._kernel._command(
            scope=_BULK_SCOPE,
            key=idempotency_key,
            operation=_BULK_OPERATION,
            stream_id=anchor_task_id,
            result_id=batch_digest,
            event=events[0],
        )
        result = await self._kernel._repository.commit(
            stream_id=anchor_task_id,
            expected_revision=anchor.revision,
            events=events,
            command=command,
        )
        if not result.applied:
            if not await self.bulk_project_reassignment_reserved(
                batch_digest=batch_digest,
                idempotency_key=idempotency_key,
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Task Project bulk reservation lost an idempotency race",
                )
            return
        await self._kernel._mirror(events)

    async def _replayed_planning_metadata(
        self,
        *,
        task_id: str,
        idempotency_key: str,
        metadata: Mapping[str, JsonValue],
    ) -> TaskState | None:
        record = await self._kernel._task_command(
            task_id,
            idempotency_key,
            _UPDATE_TASK_OPERATION,
        )
        if record is None:
            return None
        event = await self._command_event(record, _UPDATE_TASK_EVENT)
        if (
            not _json_equivalent(event.payload.get("metadata"), metadata)
            or "title" in event.payload
            or "objective" in event.payload
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Idempotency-Key is already bound to a different Task update",
            )
        return await self._kernel.get_task(task_id)

    async def _command_event(
        self,
        record: CommandRecord,
        expected_event_type: str,
    ) -> PlatformEvent:
        event = next(
            (
                item
                for item in await self._kernel.history(record.stream_id)
                if item.id == record.event_id
            ),
            None,
        )
        if event is None or event.event_type != expected_event_type:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"canonical command record has no matching {expected_event_type} event",
            )
        return event

    @staticmethod
    def _validate_planning_metadata(planning_metadata: Mapping[str, JsonValue]) -> None:
        fields = set(planning_metadata)
        missing = _TASK_PLANNING_FIELDS - fields
        unknown = fields - _TASK_PLANNING_FIELDS
        if missing or unknown:
            details: dict[str, JsonValue] = {}
            if missing:
                details["missing_fields"] = cast(JsonValue, sorted(missing))
            if unknown:
                details["unknown_fields"] = cast(JsonValue, sorted(unknown))
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Task planning metadata does not match the owned task_management schema",
                details=details,
            )

    @staticmethod
    def _validate_bulk_moves(
        moves: Sequence[Mapping[str, JsonValue]],
    ) -> list[dict[str, JsonValue]]:
        if not moves:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Task Project move batch is empty")
        normalized: list[dict[str, JsonValue]] = []
        for move in moves:
            if set(move) != {"task_id", "destination_project_id"}:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    (
                        "bulk Project move entries must contain only task_id and "
                        "destination_project_id"
                    ),
                )
            task_id = move["task_id"]
            destination = move["destination_project_id"]
            if not isinstance(task_id, str):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "bulk Project move task_id must be a string",
                )
            validate_id(task_id, "task")
            if destination is not None:
                if not isinstance(destination, str):
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST,
                        "bulk Project move destination_project_id must be a string or null",
                    )
                validate_id(destination, "project")
            normalized.append(
                {
                    "task_id": task_id,
                    "destination_project_id": destination,
                }
            )
        return normalized
