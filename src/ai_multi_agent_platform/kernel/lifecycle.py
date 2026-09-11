"""Focused lifecycle reconciliation for canonical kernel Runs.

The public ``PlatformKernel`` remains the supported façade. This component owns backend
dispatch, snapshot reconciliation, terminal transition construction and recovery markers
while canonical Task/Run persistence stays behind narrow kernel-host capabilities.
"""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionRequest,
    ExecutionSnapshot,
    ExecutionStatus,
    LifecycleBackend,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.verification import CompletionAuthority, CompletionState

from .models import TERMINAL_RUN_STATUSES, RunState, TaskState
from .repository import CommandRecord

EventSpec = tuple[
    str,
    str,
    str,
    dict[str, JsonValue],
    tuple[AdapterMetadata, ...],
]

_TERMINAL_EXECUTION_TO_RUN: dict[ExecutionStatus, RunStatus] = {
    ExecutionStatus.SUCCEEDED: RunStatus.SUCCEEDED,
    ExecutionStatus.FAILED: RunStatus.FAILED,
    ExecutionStatus.CANCELLED: RunStatus.CANCELLED,
    ExecutionStatus.TIMED_OUT: RunStatus.TIMED_OUT,
}


class LifecycleKernelHost(Protocol):
    """Internal canonical-kernel capabilities required for lifecycle reconciliation."""

    _lifecycle: LifecycleBackend
    _completion_authority: CompletionAuthority | None

    async def get_task(self, task_id: str) -> TaskState: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    def _context(self, task: TaskState, causation_id: str) -> OperationContext: ...

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

    async def _append_system_events(
        self,
        *,
        task: TaskState,
        causation_id: str,
        actor_ref: str | None,
        source: str,
        event_specs: tuple[EventSpec, ...],
    ) -> None: ...


class KernelLifecycleReconciler:
    """Reconcile lifecycle-backend state into canonical Run/Task events."""

    def __init__(
        self,
        host: LifecycleKernelHost,
        *,
        lifecycle: LifecycleBackend | None = None,
        completion_authority: CompletionAuthority | None = None,
    ) -> None:
        self._host = host
        # Compatibility-only constructor parameters keep this cohort independently
        # integrable; runtime behavior deliberately follows the host's current
        # lifecycle/completion dependencies.
        _ = lifecycle, completion_authority

    async def dispatch_started_run(
        self,
        *,
        task_id: str,
        run_id: str,
        causation_id: str,
        actor_ref: str | None,
        source: str,
    ) -> None:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        if run.status is not RunStatus.STARTING:
            return

        await self._host._append_system_events(
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
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        request = ExecutionRequest(
            run_id=run_id,
            subject_type=run.run.subject_type,
            subject_id=run.run.subject_id,
            context=self._host._context(task, causation_id),
            input={"plan_ref": task.plan_ref} if task.plan_ref is not None else {},
        )
        handle = await self._host._lifecycle.start(request)
        if handle.run_id != run_id:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"lifecycle backend returned handle for wrong run: {handle.run_id}",
            )

        task = await self._host.get_task(task_id)
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
        await self._host._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref=actor_ref,
            source=source,
            event_specs=tuple(specs),
        )

    async def reconcile_started_run(
        self,
        task_id: str,
        run_id: str,
        causation_id: str,
    ) -> None:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        if run.status not in {RunStatus.STARTING, RunStatus.RUNNING}:
            return
        try:
            snapshot = await self._host._lifecycle.get(
                run_id,
                self._host._context(task, causation_id),
            )
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            if run.status is RunStatus.STARTING:
                await self.dispatch_started_run(
                    task_id=task_id,
                    run_id=run_id,
                    causation_id=causation_id,
                    actor_ref="service:platform-kernel",
                    source="retry-recovery",
                )
            else:
                await self.mark_recovery_required(
                    task_id=task_id,
                    run_id=run_id,
                    reason="canonical_running_backend_not_found",
                    causation_id=causation_id,
                )
            return
        await self.apply_snapshot_system(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            causation_id=causation_id,
            source="retry-recovery",
        )

    async def finish_cancel(self, task_id: str, run_id: str, causation_id: str) -> None:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return
        snapshot = await self._host._lifecycle.cancel(
            run_id,
            self._host._context(task, causation_id),
        )
        await self.apply_snapshot_system(
            task_id=task_id,
            run_id=run_id,
            snapshot=snapshot,
            causation_id=causation_id,
            source="cancellation",
        )

    async def apply_snapshot_command(
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
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        if snapshot.status is ExecutionStatus.QUEUED:
            await self._host._commit_task_command(
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
            specs = self.running_specs(task, run, snapshot)
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
            await self._host._commit_task_command(
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
        await self.apply_terminal_command(
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

    async def apply_snapshot_system(
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
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        if snapshot.status is ExecutionStatus.QUEUED:
            return
        if snapshot.status is ExecutionStatus.RUNNING:
            specs = self.running_specs(task, run, snapshot)
            if specs:
                await self._host._append_system_events(
                    task=task,
                    causation_id=causation_id,
                    actor_ref="service:platform-kernel",
                    source=source,
                    event_specs=specs,
                )
            await self.clear_recovery_if_needed(task_id, run_id, causation_id, source)
            return
        target = _TERMINAL_EXECUTION_TO_RUN[snapshot.status]
        await self.apply_terminal_system(
            task=task,
            run=run,
            target=target,
            output=snapshot.output,
            causation_id=causation_id,
            source=source,
            adapter_metadata=snapshot.adapter_metadata,
        )

    @staticmethod
    def running_specs(
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

    async def apply_terminal_command(
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
        specs = self.terminal_specs(
            task,
            run,
            target,
            output,
            adapter_metadata,
            allow_queued_cancel,
        )
        await self._host._commit_task_command(
            task=task,
            key=key,
            operation=operation,
            event_specs=specs,
            result_id=run.run_id,
            actor_ref=actor_ref,
            source=source,
        )

    async def apply_terminal_system(
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
        specs = self.terminal_specs(task, run, target, output, adapter_metadata, False)
        await self._host._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref="service:platform-kernel",
            source=source,
            event_specs=specs,
        )

    def terminal_specs(
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
        elif run.status is RunStatus.STARTING and target in {
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
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

        if target is RunStatus.SUCCEEDED:
            completion_spec = self.completion_task_spec(task.task_id)
            if completion_spec[0] == "task.waiting":
                if task_status in {TaskStatus.RUNNING, TaskStatus.WAITING}:
                    specs.append(completion_spec)
                return tuple(specs)

        task_event = self.task_event_for_terminal(target)
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

    def completion_task_spec(self, task_id: str) -> EventSpec:
        if self._host._completion_authority is None:
            return ("task.succeeded", "task", task_id, {}, ())
        decision = self._host._completion_authority.assess_task_completion(task_id)
        if decision.state is CompletionState.ACCEPTED:
            return ("task.succeeded", "task", task_id, {}, ())

        payload: dict[str, JsonValue] = {
            "reason": f"verification:{decision.state.value}",
            "blocked": True,
            "verification_state": decision.state.value,
            "verification_reason": decision.reason,
            "repair_attempts_remaining": decision.repair_attempts_remaining,
        }
        if decision.policy_id is not None:
            payload["verification_policy_id"] = decision.policy_id
        if decision.policy_version is not None:
            payload["verification_policy_version"] = decision.policy_version
        if decision.blocking_verification_ids:
            payload["blocking_verification_ids"] = list(decision.blocking_verification_ids)
        if decision.subject is not None:
            payload["verification_subject"] = {
                "type": decision.subject.subject_type,
                "id": decision.subject.subject_id,
                "revision": decision.subject.revision,
                "digest": decision.subject.digest,
            }
        return ("task.waiting", "task", task_id, payload, ())

    @staticmethod
    def task_event_for_terminal(status: RunStatus) -> str | None:
        if status is RunStatus.SUCCEEDED:
            return "task.succeeded"
        if status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
            return "task.failed"
        if status is RunStatus.CANCELLED:
            return "task.cancelled"
        return None

    async def mark_recovery_required(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        causation_id: str,
    ) -> None:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        if run.recovery_required:
            return
        await self._host._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref="service:platform-kernel",
            source="recovery",
            event_specs=(("run.recovery_required", "run", run_id, {"reason": reason}, ()),),
        )

    async def clear_recovery_if_needed(
        self,
        task_id: str,
        run_id: str,
        causation_id: str,
        source: str,
    ) -> None:
        task = await self._host.get_task(task_id)
        run = await self._host.get_run(task_id, run_id)
        if not run.recovery_required:
            return
        await self._host._append_system_events(
            task=task,
            causation_id=causation_id,
            actor_ref="service:platform-kernel",
            source=source,
            event_specs=(("run.recovery_cleared", "run", run_id, {}, ()),),
        )


__all__ = ["KernelLifecycleReconciler"]
