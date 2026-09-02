"""Framework-independent platform-owned Task/Run/Event application kernel."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    EventProvider,
    ExecutionRequest,
    ExecutionSnapshot,
    ExecutionStatus,
    LifecycleBackend,
    OperationContext,
    OperationControl,
    Orchestrator,
    PlanRequest,
    PlatformEvent,
    RetryMode,
)
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.domain import (
    Event as DomainEvent,
)
from ai_multi_agent_platform.domain import (
    OwnerRef,
    Provenance,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    new_id,
    validate_id,
    validate_subject_id,
)

from .models import (
    TERMINAL_RUN_STATUSES,
    RecoveryDisposition,
    RecoveryEntry,
    RecoveryReport,
    RunState,
    TaskState,
)
from .repository import (
    CommandRecord,
    EventRepository,
    EventSourcedRunRepository,
    EventSourcedTaskRepository,
    InMemoryKernelRepository,
    RunRepository,
    TaskRepository,
)

OwnerType = Literal["user", "organization", "team", "service"]
RunSubjectType = Literal["task", "step"]

_TASK_CREATE_SCOPE = "task:create"
_KERNEL_SOURCE = "platform-kernel"
_TERMINAL_EXECUTION_TO_RUN: dict[ExecutionStatus, RunStatus] = {
    ExecutionStatus.SUCCEEDED: RunStatus.SUCCEEDED,
    ExecutionStatus.FAILED: RunStatus.FAILED,
    ExecutionStatus.CANCELLED: RunStatus.CANCELLED,
    ExecutionStatus.TIMED_OUT: RunStatus.TIMED_OUT,
}


class PlatformKernel:
    """Own canonical lifecycle truth while adapters remain replaceable participants."""

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        lifecycle: LifecycleBackend,
        repository: EventRepository | None = None,
        task_repository: TaskRepository | None = None,
        run_repository: RunRepository | None = None,
        event_sink: EventProvider | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._lifecycle = lifecycle
        self._repository = repository or InMemoryKernelRepository()
        self._tasks = task_repository or EventSourcedTaskRepository(self._repository)
        self._runs = run_repository or EventSourcedRunRepository(self._repository)
        self._event_sink = event_sink

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

        self._require_key(idempotency_key)
        existing = await self._existing_command(_TASK_CREATE_SCOPE, idempotency_key, "create_task")
        if existing is not None:
            return await self.get_task(existing.result_id)

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

        event = self._event(
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
        command = self._command(
            scope=_TASK_CREATE_SCOPE,
            key=idempotency_key,
            operation="create_task",
            stream_id=canonical_task_id,
            result_id=canonical_task_id,
            event=event,
        )
        result = await self._repository.commit(
            stream_id=canonical_task_id,
            expected_revision=0,
            events=(event,),
            command=command,
        )
        if not result.applied:
            duplicate = self._require_same_command(result.command, "create_task", idempotency_key)
            return await self.get_task(duplicate.result_id)
        await self._mirror((event,))
        return await self.get_task(canonical_task_id)

    async def get_task(self, task_id: str) -> TaskState:
        validate_id(task_id, "task")
        return await self._tasks.get_task(task_id)

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        validate_id(task_id, "task")
        validate_id(run_id, "run")
        run = await self._runs.get_run(task_id, run_id)
        if run.run.correlation_id != task_id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"run {run_id} does not belong to task {task_id}",
            )
        return run

    async def history(self, task_id: str) -> tuple[PlatformEvent, ...]:
        validate_id(task_id, "task")
        return await self._repository.read_events(task_id)

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
        task = await self.get_task(task_id)
        duplicate = await self._task_command(task_id, idempotency_key, "update_task")
        if duplicate is not None:
            return await self.get_task(task_id)
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
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="update_task",
            event_specs=(("task.updated", "task", task_id, payload, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

    async def ready_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "ready_task") is not None:
            return await self.get_task(task_id)
        if task.status not in {TaskStatus.DRAFT, TaskStatus.FAILED}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot become ready from {task.status.value}",
            )
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="ready_task",
            event_specs=(("task.ready", "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

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
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "wait_task") is not None:
            return await self.get_task(task_id)
        if task.status is not TaskStatus.RUNNING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot wait from {task.status.value}",
            )
        if not reason.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "waiting reason must not be blank")
        await self._commit_task_command(
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
        return await self.get_task(task_id)

    async def resume_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "resume_task") is not None:
            return await self.get_task(task_id)
        if task.status is not TaskStatus.WAITING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot resume from {task.status.value}",
            )
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="resume_task",
            event_specs=(("task.resumed", "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

    async def complete_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "complete_task") is not None:
            return await self.get_task(task_id)
        if task.status is not TaskStatus.RUNNING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot succeed from {task.status.value}",
            )
        active = await self._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot succeed while run {active.run_id} is {active.status.value}",
            )
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="complete_task",
            event_specs=(("task.succeeded", "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

    async def fail_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "fail_task") is not None:
            return await self.get_task(task_id)
        if task.status not in {TaskStatus.RUNNING, TaskStatus.WAITING}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot fail from {task.status.value}",
            )
        active = await self._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot fail while run {active.run_id} is {active.status.value}",
            )
        payload: dict[str, JsonValue] = {}
        if reason is not None:
            payload["reason"] = reason
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="fail_task",
            event_specs=(("task.failed", "task", task_id, payload, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "cancel_task") is not None:
            return await self.get_task(task_id)
        if task.status is TaskStatus.CANCELLED:
            await self._commit_task_command(
                task=task,
                key=idempotency_key,
                operation="cancel_task",
                event_specs=(("task.cancel_duplicate_ignored", "task", task_id, {}, ()),),
                result_id=task_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self.get_task(task_id)
        if task.status is TaskStatus.SUCCEEDED:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} already succeeded")
        if task.status is TaskStatus.FAILED:
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical lifecycle requires failed tasks to be retried to ready "
                "rather than cancelled",
            )

        active = await self._latest_active_run(task)
        if active is not None and task.status in {
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
        }:
            await self.cancel_run(
                idempotency_key=f"{idempotency_key}:run",
                task_id=task_id,
                run_id=active.run_id,
                actor_ref=actor_ref,
                source=source,
            )
            refreshed = await self.get_task(task_id)
            if refreshed.status is TaskStatus.CANCELLED:
                await self._commit_task_command(
                    task=refreshed,
                    key=idempotency_key,
                    operation="cancel_task",
                    event_specs=(("task.cancel_acknowledged", "task", task_id, {}, ()),),
                    result_id=task_id,
                    actor_ref=actor_ref,
                    source=source,
                )
                return await self.get_task(task_id)
            if active.run.subject_type == "step":
                await self._commit_task_command(
                    task=refreshed,
                    key=idempotency_key,
                    operation="cancel_task",
                    event_specs=(("task.cancelled", "task", task_id, {}, ()),),
                    result_id=task_id,
                    actor_ref=actor_ref,
                    source=source,
                )
                return await self.get_task(task_id)
            raise ContractError(
                ErrorCode.CONFLICT,
                f"run cancellation completed as {refreshed.status.value}, not task cancellation",
            )

        if task.status not in {
            TaskStatus.DRAFT,
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
        }:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} cannot be cancelled")
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="cancel_task",
            event_specs=(("task.cancelled", "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

    async def plan_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "plan_task") is not None:
            return await self.get_task(task_id)
        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} is terminal")

        context = self._context(task, idempotency_key)
        plan = await self._orchestrator.plan(
            PlanRequest(task_id=task_id, context=context, objective=task.task.description)
        )
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="plan_task",
            event_specs=(
                (
                    "plan.created",
                    "task",
                    task_id,
                    {
                        "plan_ref": plan.plan_ref,
                        "summary": plan.summary,
                        "step_refs": list(plan.step_refs),
                    },
                    plan.adapter_metadata,
                ),
            ),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

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
        task = await self.get_task(task_id)
        duplicate = await self._task_command(task_id, idempotency_key, "create_run")
        if duplicate is not None:
            return await self.get_run(task_id, duplicate.result_id)
        canonical_subject_id = subject_id or task_id
        validate_subject_id(subject_type, canonical_subject_id)
        allowed_task_statuses = (
            {TaskStatus.READY} if subject_type == "task" else {TaskStatus.READY, TaskStatus.RUNNING}
        )
        if task.status not in allowed_task_statuses:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot create a {subject_type} run from {task.status.value}",
            )
        active_subject_run = await self._active_run_for_subject(
            task, subject_type, canonical_subject_id
        )
        if active_subject_run is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"subject {canonical_subject_id} already has active run "
                f"{active_subject_run.run_id}",
            )
        run_id = new_id("run")
        attempt = await self._next_attempt(task, subject_type, canonical_subject_id)
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
        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="create_run",
            event_specs=(("run.created", "run", run_id, payload, ()),),
            result_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_run(task_id, run_id)

    async def retry_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self.get_task(task_id)
        duplicate = await self._task_command(task_id, idempotency_key, "retry_task")
        if duplicate is not None:
            return await self.get_run(task_id, duplicate.result_id)
        if task.status is not TaskStatus.FAILED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot retry from {task.status.value}",
            )
        active = await self._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot retry while run {active.run_id} is {active.status.value}",
            )
        run_id = new_id("run")
        attempt = await self._next_attempt(task, "task", task_id)
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
        await self._commit_task_command(
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
        return await self.get_run(task_id, run_id)

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        duplicate = await self._task_command(task_id, idempotency_key, "start_run")
        if duplicate is not None:
            if duplicate.result_id != run_id:
                raise ContractError(ErrorCode.CONFLICT, "start command belongs to another run")
            if run.status in {RunStatus.STARTING, RunStatus.RUNNING}:
                await self._reconcile_started_run(task_id, run_id, f"retry:{idempotency_key}")
            return await self.get_run(task_id, run_id)
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
        if run.status is not RunStatus.QUEUED:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"run {run_id} cannot start from {run.status.value}",
            )

        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="start_run",
            event_specs=(("run.starting", "run", run_id, {}, ()),),
            result_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        await self._dispatch_started_run(
            task_id=task_id,
            run_id=run_id,
            causation_id=idempotency_key,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_run(task_id, run_id)

    async def start_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        """Convenience fake/reference flow: plan if needed, create run, start it."""

        task = await self.get_task(task_id)
        existing_run_command = await self._existing_command(
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
            await self.plan_task(
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
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        duplicate = await self._task_command(task_id, idempotency_key, "refresh_run")
        if duplicate is not None:
            return await self.get_run(task_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            await self._commit_task_command(
                task=task,
                key=idempotency_key,
                operation="refresh_run",
                event_specs=(("run.refresh_ignored_terminal", "run", run_id, {}, ()),),
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self.get_run(task_id, run_id)

        try:
            snapshot = await self._lifecycle.get(run_id, self._context(task, idempotency_key))
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            await self._mark_recovery_required(
                task_id=task_id,
                run_id=run_id,
                reason="backend_not_found_during_refresh",
                causation_id=idempotency_key,
            )
            refreshed_task = await self.get_task(task_id)
            await self._commit_task_command(
                task=refreshed_task,
                key=idempotency_key,
                operation="refresh_run",
                event_specs=(("run.refresh_not_found", "run", run_id, {}, ()),),
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self.get_run(task_id, run_id)

        await self._apply_snapshot_command(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            key=idempotency_key,
            operation="refresh_run",
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_run(task_id, run_id)

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
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        duplicate = await self._task_command(task_id, idempotency_key, "record_run_outcome")
        if duplicate is not None:
            return await self.get_run(task_id, run_id)

        if run.status in TERMINAL_RUN_STATUSES:
            if run.status is not status:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"run {run_id} is already {run.status.value}; "
                    f"cannot overwrite with {status.value}",
                )
            await self._commit_task_command(
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
            return await self.get_run(task_id, run_id)

        if run.status not in {RunStatus.STARTING, RunStatus.RUNNING}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"run {run_id} cannot receive a terminal callback from {run.status.value}",
            )
        await self._apply_terminal_command(
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
        return await self.get_run(task_id, run_id)

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        duplicate = await self._task_command(task_id, idempotency_key, "cancel_run")
        if duplicate is not None:
            if run.status not in TERMINAL_RUN_STATUSES:
                await self._finish_cancel(task_id, run_id, f"retry:{idempotency_key}")
            return await self.get_run(task_id, run_id)

        if run.status in TERMINAL_RUN_STATUSES:
            await self._commit_task_command(
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
            return await self.get_run(task_id, run_id)

        if run.status is RunStatus.QUEUED:
            await self._apply_terminal_command(
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
            return await self.get_run(task_id, run_id)

        if run.status not in {RunStatus.STARTING, RunStatus.RUNNING}:
            raise ContractError(ErrorCode.CONFLICT, f"run {run_id} cannot be cancelled")

        await self._commit_task_command(
            task=task,
            key=idempotency_key,
            operation="cancel_run",
            event_specs=(("run.cancel_requested", "run", run_id, {}, ()),),
            result_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        await self._finish_cancel(task_id, run_id, idempotency_key)
        return await self.get_run(task_id, run_id)

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
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "attach_artifact") is not None:
            return await self.get_task(task_id)
        subject_type = "task"
        subject_id = task_id
        if run_id is not None:
            await self.get_run(task_id, run_id)
            subject_type = "run"
            subject_id = run_id
        await self._commit_task_command(
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
        return await self.get_task(task_id)

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
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "attach_result") is not None:
            return await self.get_task(task_id)
        subject_type = "task"
        subject_id = task_id
        if run_id is not None:
            await self.get_run(task_id, run_id)
            subject_type = "run"
            subject_id = run_id
        await self._commit_task_command(
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
        return await self.get_task(task_id)

    async def recover_task(self, task_id: str) -> RecoveryReport:
        task = await self.get_task(task_id)
        entries: list[RecoveryEntry] = []
        for run_id in task.run_ids:
            run = await self.get_run(task_id, run_id)
            before = run.status
            disposition: RecoveryDisposition
            if before is RunStatus.QUEUED:
                disposition = RecoveryDisposition.QUEUED_PENDING
            elif before is RunStatus.STARTING:
                try:
                    snapshot = await self._lifecycle.get(
                        run_id,
                        self._context(task, f"recovery:{run_id}"),
                    )
                except ContractError as exc:
                    if exc.code is not ErrorCode.NOT_FOUND:
                        raise
                    await self._dispatch_started_run(
                        task_id=task_id,
                        run_id=run_id,
                        causation_id=f"recovery:{run_id}",
                        actor_ref="service:platform-kernel",
                        source="recovery",
                    )
                    disposition = RecoveryDisposition.REDISPATCHED
                else:
                    await self._apply_snapshot_system(
                        task_id=task_id,
                        run_id=run_id,
                        snapshot=snapshot,
                        causation_id=f"recovery:{run_id}",
                        source="recovery",
                    )
                    disposition = RecoveryDisposition.RECONCILED
            elif before is RunStatus.RUNNING:
                try:
                    snapshot = await self._lifecycle.get(
                        run_id,
                        self._context(task, f"recovery:{run_id}"),
                    )
                except ContractError as exc:
                    if exc.code is not ErrorCode.NOT_FOUND:
                        raise
                    await self._mark_recovery_required(
                        task_id=task_id,
                        run_id=run_id,
                        reason="canonical_running_backend_not_found",
                        causation_id=f"recovery:{run_id}",
                    )
                    disposition = RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
                else:
                    await self._apply_snapshot_system(
                        task_id=task_id,
                        run_id=run_id,
                        snapshot=snapshot,
                        causation_id=f"recovery:{run_id}",
                        source="recovery",
                    )
                    disposition = RecoveryDisposition.RECONCILED
            else:
                disposition = RecoveryDisposition.TERMINAL_UNCHANGED
            after = (await self.get_run(task_id, run_id)).status
            entries.append(
                RecoveryEntry(
                    run_id=run_id,
                    before=before,
                    after=after,
                    disposition=disposition,
                )
            )
        return RecoveryReport(task_id=task_id, entries=tuple(entries))

    async def recover_all(self) -> tuple[RecoveryReport, ...]:
        reports: list[RecoveryReport] = []
        for stream_id in await self._repository.list_stream_ids():
            if stream_id.startswith("task_"):
                reports.append(await self.recover_task(stream_id))
        return tuple(reports)

    async def _dispatch_started_run(
        self,
        *,
        task_id: str,
        run_id: str,
        causation_id: str,
        actor_ref: str | None,
        source: str,
    ) -> None:
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        if run.status is not RunStatus.STARTING:
            return

        await self._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref=actor_ref,
            source=source,
            event_specs=(
                (
                    "run.dispatch_attempted",
                    "run",
                    run_id,
                    {"dispatch_attempt": run.dispatch_attempts + 1},
                    (),
                ),
            ),
        )
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        request = ExecutionRequest(
            run_id=run_id,
            subject_type=run.run.subject_type,
            subject_id=run.run.subject_id,
            context=self._context(task, causation_id),
            input={"plan_ref": task.plan_ref} if task.plan_ref is not None else {},
        )
        handle = await self._lifecycle.start(request)
        if handle.run_id != run_id:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"lifecycle backend returned handle for wrong run: {handle.run_id}",
            )

        task = await self.get_task(task_id)
        specs: list[EventSpec] = [
            (
                "run.running",
                "run",
                run_id,
                {"backend_ref": handle.backend_ref} if handle.backend_ref is not None else {},
                handle.adapter_metadata,
            )
        ]
        if run.recovery_required:
            specs.append(("run.recovery_cleared", "run", run_id, {}, ()))
        if task.status is TaskStatus.READY:
            specs.append(("task.running", "task", task_id, {}, ()))
        await self._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref=actor_ref,
            source=source,
            event_specs=tuple(specs),
        )

    async def _reconcile_started_run(self, task_id: str, run_id: str, causation_id: str) -> None:
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        if run.status not in {RunStatus.STARTING, RunStatus.RUNNING}:
            return
        try:
            snapshot = await self._lifecycle.get(run_id, self._context(task, causation_id))
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            if run.status is RunStatus.STARTING:
                await self._dispatch_started_run(
                    task_id=task_id,
                    run_id=run_id,
                    causation_id=causation_id,
                    actor_ref="service:platform-kernel",
                    source="retry-recovery",
                )
            else:
                await self._mark_recovery_required(
                    task_id=task_id,
                    run_id=run_id,
                    reason="canonical_running_backend_not_found",
                    causation_id=causation_id,
                )
            return
        await self._apply_snapshot_system(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            causation_id=causation_id,
            source="retry-recovery",
        )

    async def _finish_cancel(self, task_id: str, run_id: str, causation_id: str) -> None:
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return
        snapshot = await self._lifecycle.cancel(run_id, self._context(task, causation_id))
        await self._apply_snapshot_system(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            causation_id=causation_id,
            source="cancellation",
        )

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
    ) -> None:
        if snapshot.run_id != run_id:
            raise ContractError(ErrorCode.BACKEND_ERROR, "backend snapshot has wrong run id")
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        if snapshot.status is ExecutionStatus.QUEUED:
            await self._commit_task_command(
                task=task,
                key=key,
                operation=operation,
                event_specs=(
                    ("run.refresh_observed_queued", "run", run_id, {}, snapshot.adapter_metadata),
                ),
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return
        if snapshot.status is ExecutionStatus.RUNNING:
            specs = self._running_specs(task, run, snapshot)
            if not specs:
                specs = (
                    (
                        "run.refresh_observed_no_change",
                        "run",
                        run_id,
                        {},
                        snapshot.adapter_metadata,
                    ),
                )
            await self._commit_task_command(
                task=task,
                key=key,
                operation=operation,
                event_specs=specs,
                result_id=run_id,
                actor_ref=actor_ref,
                source=source,
            )
            return
        target = _TERMINAL_EXECUTION_TO_RUN[snapshot.status]
        await self._apply_terminal_command(
            task=task,
            run=run,
            target=target,
            output=snapshot.output,
            key=key,
            operation=operation,
            actor_ref=actor_ref,
            source=source,
            adapter_metadata=snapshot.adapter_metadata,
        )

    async def _apply_snapshot_system(
        self,
        *,
        task_id: str,
        run_id: str,
        snapshot: ExecutionSnapshot,
        causation_id: str,
        source: str,
    ) -> None:
        if snapshot.run_id != run_id:
            raise ContractError(ErrorCode.BACKEND_ERROR, "backend snapshot has wrong run id")
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        if snapshot.status is ExecutionStatus.QUEUED:
            return
        if snapshot.status is ExecutionStatus.RUNNING:
            specs = self._running_specs(task, run, snapshot)
            if specs:
                await self._append_system_events(
                    task=task,
                    causation_id=causation_id,
                    actor_ref="service:platform-kernel",
                    source=source,
                    event_specs=specs,
                )
            await self._clear_recovery_if_needed(task_id, run_id, causation_id, source)
            return
        target = _TERMINAL_EXECUTION_TO_RUN[snapshot.status]
        await self._apply_terminal_system(
            task=task,
            run=run,
            target=target,
            output=snapshot.output,
            causation_id=causation_id,
            source=source,
            adapter_metadata=snapshot.adapter_metadata,
        )

    def _running_specs(
        self,
        task: TaskState,
        run: RunState,
        snapshot: ExecutionSnapshot,
    ) -> tuple[EventSpec, ...]:
        if run.status is RunStatus.RUNNING:
            return ()
        if run.status is not RunStatus.STARTING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"backend reports running while canonical run is {run.status.value}",
            )
        specs: list[EventSpec] = [("run.running", "run", run.run_id, {}, snapshot.adapter_metadata)]
        if task.status is TaskStatus.READY:
            specs.append(("task.running", "task", task.task_id, {"reconciled": True}, ()))
        return tuple(specs)

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
    ) -> None:
        specs = self._terminal_specs(
            task, run, target, output, adapter_metadata, allow_queued_cancel
        )
        await self._commit_task_command(
            task=task,
            key=key,
            operation=operation,
            event_specs=specs,
            result_id=run.run_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def _apply_terminal_system(
        self,
        *,
        task: TaskState,
        run: RunState,
        target: RunStatus,
        output: dict[str, JsonValue],
        causation_id: str,
        source: str,
        adapter_metadata: tuple[AdapterMetadata, ...],
    ) -> None:
        if run.status in TERMINAL_RUN_STATUSES:
            if run.status is target:
                return
            raise ContractError(
                ErrorCode.CONFLICT,
                f"terminal run {run.run_id} cannot change {run.status.value} -> {target.value}",
            )
        specs = self._terminal_specs(task, run, target, output, adapter_metadata, False)
        await self._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref="service:platform-kernel",
            source=source,
            event_specs=specs,
        )

    def _terminal_specs(
        self,
        task: TaskState,
        run: RunState,
        target: RunStatus,
        output: dict[str, JsonValue],
        adapter_metadata: tuple[AdapterMetadata, ...],
        allow_queued_cancel: bool,
    ) -> tuple[EventSpec, ...]:
        specs: list[EventSpec] = []
        if run.status is RunStatus.QUEUED:
            if target is not RunStatus.CANCELLED or not allow_queued_cancel:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"queued run {run.run_id} can only be cancelled before dispatch",
                )
        elif run.status is RunStatus.STARTING and target in {
            RunStatus.SUCCEEDED,
            RunStatus.TIMED_OUT,
        }:
            specs.append(("run.running", "run", run.run_id, {"inferred": True}, ()))
            if task.status is TaskStatus.READY:
                specs.append(("task.running", "task", task.task_id, {"inferred": True}, ()))
        elif run.status is RunStatus.STARTING and target in {RunStatus.FAILED, RunStatus.CANCELLED}:
            pass
        elif run.status is not RunStatus.RUNNING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"run {run.run_id} cannot become {target.value} from {run.status.value}",
            )

        event_type = f"run.{target.value}"
        specs.append((event_type, "run", run.run_id, {"output": output}, adapter_metadata))

        if run.run.subject_type == "step":
            return tuple(specs)

        task_status = task.status
        if any(spec[0] == "task.running" for spec in specs):
            task_status = TaskStatus.RUNNING
        task_event = self._task_event_for_terminal(target)
        if target is RunStatus.CANCELLED and task_status is TaskStatus.READY:
            specs.append(("task.cancelled", "task", task.task_id, {}, ()))
        elif task_event is not None:
            if task_status is TaskStatus.WAITING and target is RunStatus.SUCCEEDED:
                specs.append(("task.resumed", "task", task.task_id, {"inferred": True}, ()))
                task_status = TaskStatus.RUNNING
            if task_status is TaskStatus.READY and target in {
                RunStatus.FAILED,
                RunStatus.TIMED_OUT,
            }:
                specs.append(("task.running", "task", task.task_id, {"inferred": True}, ()))
                task_status = TaskStatus.RUNNING
            if task_status in {TaskStatus.RUNNING, TaskStatus.WAITING}:
                specs.append((task_event, "task", task.task_id, {}, ()))
        return tuple(specs)

    @staticmethod
    def _task_event_for_terminal(status: RunStatus) -> str | None:
        if status is RunStatus.SUCCEEDED:
            return "task.succeeded"
        if status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
            return "task.failed"
        if status is RunStatus.CANCELLED:
            return "task.cancelled"
        return None

    async def _mark_recovery_required(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        causation_id: str,
    ) -> None:
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        if run.recovery_required:
            return
        await self._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref="service:platform-kernel",
            source="recovery",
            event_specs=(("run.recovery_required", "run", run_id, {"reason": reason}, ()),),
        )

    async def _clear_recovery_if_needed(
        self,
        task_id: str,
        run_id: str,
        causation_id: str,
        source: str,
    ) -> None:
        task = await self.get_task(task_id)
        run = await self.get_run(task_id, run_id)
        if not run.recovery_required:
            return
        await self._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref="service:platform-kernel",
            source=source,
            event_specs=(("run.recovery_cleared", "run", run_id, {}, ()),),
        )

    async def _active_run_for_subject(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> RunState | None:
        for run_id in reversed(task.run_ids):
            run = await self.get_run(task.task_id, run_id)
            if (
                run.run.subject_type == subject_type
                and run.run.subject_id == subject_id
                and run.status not in TERMINAL_RUN_STATUSES
            ):
                return run
        return None

    async def _next_attempt(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> int:
        latest = 0
        for run_id in task.run_ids:
            run = await self.get_run(task.task_id, run_id)
            if run.run.subject_type == subject_type and run.run.subject_id == subject_id:
                latest = max(latest, run.run.attempt)
        return latest + 1

    async def _latest_active_run(self, task: TaskState) -> RunState | None:
        for run_id in reversed(task.run_ids):
            run = await self.get_run(task.task_id, run_id)
            if run.status not in TERMINAL_RUN_STATUSES:
                return run
        return None

    async def _task_command(
        self,
        task_id: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None:
        self._require_key(key)
        return await self._existing_command(task_id, key, operation)

    async def _existing_command(
        self,
        scope: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None:
        record = await self._repository.find_command(scope, key)
        if record is None:
            return None
        return self._require_same_command(record, operation, key)

    @staticmethod
    def _require_same_command(
        record: CommandRecord | None,
        operation: str,
        key: str,
    ) -> CommandRecord:
        if record is None:
            raise ContractError(ErrorCode.CONFLICT, f"idempotency race lost for {key}")
        if record.operation != operation:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"idempotency key {key!r} already belongs to {record.operation}",
            )
        return record

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
    ) -> CommandRecord:
        self._require_key(key)
        existing = await self._existing_command(task.task_id, key, operation)
        if existing is not None:
            return existing
        events = self._build_events(
            task=task,
            causation_id=key,
            actor_ref=actor_ref,
            source=source,
            event_specs=event_specs,
        )
        command = self._command(
            scope=task.task_id,
            key=key,
            operation=operation,
            stream_id=task.task_id,
            result_id=result_id,
            event=events[0],
        )
        result = await self._repository.commit(
            stream_id=task.task_id,
            expected_revision=task.revision,
            events=events,
            command=command,
        )
        if not result.applied:
            return self._require_same_command(result.command, operation, key)
        await self._mirror(events)
        return command

    async def _append_system_events(
        self,
        *,
        task: TaskState,
        causation_id: str,
        actor_ref: str | None,
        source: str,
        event_specs: tuple[EventSpec, ...],
    ) -> None:
        events = self._build_events(
            task=task,
            causation_id=causation_id,
            actor_ref=actor_ref,
            source=source,
            event_specs=event_specs,
        )
        await self._repository.commit(
            stream_id=task.task_id,
            expected_revision=task.revision,
            events=events,
        )
        await self._mirror(events)

    def _build_events(
        self,
        *,
        task: TaskState,
        causation_id: str,
        actor_ref: str | None,
        source: str,
        event_specs: tuple[EventSpec, ...],
    ) -> tuple[PlatformEvent, ...]:
        return tuple(
            self._event(
                stream_id=task.task_id,
                event_type=event_type,
                subject_type=subject_type,
                subject_id=subject_id,
                causation_id=causation_id,
                owner_type=task.task.owner_ref.type,
                owner_id=task.task.owner_ref.id,
                project_id=task.task.project_id,
                actor_ref=actor_ref or f"{task.task.owner_ref.type}:{task.task.owner_ref.id}",
                source=source,
                revision=task.revision + offset,
                payload=payload,
                adapter_metadata=adapter_metadata,
            )
            for offset, (
                event_type,
                subject_type,
                subject_id,
                payload,
                adapter_metadata,
            ) in enumerate(event_specs, start=1)
        )

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
    ) -> PlatformEvent:
        enriched = dict(payload)
        enriched.update(
            {
                "actor_ref": actor_ref,
                "source": source,
                "canonical_payload_version": "1.0",
                "stream_revision": revision,
            }
        )
        occurred_at = datetime.now(UTC)
        canonical = DomainEvent(
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=stream_id,
            owner_ref=OwnerRef(type=owner_type, id=owner_id),
            project_id=project_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            payload=enriched,
            provenance=Provenance(source=source, actor_ref=actor_ref),
        )
        return PlatformEvent(
            event_id=canonical.id,
            event_type=canonical.event_type,
            subject_type=canonical.subject_type,
            subject_id=canonical.subject_id,
            occurred_at=canonical.occurred_at.isoformat(),
            context=OperationContext(
                correlation_id=stream_id,
                causation_id=causation_id,
                owner_type=owner_type,
                owner_id=owner_id,
                project_id=project_id,
                control=OperationControl(
                    idempotency_key=causation_id,
                    retry_mode=RetryMode.IDEMPOTENT,
                ),
            ),
            payload=enriched,
            adapter_metadata=adapter_metadata,
        )

    @staticmethod
    def _command(
        *,
        scope: str,
        key: str,
        operation: str,
        stream_id: str,
        result_id: str,
        event: PlatformEvent,
    ) -> CommandRecord:
        return CommandRecord(
            scope=scope,
            idempotency_key=key,
            operation=operation,
            stream_id=stream_id,
            result_id=result_id,
            event_id=event.event_id,
        )

    @staticmethod
    def _context(task: TaskState, causation_id: str) -> OperationContext:
        return OperationContext(
            correlation_id=task.task_id,
            causation_id=causation_id,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
            control=OperationControl(
                idempotency_key=causation_id,
                retry_mode=RetryMode.IDEMPOTENT,
            ),
        )

    async def _mirror(self, events: tuple[PlatformEvent, ...]) -> None:
        if self._event_sink is None:
            return
        for event in events:
            await self._event_sink.publish(event)

    @staticmethod
    def _require_key(key: str) -> None:
        if not key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key must not be blank")


EventSpec = tuple[
    str,
    str,
    str,
    dict[str, JsonValue],
    tuple[AdapterMetadata, ...],
]
