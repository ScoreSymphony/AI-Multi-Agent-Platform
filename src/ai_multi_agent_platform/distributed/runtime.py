"""Scheduling, dispatch and reconciliation across replaceable workers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from time import perf_counter
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import (
    AuthorizationProvider,
    ExecutionHandle,
    ExecutionSnapshot,
    RetryMode,
)
from ai_multi_agent_platform.contracts.authorization import AuthorizationRequest
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.security.authorization import infer_actor_identity

from .failover import (
    FailoverError,
    FailoverRejectionCode,
    WorkerOwnershipFencer,
)
from .models import (
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    Reservation,
    ReservationStatus,
    WorkerJobRequest,
    WorkerJobResult,
    WorkerRecord,
    WorkerStatus,
    utc_now,
)
from .registry import DistributedRegistry, RegistryError
from .scheduler import DeterministicScheduler, NoEligibleWorkerError, ScheduledPlacement
from .worker import WorkerDispatcher

if TYPE_CHECKING:
    from .persistence import DistributedStateStore
    from .telemetry import DistributedTelemetry


class DispatchState(StrEnum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    LOST = "lost"
    FENCED = "fenced"
    CANCEL_PENDING = "cancel_pending"
    TERMINAL = "terminal"


class DispatchAuthorizationError(RegistryError):
    """Raised when canonical #15 policy rejects an exact-Worker dispatch."""


@runtime_checkable
class WorkerResultProvider(Protocol):
    """Optional terminal-result surface implemented by local/remote Worker adapters."""

    async def result(self, worker_job_id: str) -> WorkerJobResult | None: ...


@dataclass(frozen=True, slots=True)
class DispatchRecord:
    job: WorkerJobRequest
    worker_id: str
    reservation_id: str
    state: DispatchState
    handle: ExecutionHandle | None = None
    snapshot: ExecutionSnapshot | None = None
    result: WorkerJobResult | None = None
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
        ownership_fencer: WorkerOwnershipFencer | None = None,
        authorization: AuthorizationProvider | None = None,
    ) -> None:
        self.registry = registry
        self.telemetry = telemetry
        self.scheduler = scheduler or DeterministicScheduler(registry, telemetry=telemetry)
        self._dispatchers: dict[str, WorkerDispatcher] = {}
        self._records: dict[str, DispatchRecord] = {}
        self._state_store = state_store
        self._ownership_fencer = ownership_fencer
        self._authorization = authorization
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

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        """Retrieve and durably retain one terminal Worker result without re-dispatching work."""

        record = self.get_record(worker_job_id)
        if record.result is not None:
            return record.result
        dispatcher = self._dispatchers.get(record.worker_id)
        if dispatcher is None:
            raise RegistryError(f"worker result is not currently reachable: {record.worker_id}")
        if not isinstance(dispatcher, WorkerResultProvider):
            raise RegistryError(
                f"worker dispatcher does not expose terminal results: {record.worker_id}"
            )

        result = await dispatcher.result(worker_job_id)
        if result is None:
            return None
        self._validate_result(record, result)
        updated = record
        if result.execution is not None:
            updated = self._apply_snapshot(record, result.execution, now=utc_now())
        updated = replace(updated, result=result)
        self._records[worker_job_id] = updated
        self._persist()
        return result

    async def fence_for_failover(
        self,
        worker_job_id: str,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        """Persist proof that the lost Worker no longer owns execution before redispatch."""

        record = self.get_record(worker_job_id)
        if record.state is DispatchState.FENCED:
            return record
        if record.state is DispatchState.CANCEL_PENDING:
            raise FailoverError(
                FailoverRejectionCode.CANCELLATION_PENDING,
                "worker job cancellation is pending; failover is not allowed",
            )
        if record.state is not DispatchState.LOST:
            raise FailoverError(
                FailoverRejectionCode.STATE_NOT_LOST,
                f"worker job must be lost before failover fencing; state={record.state.value}",
            )
        self._assert_retry_safe(record.job)
        if self._ownership_fencer is None:
            raise FailoverError(
                FailoverRejectionCode.FENCE_UNAVAILABLE,
                "no ownership fencer is configured for lost-worker failover",
            )

        try:
            receipt = await self._ownership_fencer.fence(
                worker_id=record.worker_id,
                job=record.job,
            )
        except FailoverError:
            raise
        except Exception as exc:
            raise FailoverError(
                FailoverRejectionCode.FENCE_REJECTED,
                "ownership fencer could not prove the previous Worker stopped",
            ) from exc
        if receipt.worker_job_id != worker_job_id or receipt.worker_id != record.worker_id:
            raise FailoverError(
                FailoverRejectionCode.FENCE_IDENTITY_MISMATCH,
                "ownership fence receipt does not match the lost Worker Job ownership",
            )

        timestamp = now or utc_now()
        reservation = self._active_reservation(record.reservation_id)
        if reservation is not None:
            self.registry.release_reservation(record.reservation_id)
            if self.telemetry is not None:
                self.telemetry.reservation(
                    record.job,
                    reservation,
                    event="released_failover_fenced",
                )
        fenced = replace(
            record,
            state=DispatchState.FENCED,
            last_error="ownership_fenced",
        )
        self._records[worker_job_id] = fenced
        self._observe_reconciliation(
            record,
            fenced,
            timestamp=timestamp,
            node_id=self._node_id_or_none(record.worker_id),
        )
        self._persist()
        return fenced

    async def redispatch_fenced(
        self,
        worker_job_id: str,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        """Create the next dispatch attempt on a different eligible Worker after fencing."""

        record = self.get_record(worker_job_id)
        if record.state is not DispatchState.FENCED:
            raise FailoverError(
                FailoverRejectionCode.NOT_FENCED,
                "worker job ownership must be fenced before cross-Worker redispatch",
            )
        self._assert_retry_safe(record.job)
        timestamp = now or utc_now()
        next_job = replace(record.job, dispatch_attempt=record.job.dispatch_attempt + 1)
        decision = self.scheduler.evaluate(next_job)
        alternatives = sorted(
            (
                evaluation
                for evaluation in decision.evaluations
                if evaluation.accepted
                and evaluation.worker_id != record.worker_id
                and evaluation.worker_id in self._dispatchers
            ),
            key=lambda evaluation: (-evaluation.score, evaluation.worker_id),
        )
        for evaluation in alternatives:
            try:
                placement = self.scheduler.schedule_to_worker(
                    next_job,
                    evaluation.worker_id,
                    now=timestamp,
                )
            except NoEligibleWorkerError:
                continue
            return await self._dispatch_placement(
                next_job,
                placement,
                timestamp=timestamp,
            )
        raise FailoverError(
            FailoverRejectionCode.NO_ALTERNATE_WORKER,
            "no alternate attached Worker is currently eligible for fenced redispatch",
        )

    async def failover(
        self,
        worker_job_id: str,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        """Fence one lost owner and redispatch the same canonical job as the next attempt."""

        await self.fence_for_failover(worker_job_id, now=now)
        return await self.redispatch_fenced(worker_job_id, now=now)

    async def cancel(self, worker_job_id: str) -> DispatchRecord:
        record = self.get_record(worker_job_id)
        if record.state is DispatchState.TERMINAL:
            return record
        if record.state is DispatchState.FENCED:
            cancelled = replace(
                record,
                state=DispatchState.TERMINAL,
                snapshot=ExecutionSnapshot(
                    run_id=record.job.execution.run_id,
                    status=RunStatus.CANCELLED,
                ),
                last_error=None,
            )
            self._records[worker_job_id] = cancelled
            self._persist()
            return cancelled
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
            if record.state in {DispatchState.FENCED, DispatchState.TERMINAL}:
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

        try:
            await self._authorize_dispatch(
                job,
                worker_id,
                node_id=placement.reservation.node_id,
            )
        except Exception:
            self.registry.release_reservation(placement.reservation.reservation_id)
            if self.telemetry is not None:
                self.telemetry.reservation(
                    job,
                    placement.reservation,
                    event="released_unauthorized",
                )
            self._persist()
            raise

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

    async def _authorize_dispatch(
        self,
        job: WorkerJobRequest,
        worker_id: str,
        *,
        node_id: str,
    ) -> None:
        if self._authorization is None:
            return
        operation = job.execution.context
        principal_ref = job.actor_ref or operation.owner_id or "service:distributed-runtime"
        actor_identity_ref = principal_ref
        if (
            job.actor_ref is None
            and operation.owner_id is not None
            and operation.owner_type == "user"
        ):
            actor_identity_ref = f"user:{operation.owner_id}"
        actor_type = infer_actor_identity(actor_identity_ref).actor_type.value
        task_id = job.execution.subject_id if job.execution.subject_type == "task" else None
        capability_ref = (
            job.requirements.capability_refs[0]
            if len(job.requirements.capability_refs) == 1
            else None
        )
        decision = await self._authorization.authorize(
            AuthorizationRequest(
                principal_ref=principal_ref,
                action="execute",
                resource_ref=worker_id,
                context=operation,
                actor_type=actor_type,
                resource_type="worker",
                workspace_id=job.workspace_ref,
                task_id=task_id,
                run_id=job.execution.run_id,
                capability_ref=capability_ref,
                side_effect="worker.dispatch",
                node_id=node_id,
            )
        )
        if not decision.allowed:
            raise DispatchAuthorizationError(
                decision.reason or "worker dispatch denied by canonical authorization policy"
            )

    def _validate_result(self, record: DispatchRecord, result: WorkerJobResult) -> None:
        if result.worker_job_id != record.job.worker_job_id:
            raise RegistryError("Worker result belongs to a different Worker Job")
        if result.worker_id != record.worker_id:
            raise RegistryError("Worker result belongs to a different Worker")
        if result.execution is not None and result.execution.run_id != record.job.execution.run_id:
            raise RegistryError("Worker result belongs to a different canonical Run")

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
        reservation = self._active_reservation(record.reservation_id)
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

    def _assert_retry_safe(self, job: WorkerJobRequest) -> None:
        if job.execution.context.control.retry_mode is RetryMode.NEVER:
            raise FailoverError(
                FailoverRejectionCode.RETRY_FORBIDDEN,
                "worker job retry_mode forbids cross-Worker failover",
            )

    def _active_reservation(self, reservation_id: str) -> Reservation | None:
        return next(
            (
                item
                for item in self.registry.active_reservations()
                if item.reservation_id == reservation_id
            ),
            None,
        )

    def _node_id_or_none(self, worker_id: str) -> str | None:
        try:
            return self.registry.get_worker(worker_id).node_id
        except RegistryError:
            return None

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
