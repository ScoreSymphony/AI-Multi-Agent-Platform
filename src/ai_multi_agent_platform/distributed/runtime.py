"""Scheduling, dispatch and reconciliation across replaceable workers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot
from ai_multi_agent_platform.domain import RunStatus

from .models import ReservationStatus, WorkerJobRequest, WorkerStatus
from .registry import DistributedRegistry, RegistryError
from .scheduler import DeterministicScheduler, ScheduledPlacement
from .worker import WorkerDispatcher


class DispatchState(StrEnum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    LOST = "lost"
    CANCEL_PENDING = "cancel_pending"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    job: WorkerJobRequest
    worker_id: str
    reservation_id: str
    state: DispatchState
    handle: ExecutionHandle | None = None
    snapshot: ExecutionSnapshot | None = None
    last_error: str | None = None


class DistributedRuntime:
    """Reference control-side runtime with idempotent dispatch and reconciliation."""

    def __init__(
        self,
        registry: DistributedRegistry,
        *,
        scheduler: DeterministicScheduler | None = None,
    ) -> None:
        self.registry = registry
        self.scheduler = scheduler or DeterministicScheduler(registry)
        self._dispatchers: dict[str, WorkerDispatcher] = {}
        self._records: dict[str, DispatchRecord] = {}

    def attach_worker(self, dispatcher: WorkerDispatcher) -> None:
        worker = self.registry.get_worker(dispatcher.worker_id)
        if worker.worker_id != dispatcher.worker_id:
            raise RegistryError("dispatcher worker identity mismatch")
        self._dispatchers[dispatcher.worker_id] = dispatcher

    def detach_worker(self, worker_id: str) -> None:
        self._dispatchers.pop(worker_id, None)

    def get_record(self, worker_job_id: str) -> DispatchRecord:
        try:
            return self._records[worker_job_id]
        except KeyError as exc:
            raise RegistryError(f"unknown dispatched worker job: {worker_job_id}") from exc

    async def dispatch(
        self,
        job: WorkerJobRequest,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        existing = self._records.get(job.worker_job_id)
        if existing is not None:
            if existing.job != job:
                raise RegistryError("duplicate worker_job_id carries a different request")
            return existing

        placement = self.scheduler.schedule(job, now=now)
        worker_id = placement.decision.selected_worker_id
        if worker_id is None:
            raise AssertionError("scheduler returned placement without selected worker")
        reserved = DispatchRecord(
            job=job,
            worker_id=worker_id,
            reservation_id=placement.reservation.reservation_id,
            state=DispatchState.RESERVED,
        )
        dispatcher = self._dispatchers.get(worker_id)
        if dispatcher is None:
            self.registry.release_reservation(placement.reservation.reservation_id)
            raise RegistryError(f"selected worker has no attached dispatcher: {worker_id}")

        try:
            handle = await dispatcher.dispatch(job)
        except Exception:
            self.registry.release_reservation(placement.reservation.reservation_id)
            raise
        dispatched = replace(reserved, state=DispatchState.DISPATCHED, handle=handle)
        self._records[job.worker_job_id] = dispatched
        return dispatched

    async def cancel(self, worker_job_id: str) -> DispatchRecord:
        record = self.get_record(worker_job_id)
        if record.state is DispatchState.TERMINAL:
            return record
        worker = self.registry.get_worker(record.worker_id)
        dispatcher = self._dispatchers.get(record.worker_id)
        if worker.status is WorkerStatus.OFFLINE or dispatcher is None:
            pending = replace(record, state=DispatchState.CANCEL_PENDING)
            self._records[worker_job_id] = pending
            return pending
        snapshot = await dispatcher.cancel(worker_job_id)
        return self._apply_snapshot(record, snapshot)

    async def reconcile(self, *, now: datetime | None = None) -> tuple[DispatchRecord, ...]:
        """Reconcile liveness and job state; remote running is never trusted indefinitely."""

        self.registry.expire_heartbeats(now=now)
        self.registry.expire_reservations(now=now)
        reconciled: list[DispatchRecord] = []
        for worker_job_id in sorted(self._records):
            record = self._records[worker_job_id]
            if record.state is DispatchState.TERMINAL:
                reconciled.append(record)
                continue

            worker = self.registry.get_worker(record.worker_id)
            dispatcher = self._dispatchers.get(record.worker_id)
            if worker.status is WorkerStatus.OFFLINE or dispatcher is None:
                state = (
                    DispatchState.CANCEL_PENDING
                    if record.state is DispatchState.CANCEL_PENDING
                    else DispatchState.LOST
                )
                updated = replace(record, state=state, last_error="worker unreachable")
                self._records[worker_job_id] = updated
                reconciled.append(updated)
                continue

            try:
                if record.state is DispatchState.CANCEL_PENDING:
                    snapshot = await dispatcher.cancel(worker_job_id)
                else:
                    snapshot = await dispatcher.get(worker_job_id)
            except Exception as exc:
                updated = replace(
                    record,
                    state=DispatchState.LOST,
                    last_error=type(exc).__name__,
                )
                self._records[worker_job_id] = updated
                reconciled.append(updated)
                continue
            updated = self._apply_snapshot(record, snapshot)
            reconciled.append(updated)
        return tuple(reconciled)

    def _apply_snapshot(
        self,
        record: DispatchRecord,
        snapshot: ExecutionSnapshot,
    ) -> DispatchRecord:
        terminal = snapshot.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
        state = DispatchState.TERMINAL if terminal else DispatchState.RUNNING
        updated = replace(
            record,
            state=state,
            snapshot=snapshot,
            last_error=None,
        )
        if terminal:
            reservation = next(
                (
                    item
                    for item in self.registry.active_reservations()
                    if item.reservation_id == record.reservation_id
                ),
                None,
            )
            if reservation is not None and reservation.status is ReservationStatus.ACTIVE:
                self.registry.release_reservation(record.reservation_id)
        self._records[record.job.worker_job_id] = updated
        return updated


def placement_worker_id(placement: ScheduledPlacement) -> str:
    """Small helper for callers that need a non-optional selected worker after scheduling."""

    selected = placement.decision.selected_worker_id
    if selected is None:
        raise RegistryError("placement contains no selected worker")
    return selected
