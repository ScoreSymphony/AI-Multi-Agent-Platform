"""Focused Task command handling for the canonical platform kernel.

The public ``PlatformKernel`` remains the supported façade. This component owns Task
creation and Task lifecycle command mechanics while depending only on the narrow kernel
capabilities required to validate and commit canonical Task transitions.
"""

from __future__ import annotations

from typing import Literal, Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Task, TaskStatus, new_id, validate_id
from ai_multi_agent_platform.verification import CompletionAuthority

from .models import RunState, TaskState
from .repository import CommandRecord, EventRepository

OwnerType = Literal["user", "organization", "team", "service"]
EventSpec = tuple[
    str,
    str,
    str,
    dict[str, JsonValue],
    tuple[AdapterMetadata, ...],
]

_TASK_CREATE_SCOPE = "task:create"
_KERNEL_SOURCE = "platform-kernel"


class TaskCommandKernelHost(Protocol):
    """Internal kernel capabilities required by Task command handling."""

    _repository: EventRepository
    _completion_authority: CompletionAuthority | None

    async def get_task(self, task_id: str) -> TaskState: ...

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState: ...

    @staticmethod
    def _require_key(key: str) -> None: ...

    async def _existing_command(
        self,
        scope: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None: ...

    @staticmethod
    def _require_same_command(
        record: CommandRecord | None,
        operation: str,
        key: str,
    ) -> CommandRecord: ...

    @staticmethod
    def _event(
        *,
        stream_id: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
        causation_id: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None,
        actor_ref: str,
        source: str,
        revision: int,
        payload: dict[str, JsonValue],
        adapter_metadata: tuple[AdapterMetadata, ...] = (),
    ) -> PlatformEvent: ...

    @staticmethod
    def _command(
        *,
        scope: str,
        key: str,
        operation: str,
        stream_id: str,
        result_id: str,
        event: PlatformEvent,
    ) -> CommandRecord: ...

    async def _mirror(self, events: tuple[PlatformEvent, ...]) -> None: ...

    async def _task_command(
        self,
        task_id: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None: ...

    async def _latest_active_run(self, task: TaskState) -> RunState | None: ...

    async def _active_runs(self, task: TaskState) -> tuple[RunState, ...]: ...

    def _completion_task_spec(self, task_id: str) -> EventSpec: ...

    async def _commit_task_command(
        self,
        *,
        task: TaskState,
        key: str,
        operation: str,
        event_specs: tuple[EventSpec, ...],
        result_id: str,
        actor_ref: str | None,
        source: str,
    ) -> CommandRecord: ...


class KernelTaskCommands:
    """Apply canonical Task commands while preserving the ``PlatformKernel`` façade."""

    def __init__(self, host: TaskCommandKernelHost) -> None:
        self._host = host

    async def create_task(
        self,
        *,
        idempotency_key: str,
        title: str,
        objective: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None = None,
        task_id: str | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        """Create exactly one canonical Task for a retriable logical command."""

        self._host._require_key(idempotency_key)
        existing = await self._host._existing_command(
            _TASK_CREATE_SCOPE,
            idempotency_key,
            "create_task",
        )
        if existing is not None:
            return await self._host.get_task(existing.result_id)

        if not title.strip() or not objective.strip() or not owner_id.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "task title/objective/owner must not be blank",
            )
        if owner_type not in {"user", "organization", "team", "service"}:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"unsupported owner type: {owner_type}")

        canonical_task_id = task_id or new_id("task")
        validate_id(canonical_task_id, "task")
        Task(
            id=canonical_task_id,
            title=title,
            description=objective,
            owner_ref=OwnerRef(type=owner_type, id=owner_id),
            project_id=project_id,
            correlation_id=canonical_task_id,
            causation_id=idempotency_key,
        )

        event = self._host._event(
            stream_id=canonical_task_id,
            event_type="task.created",
            subject_type="task",
            subject_id=canonical_task_id,
            causation_id=idempotency_key,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            actor_ref=actor_ref or f"{owner_type}:{owner_id}",
            source=source,
            revision=1,
            payload={
                "title": title,
                "objective": objective,
                "owner_type": owner_type,
                "owner_id": owner_id,
            },
        )
        command = self._host._command(
            scope=_TASK_CREATE_SCOPE,
            key=idempotency_key,
            operation="create_task",
            stream_id=canonical_task_id,
            result_id=canonical_task_id,
            event=event,
        )
        result = await self._host._repository.commit(
            stream_id=canonical_task_id,
            expected_revision=0,
            events=(event,),
            command=command,
        )
        if not result.applied:
            duplicate = self._host._require_same_command(
                result.command,
                "create_task",
                idempotency_key,
            )
            return await self._host.get_task(duplicate.result_id)
        await self._host._mirror((event,))
        return await self._host.get_task(canonical_task_id)

    async def update_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        title: str | None = None,
        objective: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self._host.get_task(task_id)
        duplicate = await self._host._task_command(task_id, idempotency_key, "update_task")
        if duplicate is not None:
            return await self._host.get_task(task_id)
        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} is terminal")
        if title is None and objective is None and metadata is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "task update contains no changes")
        if title is not None and not title.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "task title must not be blank")
        if objective is not None and not objective.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "task objective must not be blank")

        payload: dict[str, JsonValue] = {}
        if title is not None:
            payload["title"] = title
        if objective is not None:
            payload["objective"] = objective
        if metadata is not None:
            payload["metadata"] = metadata
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="update_task",
            event_specs=(("task.updated", "task", task_id, payload, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)

    async def ready_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "ready_task") is not None:
            return await self._host.get_task(task_id)
        if task.status not in {TaskStatus.DRAFT, TaskStatus.FAILED}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot become ready from {task.status.value}",
            )
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="ready_task",
            event_specs=(("task.ready", "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)

    async def wait_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str,
        blocked: bool = False,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "wait_task") is not None:
            return await self._host.get_task(task_id)
        if task.status is not TaskStatus.RUNNING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot wait from {task.status.value}",
            )
        if not reason.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "waiting reason must not be blank")
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="wait_task",
            event_specs=(
                ("task.waiting", "task", task_id, {"reason": reason, "blocked": blocked}, ()),
            ),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)

    async def resume_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "resume_task") is not None:
            return await self._host.get_task(task_id)
        if task.status is not TaskStatus.WAITING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot resume from {task.status.value}",
            )
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="resume_task",
            event_specs=(("task.resumed", "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)

    async def complete_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "complete_task") is not None:
            return await self._host.get_task(task_id)
        verification_wait = (
            self._host._completion_authority is not None
            and task.status is TaskStatus.WAITING
            and task.wait_reason is not None
            and task.wait_reason.startswith("verification:")
        )
        if task.status is not TaskStatus.RUNNING and not verification_wait:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot succeed from {task.status.value}",
            )
        active = await self._host._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot succeed while run {active.run_id} is {active.status.value}",
            )
        completion_spec = self._host._completion_task_spec(task_id)
        specs: list[EventSpec] = []
        if task.status is TaskStatus.WAITING and completion_spec[0] == "task.succeeded":
            specs.append(
                (
                    "task.resumed",
                    "task",
                    task_id,
                    {"verification_completed": True},
                    (),
                )
            )
        specs.append(completion_spec)
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="complete_task",
            event_specs=tuple(specs),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)

    async def fail_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "fail_task") is not None:
            return await self._host.get_task(task_id)
        if task.status not in {TaskStatus.RUNNING, TaskStatus.WAITING}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot fail from {task.status.value}",
            )
        active = await self._host._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot fail while run {active.run_id} is {active.status.value}",
            )
        payload: dict[str, JsonValue] = {}
        if reason is not None:
            payload["reason"] = reason
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="fail_task",
            event_specs=(("task.failed", "task", task_id, payload, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "cancel_task") is not None:
            return await self._host.get_task(task_id)
        if task.status is TaskStatus.SUCCEEDED:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} already succeeded")
        if task.status is TaskStatus.FAILED:
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical lifecycle requires failed tasks to be retried to ready "
                "rather than cancelled",
            )
        if task.status not in {
            TaskStatus.DRAFT,
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
            TaskStatus.CANCELLED,
        }:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} cannot be cancelled")

        active_runs = await self._host._active_runs(task)
        step_runs = tuple(run for run in active_runs if run.run.subject_type == "step")
        task_runs = tuple(run for run in active_runs if run.run.subject_type == "task")
        for run in (*step_runs, *task_runs):
            await self._host.cancel_run(
                idempotency_key=f"{idempotency_key}:run:{run.run_id}",
                task_id=task_id,
                run_id=run.run_id,
                actor_ref=actor_ref,
                source=source,
            )

        refreshed = await self._host.get_task(task_id)
        remaining = await self._host._active_runs(refreshed)
        if remaining:
            remaining_ids = ", ".join(run.run_id for run in remaining)
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cancellation is incomplete; active runs: {remaining_ids}",
            )

        if refreshed.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
            await self._host._commit_task_command(
                task=refreshed,
                key=idempotency_key,
                operation="cancel_task",
                event_specs=(
                    (
                        "task.cancel_lost_race",
                        "task",
                        task_id,
                        {"status": refreshed.status.value},
                        (),
                    ),
                ),
                result_id=task_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self._host.get_task(task_id)

        event_type = (
            "task.cancel_acknowledged"
            if refreshed.status is TaskStatus.CANCELLED
            else "task.cancelled"
        )
        await self._host._commit_task_command(
            task=refreshed,
            key=idempotency_key,
            operation="cancel_task",
            event_specs=((event_type, "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)


__all__ = ["KernelTaskCommands"]
