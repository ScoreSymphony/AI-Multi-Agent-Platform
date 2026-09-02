"""Framework-independent task/run/event kernel."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    EventProvider,
    ExecutionRequest,
    ExecutionSnapshot,
    ExecutionStatus,
    LifecycleBackend,
    OperationContext,
    Orchestrator,
    PlanRequest,
    PlatformEvent,
)
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import TERMINAL_RUN_STATUSES, TERMINAL_TASK_STATUSES, RunView, TaskStatus, TaskView
from .state import reduce_run, reduce_task


class PlatformKernel:
    """Own canonical lifecycle decisions while delegating replaceable capabilities."""

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        lifecycle: LifecycleBackend,
        events: EventProvider,
    ) -> None:
        self._orchestrator = orchestrator
        self._lifecycle = lifecycle
        self._events = events

    async def create_task(
        self,
        *,
        command_id: str,
        task_id: str,
        title: str,
        objective: str,
        owner_type: str,
        owner_id: str,
        project_id: str | None = None,
    ) -> TaskView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.event_type != "task.created" or marker.subject_id != task_id:
                self._raise_command_conflict(command_id, marker)
            return reduce_task(history, task_id)
        if history:
            raise ContractError(ErrorCode.CONFLICT, f"Task already exists: {task_id}")

        context = OperationContext(
            correlation_id=task_id,
            causation_id=command_id,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
        )
        await self._publish(
            event_type="task.created",
            subject_type="task",
            subject_id=task_id,
            context=context,
            payload={
                "command_id": command_id,
                "title": title,
                "objective": objective,
            },
        )
        return await self.get_task(task_id)

    async def update_task(
        self,
        *,
        command_id: str,
        task_id: str,
        title: str | None = None,
        objective: str | None = None,
    ) -> TaskView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.event_type != "task.updated" or marker.subject_id != task_id:
                self._raise_command_conflict(command_id, marker)
            return reduce_task(history, task_id)

        task = reduce_task(history, task_id)
        if task.status in TERMINAL_TASK_STATUSES:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Task {task_id} cannot be updated from {task.status.value}",
            )
        if title is None and objective is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Task update contains no changes")
        if title == "" or objective == "":
            raise ContractError(ErrorCode.INVALID_REQUEST, "Task fields cannot be empty")

        payload: dict[str, JsonValue] = {"command_id": command_id}
        if title is not None:
            payload["title"] = title
        if objective is not None:
            payload["objective"] = objective

        await self._publish(
            event_type="task.updated",
            subject_type="task",
            subject_id=task_id,
            context=self._context(task, command_id),
            payload=payload,
        )
        return await self.get_task(task_id)

    async def ready_task(self, *, command_id: str, task_id: str) -> TaskView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.event_type != "task.ready" or marker.subject_id != task_id:
                self._raise_command_conflict(command_id, marker)
            return reduce_task(history, task_id)

        task = reduce_task(history, task_id)
        if task.status not in {TaskStatus.DRAFT, TaskStatus.FAILED}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Task {task_id} cannot become ready from {task.status.value}",
            )

        await self._publish(
            event_type="task.ready",
            subject_type="task",
            subject_id=task_id,
            context=self._context(task, command_id),
            payload={"command_id": command_id},
        )
        return await self.get_task(task_id)

    async def start_task(self, *, command_id: str, task_id: str) -> RunView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.event_type != "run.queued":
                self._raise_command_conflict(command_id, marker)
            run_id = marker.subject_id
            await self._recover_run(task_id=task_id, run_id=run_id, command_id=command_id)
            return await self.get_run(task_id, run_id)

        task = reduce_task(history, task_id)
        if task.status is not TaskStatus.READY:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Task {task_id} cannot start from {task.status.value}",
            )

        context = self._context(task, command_id)
        plan = await self._orchestrator.plan(
            PlanRequest(task_id=task_id, context=context, objective=task.objective)
        )
        await self._publish(
            event_type="plan.created",
            subject_type="task",
            subject_id=task_id,
            context=context,
            payload={
                "plan_ref": plan.plan_ref,
                "summary": plan.summary,
                "step_refs": list(plan.step_refs),
            },
        )

        run_id = f"run_{uuid4()}"
        await self._publish(
            event_type="run.queued",
            subject_type="run",
            subject_id=run_id,
            context=context,
            payload={
                "command_id": command_id,
                "task_id": task_id,
                "attempt": len(task.run_ids) + 1,
            },
        )
        await self._publish(
            event_type="task.running",
            subject_type="task",
            subject_id=task_id,
            context=context,
        )

        request = ExecutionRequest(
            run_id=run_id,
            subject_type="task",
            subject_id=task_id,
            context=context,
            input={"plan_ref": plan.plan_ref},
        )
        handle = await self._lifecycle.start(request)
        await self._publish(
            event_type="run.running",
            subject_type="run",
            subject_id=run_id,
            context=context,
            payload={"backend_ref": handle.backend_ref},
        )
        return await self.get_run(task_id, run_id)

    async def refresh_run(
        self,
        *,
        command_id: str,
        task_id: str,
        run_id: str,
    ) -> RunView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.subject_id != run_id or not marker.event_type.startswith("run."):
                self._raise_command_conflict(command_id, marker)
            return reduce_run(history, run_id)

        task = reduce_task(history, task_id)
        run = reduce_run(history, run_id)
        self._require_run_task(run, task_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return run

        snapshot = await self._lifecycle.get(run_id, self._context(task, command_id))
        await self._apply_snapshot(
            task=task,
            run=run,
            snapshot=snapshot,
            command_id=command_id,
            mark_command=True,
        )
        return await self.get_run(task_id, run_id)

    async def cancel_run(
        self,
        *,
        command_id: str,
        task_id: str,
        run_id: str,
    ) -> RunView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.event_type != "run.cancelled" or marker.subject_id != run_id:
                self._raise_command_conflict(command_id, marker)
            return reduce_run(history, run_id)

        task = reduce_task(history, task_id)
        run = reduce_run(history, run_id)
        self._require_run_task(run, task_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return run

        snapshot = await self._lifecycle.cancel(run_id, self._context(task, command_id))
        if snapshot.status is not ExecutionStatus.CANCELLED:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"Lifecycle backend did not confirm cancellation for {run_id}",
            )
        await self._apply_snapshot(
            task=task,
            run=run,
            snapshot=snapshot,
            command_id=command_id,
            mark_command=True,
        )
        return await self.get_run(task_id, run_id)

    async def attach_artifact(
        self,
        *,
        command_id: str,
        task_id: str,
        artifact_ref: str,
    ) -> TaskView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.event_type != "artifact.attached" or marker.subject_id != task_id:
                self._raise_command_conflict(command_id, marker)
            return reduce_task(history, task_id)

        task = reduce_task(history, task_id)
        await self._publish(
            event_type="artifact.attached",
            subject_type="task",
            subject_id=task_id,
            context=self._context(task, command_id),
            payload={"command_id": command_id, "artifact_ref": artifact_ref},
        )
        return await self.get_task(task_id)

    async def record_result(
        self,
        *,
        command_id: str,
        task_id: str,
        result_ref: str,
    ) -> TaskView:
        history = await self._events.read(task_id)
        marker = self._command_marker(history, command_id)
        if marker is not None:
            if marker.event_type != "result.recorded" or marker.subject_id != task_id:
                self._raise_command_conflict(command_id, marker)
            return reduce_task(history, task_id)

        task = reduce_task(history, task_id)
        await self._publish(
            event_type="result.recorded",
            subject_type="task",
            subject_id=task_id,
            context=self._context(task, command_id),
            payload={"command_id": command_id, "result_ref": result_ref},
        )
        return await self.get_task(task_id)

    async def recover_task(self, task_id: str) -> TaskView:
        """Recover queued attempts after constructing a new kernel process."""

        history = await self._events.read(task_id)
        task = reduce_task(history, task_id)
        for run_id in task.run_ids:
            run = reduce_run(history, run_id)
            if run.status is ExecutionStatus.QUEUED:
                await self._recover_run(
                    task_id=task_id,
                    run_id=run_id,
                    command_id=f"recovery:{run_id}",
                )
                history = await self._events.read(task_id)
        return reduce_task(history, task_id)

    async def get_task(self, task_id: str) -> TaskView:
        return reduce_task(await self._events.read(task_id), task_id)

    async def get_run(self, task_id: str, run_id: str) -> RunView:
        run = reduce_run(await self._events.read(task_id), run_id)
        self._require_run_task(run, task_id)
        return run

    async def history(self, task_id: str) -> tuple[PlatformEvent, ...]:
        return await self._events.read(task_id)

    async def _recover_run(self, *, task_id: str, run_id: str, command_id: str) -> None:
        history = await self._events.read(task_id)
        task = reduce_task(history, task_id)
        run = reduce_run(history, run_id)
        self._require_run_task(run, task_id)
        if run.status is not ExecutionStatus.QUEUED:
            return

        context = self._context(task, command_id)
        if task.status is TaskStatus.READY:
            await self._publish(
                event_type="task.running",
                subject_type="task",
                subject_id=task_id,
                context=context,
            )
            task = await self.get_task(task_id)
            context = self._context(task, command_id)

        try:
            snapshot = await self._lifecycle.get(run_id, context)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            handle = await self._lifecycle.start(
                ExecutionRequest(
                    run_id=run_id,
                    subject_type="task",
                    subject_id=task_id,
                    context=context,
                    input={"plan_ref": task.plan_ref},
                )
            )
            await self._publish(
                event_type="run.running",
                subject_type="run",
                subject_id=run_id,
                context=context,
                payload={"backend_ref": handle.backend_ref},
            )
            return

        await self._apply_snapshot(
            task=task,
            run=run,
            snapshot=snapshot,
            command_id=command_id,
            mark_command=False,
        )

    async def _apply_snapshot(
        self,
        *,
        task: TaskView,
        run: RunView,
        snapshot: ExecutionSnapshot,
        command_id: str,
        mark_command: bool,
    ) -> None:
        if snapshot.run_id != run.run_id:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"Backend returned snapshot for wrong run: {snapshot.run_id}",
            )
        if snapshot.status is run.status:
            return

        context = self._context(task, command_id)
        event_type = f"run.{snapshot.status.value}"
        payload: dict[str, JsonValue] = {"output": snapshot.output}
        if mark_command:
            payload["command_id"] = command_id

        await self._publish(
            event_type=event_type,
            subject_type="run",
            subject_id=run.run_id,
            context=context,
            payload=payload,
        )

        task_event = self._task_event_for_run_status(snapshot.status)
        if task_event is not None and task.status not in TERMINAL_TASK_STATUSES:
            await self._publish(
                event_type=task_event,
                subject_type="task",
                subject_id=task.task_id,
                context=context,
            )

    @staticmethod
    def _task_event_for_run_status(status: ExecutionStatus) -> str | None:
        if status is ExecutionStatus.SUCCEEDED:
            return "task.succeeded"
        if status in {ExecutionStatus.FAILED, ExecutionStatus.TIMED_OUT}:
            return "task.failed"
        if status is ExecutionStatus.CANCELLED:
            return "task.cancelled"
        return None

    @staticmethod
    def _require_run_task(run: RunView, task_id: str) -> None:
        if run.task_id != task_id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Run {run.run_id} does not belong to task {task_id}",
            )

    @staticmethod
    def _context(task: TaskView, causation_id: str) -> OperationContext:
        return OperationContext(
            correlation_id=task.task_id,
            causation_id=causation_id,
            owner_type=task.owner_type,
            owner_id=task.owner_id,
            project_id=task.project_id,
        )

    async def _publish(
        self,
        *,
        event_type: str,
        subject_type: str,
        subject_id: str,
        context: OperationContext,
        payload: dict[str, JsonValue] | None = None,
    ) -> PlatformEvent:
        event = PlatformEvent(
            event_id=f"event_{uuid4()}",
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            occurred_at=datetime.now(UTC).isoformat(),
            context=context,
            payload=payload or {},
        )
        await self._events.publish(event)
        return event

    @staticmethod
    def _command_marker(
        events: tuple[PlatformEvent, ...],
        command_id: str,
    ) -> PlatformEvent | None:
        for event in events:
            if event.payload.get("command_id") == command_id:
                return event
        return None

    @staticmethod
    def _raise_command_conflict(command_id: str, marker: PlatformEvent) -> None:
        raise ContractError(
            ErrorCode.CONFLICT,
            f"Command {command_id!r} was already used for {marker.event_type}",
        )
