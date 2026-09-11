"""Focused Run command handling for the canonical platform kernel.

The public ``PlatformKernel`` remains the supported façade. This component owns Run
command orchestration and output attachment commands while lifecycle reconciliation and
canonical commit primitives stay behind narrow internal kernel capabilities.
"""

from __future__ import annotations

from typing import Literal, Protocol

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.domain import (
    Run,
    RunStatus,
    TaskStatus,
    new_id,
    validate_id,
    validate_subject_id,
)

from .models import TERMINAL_RUN_STATUSES, RunState, TaskState
from .repository import CommandRecord

RunSubjectType = Literal["task", "step"]
EventSpec = tuple[
    str,
    str,
    str,
    dict[str, JsonValue],
    tuple[AdapterMetadata, ...],
]

_KERNEL_SOURCE = "platform-kernel"


class RunCommandKernelHost(Protocol):
    """Internal kernel capabilities required by Run command handling."""

    _lifecycle: LifecycleBackend

    async def get_task(self, task_id: str) -> TaskState: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    async def plan_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState: ...

    async def _task_command(
        self,
        task_id: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None: ...

    async def _existing_command(
        self,
        scope: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None: ...

    def _validate_run_subject(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> None: ...

    async def _active_runs(self, task: TaskState) -> tuple[RunState, ...]: ...

    async def _active_run_for_subject(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> RunState | None: ...

    async def _next_attempt(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> int: ...

    async def _latest_active_run(self, task: TaskState) -> RunState | None: ...

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

    async def _reconcile_started_run(
        self, task_id: str, run_id: str, causation_id: str
    ) -> None: ...

    async def _dispatch_started_run(
        self,
        *,
        task_id: str,
        run_id: str,
        causation_id: str,
        actor_ref: str | None,
        source: str,
    ) -> None: ...

    def _context(self, task: TaskState, causation_id: str) -> OperationContext: ...

    async def _mark_recovery_required(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        causation_id: str,
    ) -> None: ...

    async def _apply_snapshot_command(
        self,
        *,
        task_id: str,
        run_id: str,
        snapshot: ExecutionSnapshot,
        key: str,
        operation: str,
        actor_ref: str | None,
        source: str,
    ) -> None: ...

    async def _apply_terminal_command(
        self,
        *,
        task: TaskState,
        run: RunState,
        target: RunStatus,
        output: dict[str, JsonValue],
        key: str,
        operation: str,
        actor_ref: str | None,
        source: str,
        adapter_metadata: tuple[AdapterMetadata, ...],
        allow_queued_cancel: bool = False,
    ) -> None: ...

    async def _finish_cancel(self, task_id: str, run_id: str, causation_id: str) -> None: ...

    def _invalidate_completion_subject(self, task_id: str) -> None: ...


class KernelRunCommands:
    """Apply canonical Run and output-attachment commands behind ``PlatformKernel``."""

    def __init__(self, host: RunCommandKernelHost) -> None:
        self._host = host

    async def create_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        subject_type: RunSubjectType = "task",
        subject_id: str | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self._host.get_task(task_id)
        duplicate = await self._host._task_command(task_id, idempotency_key, "create_run")
        if duplicate is not None:
            return await self._host.get_run(task_id, duplicate.result_id)
        canonical_subject_id = subject_id or task_id
        validate_subject_id(subject_type, canonical_subject_id)
        self._host._validate_run_subject(task, subject_type, canonical_subject_id)
        allowed_task_statuses = (
            {TaskStatus.READY} if subject_type == "task" else {TaskStatus.READY, TaskStatus.RUNNING}
        )
        if task.status not in allowed_task_statuses:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot create a {subject_type} run from {task.status.value}",
            )
        active_runs = await self._host._active_runs(task)
        active_modes = {active.run.subject_type for active in active_runs}
        if active_modes and subject_type not in active_modes:
            raise ContractError(
                ErrorCode.CONFLICT,
                "task-level and step-level runs cannot be active at the same time",
            )
        active_subject_run = await self._host._active_run_for_subject(
            task, subject_type, canonical_subject_id
        )
        if active_subject_run is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"subject {canonical_subject_id} already has active run "
                f"{active_subject_run.run_id}",
            )
        run_id = new_id("run")
        attempt = await self._host._next_attempt(task, subject_type, canonical_subject_id)
        Run(
            id=run_id,
            subject_type=subject_type,
            subject_id=canonical_subject_id,
            owner_ref=task.task.owner_ref,
            correlation_id=task_id,
            attempt=attempt,
            project_id=task.task.project_id,
            causation_id=idempotency_key,
        )
        payload: dict[str, JsonValue] = {
            "task_id": task_id,
            "subject_type": subject_type,
            "subject_id": canonical_subject_id,
            "attempt": attempt,
            "owner_type": task.task.owner_ref.type,
            "owner_id": task.task.owner_ref.id,
        }
        if task.plan_ref is not None:
            payload["plan_ref"] = task.plan_ref
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="create_run",
            event_specs=(("run.created", "run", run_id, payload, ()),),
            result_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_run(task_id, run_id)

    async def retry_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self._host.get_task(task_id)
        duplicate = await self._host._task_command(task_id, idempotency_key, "retry_task")
        if duplicate is not None:
            return await self._host.get_run(task_id, duplicate.result_id)
        if task.status is not TaskStatus.FAILED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot retry from {task.status.value}",
            )
        active = await self._host._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot retry while run {active.run_id} is {active.status.value}",
            )
        run_id = new_id("run")
        attempt = await self._host._next_attempt(task, "task", task_id)
        payload: dict[str, JsonValue] = {
            "task_id": task_id,
            "subject_type": "task",
            "subject_id": task_id,
            "attempt": attempt,
            "owner_type": task.task.owner_ref.type,
            "owner_id": task.task.owner_ref.id,
        }
        if task.plan_ref is not None:
            payload["plan_ref"] = task.plan_ref
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="retry_task",
            event_specs=(
                ("task.ready", "task", task_id, {"retry": True}, ()),
                ("run.created", "run", run_id, payload, ()),
            ),
            result_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_run(task_id, run_id)

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        duplicate = await self._host._task_command(task_id, idempotency_key, "start_run")
        if duplicate is not None:
            if duplicate.result_id != run_id:
                raise ContractError(ErrorCode.CONFLICT, "start command belongs to another run")
            if run.status in {RunStatus.STARTING, RunStatus.RUNNING}:
                await self._host._reconcile_started_run(task_id, run_id, f"retry:{idempotency_key}")
            return await self._host.get_run(task_id, run_id)
        allowed_task_statuses = (
            {TaskStatus.READY}
            if run.run.subject_type == "task"
            else {TaskStatus.READY, TaskStatus.RUNNING}
        )
        if task.status not in allowed_task_statuses:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot start a {run.run.subject_type} run "
                f"from {task.status.value}",
            )
        other_active_runs = tuple(
            active for active in await self._host._active_runs(task) if active.run_id != run_id
        )
        if any(active.run.subject_type != run.run.subject_type for active in other_active_runs):
            raise ContractError(
                ErrorCode.CONFLICT,
                "task-level and step-level runs cannot be active at the same time",
            )
        if run.status is not RunStatus.QUEUED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"run {run_id} cannot start from {run.status.value}",
            )

        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="start_run",
            event_specs=(("run.starting", "run", run_id, {}, ()),),
            result_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        await self._host._dispatch_started_run(
            task_id=task_id,
            run_id=run_id,
            causation_id=idempotency_key,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_run(task_id, run_id)

    async def start_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        """Convenience fake/reference flow: plan if needed, create run, start it."""

        task = await self._host.get_task(task_id)
        existing_run_command = await self._host._existing_command(
            task_id, f"{idempotency_key}:create-run", "create_run"
        )
        if existing_run_command is not None:
            return await self.start_run(
                idempotency_key=f"{idempotency_key}:start-run",
                task_id=task_id,
                run_id=existing_run_command.result_id,
                actor_ref=actor_ref,
                source=source,
            )
        if task.status is not TaskStatus.READY:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot start from {task.status.value}",
            )
        if task.plan_ref is None:
            await self._host.plan_task(
                idempotency_key=f"{idempotency_key}:plan",
                task_id=task_id,
                actor_ref=actor_ref,
                source=source,
            )
        run = await self.create_run(
            idempotency_key=f"{idempotency_key}:create-run",
            task_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.start_run(
            idempotency_key=f"{idempotency_key}:start-run",
            task_id=task_id,
            run_id=run.run_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def refresh_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        duplicate = await self._host._task_command(task_id, idempotency_key, "refresh_run")
        if duplicate is not None:
            return await self._host.get_run(task_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            await self._host._commit_task_command(
                task=task,
                key=idempotency_key,
                operation="refresh_run",
                event_specs=(("run.refresh_ignored_terminal", "run", run_id, {}, ()),),
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self._host.get_run(task_id, run_id)

        try:
            snapshot = await self._host._lifecycle.get(
                run_id, self._host._context(task, idempotency_key)
            )
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            await self._host._mark_recovery_required(
                task_id=task_id,
                run_id=run_id,
                reason="backend_not_found_during_refresh",
                causation_id=idempotency_key,
            )
            refreshed_task = await self._host.get_task(task_id)
            await self._host._commit_task_command(
                task=refreshed_task,
                key=idempotency_key,
                operation="refresh_run",
                event_specs=(("run.refresh_not_found", "run", run_id, {}, ()),),
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self._host.get_run(task_id, run_id)

        await self._host._apply_snapshot_command(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            key=idempotency_key,
            operation="refresh_run",
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_run(task_id, run_id)

    async def record_run_outcome(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        status: RunStatus,
        output: dict[str, JsonValue] | None = None,
        actor_ref: str | None = None,
        source: str = "executor-callback",
        adapter_metadata: tuple[AdapterMetadata, ...] = (),
    ) -> RunState:
        if status not in TERMINAL_RUN_STATUSES:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "record_run_outcome requires terminal status",
            )
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        duplicate = await self._host._task_command(task_id, idempotency_key, "record_run_outcome")
        if duplicate is not None:
            return await self._host.get_run(task_id, run_id)

        if run.status in TERMINAL_RUN_STATUSES:
            if run.status is not status:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"run {run_id} is already {run.status.value}; "
                    f"cannot overwrite with {status.value}",
                )
            await self._host._commit_task_command(
                task=task,
                key=idempotency_key,
                operation="record_run_outcome",
                event_specs=(
                    (
                        "run.terminal_duplicate_ignored",
                        "run",
                        run_id,
                        {"status": status.value},
                        adapter_metadata,
                    ),
                ),
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self._host.get_run(task_id, run_id)

        if run.status not in {RunStatus.STARTING, RunStatus.RUNNING}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"run {run_id} cannot receive a terminal callback from {run.status.value}",
            )
        await self._host._apply_terminal_command(
            task=task,
            run=run,
            target=status,
            output=output or {},
            key=idempotency_key,
            operation="record_run_outcome",
            actor_ref=actor_ref,
            source=source,
            adapter_metadata=adapter_metadata,
        )
        return await self._host.get_run(task_id, run_id)

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        duplicate = await self._host._task_command(task_id, idempotency_key, "cancel_run")
        if duplicate is not None:
            if run.status not in TERMINAL_RUN_STATUSES:
                await self._host._finish_cancel(task_id, run_id, f"retry:{idempotency_key}")
            return await self._host.get_run(task_id, run_id)

        if run.status in TERMINAL_RUN_STATUSES:
            await self._host._commit_task_command(
                task=task,
                key=idempotency_key,
                operation="cancel_run",
                event_specs=(
                    (
                        "run.cancel_ignored_terminal",
                        "run",
                        run_id,
                        {"status": run.status.value},
                        (),
                    ),
                ),
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self._host.get_run(task_id, run_id)

        if run.status is RunStatus.QUEUED:
            await self._host._apply_terminal_command(
                task=task,
                run=run,
                target=RunStatus.CANCELLED,
                output={},
                key=idempotency_key,
                operation="cancel_run",
                actor_ref=actor_ref,
                source=source,
                adapter_metadata=(),
                allow_queued_cancel=True,
            )
            return await self._host.get_run(task_id, run_id)

        if run.status not in {RunStatus.STARTING, RunStatus.RUNNING}:
            raise ContractError(ErrorCode.CONFLICT, f"run {run_id} cannot be cancelled")

        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="cancel_run",
            event_specs=(("run.cancel_requested", "run", run_id, {}, ()),),
            result_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        await self._host._finish_cancel(task_id, run_id, idempotency_key)
        return await self._host.get_run(task_id, run_id)

    async def attach_artifact(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        artifact_id: str,
        run_id: str | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        validate_id(artifact_id, "artifact")
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "attach_artifact") is not None:
            return await self._host.get_task(task_id)
        subject_type = "task"
        subject_id = task_id
        if run_id is not None:
            await self._host.get_run(task_id, run_id)
            subject_type = "run"
            subject_id = run_id
        self._host._invalidate_completion_subject(task_id)
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="attach_artifact",
            event_specs=(
                (
                    "artifact.attached",
                    subject_type,
                    subject_id,
                    {"task_id": task_id, "artifact_id": artifact_id},
                    (),
                ),
            ),
            result_id=subject_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)

    async def attach_result(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        result_id: str,
        run_id: str | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        validate_id(result_id, "result")
        task = await self._host.get_task(task_id)
        if await self._host._task_command(task_id, idempotency_key, "attach_result") is not None:
            return await self._host.get_task(task_id)
        subject_type = "task"
        subject_id = task_id
        if run_id is not None:
            await self._host.get_run(task_id, run_id)
            subject_type = "run"
            subject_id = run_id
        self._host._invalidate_completion_subject(task_id)
        await self._host._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="attach_result",
            event_specs=(
                (
                    "result.attached",
                    subject_type,
                    subject_id,
                    {"task_id": task_id, "result_id": result_id},
                    (),
                ),
            ),
            result_id=subject_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self._host.get_task(task_id)


__all__ = ["KernelRunCommands"]
