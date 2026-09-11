"""Focused recovery coordination for the platform Task/Run kernel.

Recovery remains owned by the canonical kernel.  This component only isolates restart
and reconciliation mechanics from the public ``PlatformKernel`` façade and depends on a
narrow internal host protocol rather than on a concrete executor/orchestrator backend.
"""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationContext,
)
from ai_multi_agent_platform.domain import RunStatus

from .models import RecoveryDisposition, RecoveryEntry, RecoveryReport, RunState, TaskState
from .repository import EventRepository


class RecoveryKernelHost(Protocol):
    """Internal capabilities required by kernel recovery coordination."""

    _lifecycle: LifecycleBackend
    _repository: EventRepository

    async def get_task(self, task_id: str) -> TaskState: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    def _context(self, task: TaskState, causation_id: str) -> OperationContext: ...

    async def _dispatch_started_run(
        self,
        *,
        task_id: str,
        run_id: str,
        causation_id: str,
        actor_ref: str | None,
        source: str,
    ) -> None: ...

    async def _apply_snapshot_system(
        self,
        *,
        task_id: str,
        run_id: str,
        snapshot: ExecutionSnapshot,
        causation_id: str,
        source: str,
    ) -> None: ...

    async def _mark_recovery_required(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        causation_id: str,
    ) -> None: ...


class KernelRecovery:
    """Reconcile canonical Runs with lifecycle-backend state after retries/restarts."""

    def __init__(self, host: RecoveryKernelHost) -> None:
        self._host = host

    async def recover_task(self, task_id: str) -> RecoveryReport:
        task = await self._host.get_task(task_id)
        entries: list[RecoveryEntry] = []
        for run_id in task.run_ids:
            run = await self._host.get_run(task_id, run_id)
            before = run.status
            disposition: RecoveryDisposition
            if before is RunStatus.QUEUED:
                disposition = RecoveryDisposition.QUEUED_PENDING
            elif before is RunStatus.STARTING:
                try:
                    snapshot = await self._host._lifecycle.get(
                        run_id,
                        self._host._context(task, f"recovery:{run_id}"),
                    )
                except ContractError as exc:
                    if exc.code is not ErrorCode.NOT_FOUND:
                        raise
                    await self._host._dispatch_started_run(
                        task_id=task_id,
                        run_id=run_id,
                        causation_id=f"recovery:{run_id}",
                        actor_ref="service:platform-kernel",
                        source="recovery",
                    )
                    disposition = RecoveryDisposition.REDISPATCHED
                else:
                    await self._host._apply_snapshot_system(
                        task_id=task_id,
                        run_id=run_id,
                        snapshot=snapshot,
                        causation_id=f"recovery:{run_id}",
                        source="recovery",
                    )
                    disposition = RecoveryDisposition.RECONCILED
            elif before is RunStatus.RUNNING:
                try:
                    snapshot = await self._host._lifecycle.get(
                        run_id,
                        self._host._context(task, f"recovery:{run_id}"),
                    )
                except ContractError as exc:
                    if exc.code is not ErrorCode.NOT_FOUND:
                        raise
                    await self._host._mark_recovery_required(
                        task_id=task_id,
                        run_id=run_id,
                        reason="canonical_running_backend_not_found",
                        causation_id=f"recovery:{run_id}",
                    )
                    disposition = RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED
                else:
                    await self._host._apply_snapshot_system(
                        task_id=task_id,
                        run_id=run_id,
                        snapshot=snapshot,
                        causation_id=f"recovery:{run_id}",
                        source="recovery",
                    )
                    disposition = RecoveryDisposition.RECONCILED
            else:
                disposition = RecoveryDisposition.TERMINAL_UNCHANGED
            after = (await self._host.get_run(task_id, run_id)).status
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
        for stream_id in await self._host._repository.list_stream_ids():
            if stream_id.startswith("task_"):
                reports.append(await self.recover_task(stream_id))
        return tuple(reports)


__all__ = ["KernelRecovery"]
