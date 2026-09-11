"""Framework-independent platform-owned Task/Run/Event application kernel."""

from __future__ import annotations

from typing import Literal

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    EventProvider,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
    OperationControl,
    Orchestrator,
    PlanRequest,
    PlatformEvent,
    RetryMode,
)
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.domain import Event as DomainEvent
from ai_multi_agent_platform.domain import (
    OwnerRef,
    Plan,
    Provenance,
    RunStatus,
    Step,
    TaskStatus,
    new_id,
)
from ai_multi_agent_platform.verification import (
    CompletionAuthority,
    OutputChangeAwareCompletionAuthority,
)

from .lifecycle import KernelLifecycleReconciler
from .models import RecoveryReport, RunState, TaskState
from .queries import KernelQueries
from .recovery import KernelRecovery
from .repository import (
    CommandRecord,
    EventRepository,
    EventSourcedRunRepository,
    EventSourcedTaskRepository,
    InMemoryKernelRepository,
    RunRepository,
    TaskRepository,
)
from .run_commands import KernelRunCommands
from .task_commands import KernelTaskCommands

OwnerType = Literal["user", "organization", "team", "service"]
RunSubjectType = Literal["task", "step"]

_KERNEL_SOURCE = "platform-kernel"


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
        completion_authority: CompletionAuthority | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._lifecycle = lifecycle
        self._repository = repository or InMemoryKernelRepository()
        self._tasks = task_repository or EventSourcedTaskRepository(self._repository)
        self._runs = run_repository or EventSourcedRunRepository(self._repository)
        self._queries = KernelQueries(
            repository=self._repository,
            task_repository=self._tasks,
            run_repository=self._runs,
        )
        self._event_sink = event_sink
        self._completion_authority = completion_authority
        self._lifecycle_reconciler = KernelLifecycleReconciler(
            self,
            lifecycle=lifecycle,
            completion_authority=completion_authority,
        )
        self._recovery = KernelRecovery(self)
        self._task_commands = KernelTaskCommands(self)
        self._run_commands = KernelRunCommands(self)

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
        return await self._task_commands.create_task(
            idempotency_key=idempotency_key,
            title=title,
            objective=objective,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            task_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def get_task(self, task_id: str) -> TaskState:
        return await self._queries.get_task(task_id)

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        return await self._queries.get_run(task_id, run_id)

    async def history(self, task_id: str) -> tuple[PlatformEvent, ...]:
        return await self._queries.history(task_id)

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
        return await self._task_commands.update_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            title=title,
            objective=objective,
            metadata=metadata,
            actor_ref=actor_ref,
            source=source,
        )

    async def ready_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        return await self._task_commands.ready_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )

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
        return await self._task_commands.wait_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            reason=reason,
            blocked=blocked,
            actor_ref=actor_ref,
            source=source,
        )

    async def resume_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        return await self._task_commands.resume_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def complete_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        return await self._task_commands.complete_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def fail_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str | None = None,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        return await self._task_commands.fail_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            reason=reason,
            actor_ref=actor_ref,
            source=source,
        )

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        return await self._task_commands.cancel_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )

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
        active = await self._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot be replanned while run "
                f"{active.run_id} is {active.status.value}",
            )

        context = self._context(task, idempotency_key)
        proposal = await self._orchestrator.plan(
            PlanRequest(task_id=task_id, context=context, objective=task.task.description)
        )

        provenance = Provenance(
            source=source,
            actor_ref=actor_ref or f"{task.task.owner_ref.type}:{task.task.owner_ref.id}",
        )
        canonical_plan = Plan(
            task_id=task_id,
            owner_ref=task.task.owner_ref,
            active=True,
            project_id=task.task.project_id,
            provenance=provenance,
        )
        step_ids = {step.key: new_id("step") for step in proposal.steps}
        canonical_steps = tuple(
            Step(
                id=step_ids[step.key],
                plan_id=canonical_plan.id,
                title=step.title,
                owner_ref=task.task.owner_ref,
                depends_on=tuple(step_ids[key] for key in step.depends_on),
                project_id=task.task.project_id,
                provenance=provenance,
            )
            for step in proposal.steps
        )
        step_payloads: list[JsonValue] = [
            {
                "id": canonical_step.id,
                "proposal_key": proposed.key,
                "title": proposed.title,
                "objective": proposed.objective,
                "depends_on": list(canonical_step.depends_on),
                "metadata": proposed.metadata,
            }
            for proposed, canonical_step in zip(proposal.steps, canonical_steps, strict=True)
        ]
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
                        "plan_ref": canonical_plan.id,
                        "summary": proposal.summary,
                        "step_refs": [step.id for step in canonical_steps],
                        "steps": step_payloads,
                    },
                    proposal.adapter_metadata,
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
        return await self._run_commands.create_run(
            idempotency_key=idempotency_key,
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def retry_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        return await self._run_commands.retry_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        return await self._run_commands.start_run(
            idempotency_key=idempotency_key,
            task_id=task_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def start_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        return await self._run_commands.start_task(
            idempotency_key=idempotency_key,
            task_id=task_id,
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
        return await self._run_commands.refresh_run(
            idempotency_key=idempotency_key,
            task_id=task_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )

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
        return await self._run_commands.record_run_outcome(
            idempotency_key=idempotency_key,
            task_id=task_id,
            run_id=run_id,
            status=status,
            output=output,
            actor_ref=actor_ref,
            source=source,
            adapter_metadata=adapter_metadata,
        )

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> RunState:
        return await self._run_commands.cancel_run(
            idempotency_key=idempotency_key,
            task_id=task_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )

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
        return await self._run_commands.attach_artifact(
            idempotency_key=idempotency_key,
            task_id=task_id,
            artifact_id=artifact_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )

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
        return await self._run_commands.attach_result(
            idempotency_key=idempotency_key,
            task_id=task_id,
            result_id=result_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )

    def _invalidate_completion_subject(self, task_id: str) -> None:
        authority = self._completion_authority
        if isinstance(authority, OutputChangeAwareCompletionAuthority):
            authority.invalidate_task_subject(task_id)

    async def recover_task(self, task_id: str) -> RecoveryReport:
        return await self._recovery.recover_task(task_id)

    async def recover_all(self) -> tuple[RecoveryReport, ...]:
        return await self._recovery.recover_all()

    async def _dispatch_started_run(
        self,
        *,
        task_id: str,
        run_id: str,
        causation_id: str,
        actor_ref: str | None,
        source: str,
    ) -> None:
        return await self._lifecycle_reconciler.dispatch_started_run(
            task_id=task_id,
            run_id=run_id,
            causation_id=causation_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def _reconcile_started_run(
        self,
        task_id: str,
        run_id: str,
        causation_id: str,
    ) -> None:
        return await self._lifecycle_reconciler.reconcile_started_run(
            task_id,
            run_id,
            causation_id,
        )

    async def _finish_cancel(self, task_id: str, run_id: str, causation_id: str) -> None:
        return await self._lifecycle_reconciler.finish_cancel(task_id, run_id, causation_id)

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
        return await self._lifecycle_reconciler.apply_snapshot_command(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            key=key,
            operation=operation,
            actor_ref=actor_ref,
            source=source,
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
        return await self._lifecycle_reconciler.apply_snapshot_system(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            causation_id=causation_id,
            source=source,
        )

    def _running_specs(
        self,
        task: TaskState,
        run: RunState,
        snapshot: ExecutionSnapshot,
    ) -> tuple[EventSpec, ...]:
        return self._lifecycle_reconciler.running_specs(task, run, snapshot)

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
        return await self._lifecycle_reconciler.apply_terminal_command(
            task=task,
            run=run,
            target=target,
            output=output,
            key=key,
            operation=operation,
            actor_ref=actor_ref,
            source=source,
            adapter_metadata=adapter_metadata,
            allow_queued_cancel=allow_queued_cancel,
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
        return await self._lifecycle_reconciler.apply_terminal_system(
            task=task,
            run=run,
            target=target,
            output=output,
            causation_id=causation_id,
            source=source,
            adapter_metadata=adapter_metadata,
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
        return self._lifecycle_reconciler.terminal_specs(
            task,
            run,
            target,
            output,
            adapter_metadata,
            allow_queued_cancel,
        )

    def _completion_task_spec(self, task_id: str) -> EventSpec:
        return self._lifecycle_reconciler.completion_task_spec(task_id)

    @staticmethod
    def _task_event_for_terminal(status: RunStatus) -> str | None:
        return KernelLifecycleReconciler.task_event_for_terminal(status)

    async def _mark_recovery_required(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        causation_id: str,
    ) -> None:
        return await self._lifecycle_reconciler.mark_recovery_required(
            task_id=task_id,
            run_id=run_id,
            reason=reason,
            causation_id=causation_id,
        )

    async def _clear_recovery_if_needed(
        self,
        task_id: str,
        run_id: str,
        causation_id: str,
        source: str,
    ) -> None:
        return await self._lifecycle_reconciler.clear_recovery_if_needed(
            task_id,
            run_id,
            causation_id,
            source,
        )

    @staticmethod
    def _validate_run_subject(
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> None:
        KernelQueries.validate_run_subject(task, subject_type, subject_id)

    async def _active_runs(self, task: TaskState) -> tuple[RunState, ...]:
        return await self._queries.active_runs(task)

    async def _active_run_for_subject(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> RunState | None:
        return await self._queries.active_run_for_subject(task, subject_type, subject_id)

    async def _next_attempt(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> int:
        return await self._queries.next_attempt(task, subject_type, subject_id)

    async def _latest_active_run(self, task: TaskState) -> RunState | None:
        return await self._queries.latest_active_run(task)

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
        if adapter_metadata:
            namespaces = [item.namespace for item in adapter_metadata]
            if len(namespaces) != len(set(namespaces)):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "adapter metadata namespaces must be unique",
                )
            enriched["adapter_metadata"] = {
                item.namespace: dict(item.values) for item in adapter_metadata
            }

        return DomainEvent(
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=stream_id,
            owner_ref=OwnerRef(type=owner_type, id=owner_id),
            project_id=project_id,
            causation_id=causation_id,
            payload=enriched,
            provenance=Provenance(source=source, actor_ref=actor_ref),
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
            event_id=event.id,
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
