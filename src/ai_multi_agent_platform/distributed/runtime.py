"""Scheduling, dispatch and reconciliation across replaceable workers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from time import perf_counter
from typing import TYPE_CHECKING

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot
from ai_multi_agent_platform.domain import RunStatus

from .models import (
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    ReservationStatus,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
    utc_now,
)
from .registry import DistributedRegistry, RegistryError
from .scheduler import DeterministicScheduler, ScheduledPlacement
from .worker import WorkerDispatcher

if TYPE_CHECKING:
    from .persistence import DistributedStateStore
    from .telemetry import DistributedTelemetry


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
        state_store: DistributedStateStore | None = None,
        telemetry: DistributedTelemetry | None = None,
    ) -> None:
        self.registry = registry
        self.telemetry = telemetry
        self.scheduler = scheduler or DeterministicScheduler(registry, telemetry=telemetry)
        self._dispatchers: dict[str, WorkerDispatcher] = {}
        self._records: dict[str, DispatchRecord] = {}
        self._state_store = state_store
        if self._state_store is not None:
            self._state_store.restore(self.registry, self)

    def records(self) -> tuple[DispatchRecord, ...]:
        return tuple(self._records[worker_job_id] for worker_job_id in sorted(self._records))

    def restore_records(self, records: tuple[DispatchRecord, ...]) -> None:
        """Restore persisted dispatch ownership before workers re-establish liveness."""

        if self._records:
            raise RegistryError("dispatch record restore requires an empty runtime")
        restored = {record.job.worker_job_id: record for record in records}
        if len(restored) != len(records):
            raise RegistryError("distributed state contains duplicate worker job records")
        self._records = restored

    def register(
        self,
        request: RegistrationRequest,
        *,
        now: datetime | None = None,
    ) -> NodeRecord:
        node = self.registry.register(request, now=now)
        self._persist()
        return node

    def heartbeat(self, heartbeat: Heartbeat) -> NodeRecord:
        node = self.registry.heartbeat(heartbeat)
        if self.telemetry is not None:
            workers = tuple(
                worker
                for worker in self.registry.list_workers()
                if worker.node_id == heartbeat.node_id
            )
            self.telemetry.heartbeat(node, workers, observed_at=heartbeat.observed_at)
        self._persist()
        return node

    def set_node_draining(self, node_id: str, *, draining: bool) -> NodeRecord:
        node = self.registry.set_node_draining(node_id, draining=draining)
        self._persist()
        return node

    def set_node_maintenance(self, node_id: str, *, maintenance: bool) -> NodeRecord:
        node = self.registry.set_node_maintenance(node_id, maintenance=maintenance)
        self._persist()
        return node

    def set_worker_draining(self, worker_id: str, *, draining: bool) -> WorkerRecord:
        worker = self.registry.set_worker_draining(worker_id, draining=draining)
        self._persist()
        return worker

    def deregister_worker(self, worker_id: str) -> None:
        self.registry.deregister_worker(worker_id)
        self._dispatchers.pop(worker_id, None)
        self._persist()

    def deregister_node(self, node_id: str) -> None:
        worker_ids = tuple(
            worker.worker_id for worker in self.registry.list_workers() if worker.node_id == node_id
        )
        self.registry.deregister_node(node_id)
        for worker_id in worker_ids:
            self._dispatchers.pop(worker_id, None)
        self._persist()

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
        existing = self._existing_record(job)
        if existing is not None:
            return existing
        timestamp = now or utc_now()
        placement = self.scheduler.schedule(job, now=timestamp)
        return await self._dispatch_placement(job, placement, timestamp=timestamp)

    async def dispatch_to_worker(
        self,
        job: WorkerJobRequest,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        """Dispatch to exactly one requested eligible Worker without scheduler fallback."""

        existing = self._existing_record(job)
        if existing is not None:
            if existing.worker_id != worker_id:
                raise RegistryError(
                    "existing worker job ownership cannot move to another Worker implicitly"
                )
            return existing
        timestamp = now or utc_now()
        placement = self.scheduler.schedule_to_worker(job, worker_id, now=timestamp)
        return await self._dispatch_placement(job, placement, timestamp=timestamp)

    async def cancel(self, worker_job_id: str) -> DispatchRecord:
        record = self.get_record(worker_job_id)
        if record.state is DispatchState.TERMINAL:
            return record
        worker = self.registry.get_worker(record.worker_id)
        dispatcher = self._dispatchers.get(record.worker_id)
        if worker.status is WorkerStatus.OFFLINE or dispatcher is None:
            pending = replace(record, state=DispatchState.CANCEL_PENDING)
            self._records[worker_job_id] = pending
            self._persist()
            return pending
        snapshot = await dispatcher.cancel(worker_job_id)
        updated = self._apply_snapshot(record, snapshot, now=utc_now())
        self._persist()
        return updated

    async def reconcile(self, *, now: datetime | None = None) -> tuple[DispatchRecord, ...]:
        """Reconcile liveness and job state; remote running is never trusted indefinitely."""

        timestamp = now or utc_now()
        self.registry.expire_heartbeats(now=timestamp)
        if self.telemetry is not None:
            self.telemetry.liveness(
                self.registry.list_nodes(),
                self.registry.list_workers(),
                observed_at=timestamp,
            )
        reconciled: list[DispatchRecord] = []
        for worker_job_id in sorted(self._records):
            record = self._records[worker_job_id]
            if record.state is DispatchState.TERMINAL:
                reconciled.append(record)
                continue

            try:
                worker = self.registry.get_worker(record.worker_id)
            except RegistryError:
                updated = replace(
                    record,
                    state=DispatchState.LOST,
                    last_error="worker_unreachable",
                )
                self._records[worker_job_id] = updated
                self._observe_reconciliation(record, updated, timestamp=timestamp, node_id=None)
                reconciled.append(updated)
                continue

            dispatcher = self._dispatchers.get(record.worker_id)
            if worker.status is WorkerStatus.OFFLINE or dispatcher is None:
                state = (
                    DispatchState.CANCEL_PENDING
                    if record.state is DispatchState.CANCEL_PENDING
                    else DispatchState.LOST
                )
                updated = replace(record, state=state, last_error="worker_unreachable")
                self._records[worker_job_id] = updated
                self._observe_reconciliation(
                    record,
                    updated,
                    timestamp=timestamp,
                    node_id=worker.node_id,
                )
                reconciled.append(updated)
                continue

            try:
                if record.state is DispatchState.CANCEL_PENDING:
                    snapshot = await dispatcher.cancel(worker_job_id)
                else:
                    snapshot = await dispatcher.get(worker_job_id)
            except Exception:
                updated = replace(
                    record,
                    state=DispatchState.LOST,
                    last_error="worker_state_unavailable",
                )
                self._records[worker_job_id] = updated
                self._observe_reconciliation(
                    record,
                    updated,
                    timestamp=timestamp,
                    node_id=worker.node_id,
                )
                reconciled.append(updated)
                continue
            updated = self._apply_snapshot(record, snapshot, now=timestamp)
            self._observe_reconciliation(
                record,
                updated,
                timestamp=timestamp,
                node_id=worker.node_id,
            )
            reconciled.append(updated)

        self.registry.expire_reservations(now=timestamp)
        self._persist()
        return tuple(reconciled)

    def _existing_record(self, job: WorkerJobRequest) -> DispatchRecord | None:
        existing = self._records.get(job.worker_job_id)
        if existing is not None and existing.job != job:
            raise RegistryError("duplicate worker_job_id carries a different request")
        return existing

    async def _dispatch_placement(
        self,
        job: WorkerJobRequest,
        placement: ScheduledPlacement,
        *,
        timestamp: datetime,
    ) -> DispatchRecord:
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
            if self.telemetry is not None:
                self.telemetry.reservation(job, placement.reservation, event="released_unreachable")
            self._persist()
            raise RegistryError(f"selected worker has no attached dispatcher: {worker_id}")

        # Persist ownership before the external dispatch boundary. If acknowledgement is lost or
        # the control process restarts after the worker accepted the job, recovery must reconcile
        # this exact worker before another worker can claim the same canonical worker job.
        self._records[job.worker_job_id] = reserved
        self._persist()
        started = perf_counter()
        try:
            handle = await dispatcher.dispatch(job)
        except Exception:
            duration = max(0.0, perf_counter() - started)
            lost = replace(
                reserved,
                state=DispatchState.LOST,
                last_error="dispatch_outcome_unknown",
            )
            self._records[job.worker_job_id] = lost
            if self.telemetry is not None:
                self.telemetry.dispatch(
                    job,
                    node_id=placement.reservation.node_id,
                    worker_id=worker_id,
                    duration_seconds=duration,
                    succeeded=False,
                    failure_code="dispatch_outcome_unknown",
                )
            self._persist()
            raise

        duration = max(0.0, perf_counter() - started)
        committed = self.registry.commit_reservation(
            placement.reservation.reservation_id,
            now=timestamp,
        )
        if self.telemetry is not None:
            self.telemetry.reservation(job, committed, event="committed")
            self.telemetry.dispatch(
                job,
                node_id=placement.reservation.node_id,
                worker_id=worker_id,
                duration_seconds=duration,
                succeeded=True,
            )
        dispatched = replace(
            reserved,
            state=DispatchState.DISPATCHED,
            handle=handle,
            last_error=None,
        )
        self._records[job.worker_job_id] = dispatched
        self._persist()
        return dispatched

    def _apply_snapshot(
        self,
        record: DispatchRecord,
        snapshot: ExecutionSnapshot,
        *,
        now: datetime,
    ) -> DispatchRecord:
        terminal = snapshot.status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
        reservation = next(
            (
                item
                for item in self.registry.active_reservations()
                if item.reservation_id == record.reservation_id
            ),
            None,
        )
        if terminal:
            if reservation is not None:
                self.registry.release_reservation(record.reservation_id)
                if self.telemetry is not None:
                    self.telemetry.reservation(record.job, reservation, event="released_terminal")
            state = DispatchState.TERMINAL
            last_error = None
        elif reservation is None:
            state = DispatchState.LOST
            last_error = "reservation_missing"
        else:
            if reservation.status is ReservationStatus.RESERVED:
                updated_reservation = self.registry.commit_reservation(
                    record.reservation_id, now=now
                )
                reservation_event = "committed"
            else:
                updated_reservation = self.registry.renew_reservation(
                    record.reservation_id, now=now
                )
                reservation_event = "renewed"
            if self.telemetry is not None:
                self.telemetry.reservation(
                    record.job,
                    updated_reservation,
                    event=reservation_event,
                )
            state = DispatchState.RUNNING
            last_error = None

        updated = replace(
            record,
            state=state,
            snapshot=snapshot,
            last_error=last_error,
        )
        self._records[record.job.worker_job_id] = updated
        return updated

    def _observe_reconciliation(
        self,
        previous: DispatchRecord,
        current: DispatchRecord,
        *,
        timestamp: datetime,
        node_id: str | None,
    ) -> None:
        if self.telemetry is None:
            return
        self.telemetry.reconciliation(
            current.job,
            node_id=node_id,
            worker_id=current.worker_id,
            previous_state=previous.state.value,
            current_state=current.state.value,
            error_code=current.last_error,
            observed_at=timestamp,
        )

    def _persist(self) -> None:
        if self._state_store is not None:
            self._state_store.save(self.registry, self)
