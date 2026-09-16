"""Deterministic capability/resource scheduler for local and remote workers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .models import (
    CandidateEvaluation,
    NodeRecord,
    RejectionCode,
    RejectionReason,
    Reservation,
    ResourceSnapshot,
    SchedulingDecision,
    WorkerJobRequest,
    WorkerRecord,
)
from .placement_policy import evaluate_candidate, select_worker
from .pressure import (
    AdmissionAction,
    AdmissionDecision,
    HostPressureSnapshot,
    PressureAdmissionPolicy,
    PressureSnapshotProvider,
)
from .pressure_telemetry import PressureTelemetry
from .registry import DistributedRegistry, RegistryError

if TYPE_CHECKING:
    from .telemetry import DistributedTelemetry


class NoEligibleWorkerError(RegistryError):
    """Raised when a scheduling request has no eligible worker."""


@dataclass(frozen=True, slots=True)
class ScheduledPlacement:
    decision: SchedulingDecision
    reservation: Reservation


class DeterministicScheduler:
    """Reference scheduler with explainable filtering and stable tie-breaking.

    Optional host-pressure admission augments the existing capability/resource scheduler. The
    scheduler remains the sole placement/reservation authority: pressure code can only admit or
    reject a candidate before reservation and never creates a second dispatch/lifecycle path.
    """

    def __init__(
        self,
        registry: DistributedRegistry,
        *,
        telemetry: DistributedTelemetry | None = None,
        pressure_provider: PressureSnapshotProvider | None = None,
        pressure_policy: PressureAdmissionPolicy | None = None,
        workload_class_resolver: Callable[[WorkerJobRequest], str | None] | None = None,
    ) -> None:
        self.registry = registry
        self.telemetry = telemetry
        self.pressure_provider = pressure_provider
        self.pressure_policy = pressure_policy
        self.workload_class_resolver = workload_class_resolver
        self.pressure_telemetry = (
            None if telemetry is None else PressureTelemetry(telemetry.telemetry)
        )

    def evaluate(
        self,
        job: WorkerJobRequest,
        *,
        now: datetime | None = None,
    ) -> SchedulingDecision:
        pressure_snapshots: dict[str, HostPressureSnapshot | None] = {}
        evaluations = tuple(
            self._evaluate_worker(
                worker,
                self.registry.get_node(worker.node_id),
                job,
                now=now,
                pressure_snapshots=pressure_snapshots,
            )
            for worker in self.registry.list_workers()
        )
        return SchedulingDecision(
            worker_job_id=job.worker_job_id,
            selected_worker_id=select_worker(evaluations),
            evaluations=evaluations,
        )

    def evaluate_worker(
        self,
        job: WorkerJobRequest,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> CandidateEvaluation:
        """Evaluate one explicitly requested Worker against the same hard filters."""

        worker = self.registry.get_worker(worker_id)
        node = self.registry.get_node(worker.node_id)
        return self._evaluate_worker(
            worker,
            node,
            job,
            now=now,
            pressure_snapshots={},
        )

    def pressure_admission(
        self,
        job: WorkerJobRequest,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> AdmissionDecision | None:
        """Return the structured pressure decision without reserving or dispatching work."""

        if self.pressure_policy is None:
            return None
        worker = self.registry.get_worker(worker_id)
        node = self.registry.get_node(worker.node_id)
        available = self.registry.available_node_resources(node.node_id)
        snapshot = self._pressure_snapshot(node.node_id, {})
        return self._pressure_decision(
            job=job,
            node=node,
            worker=worker,
            available=available,
            snapshot=snapshot,
            now=now,
            workload_class=self._workload_class(job),
        )

    def schedule(
        self,
        job: WorkerJobRequest,
        *,
        now: datetime | None = None,
    ) -> ScheduledPlacement:
        """Select and atomically reserve a deterministic eligible worker."""

        self.registry.expire_heartbeats(now=now)
        self.registry.expire_reservations(now=now)
        decision = self.evaluate(job, now=now)
        if self.telemetry is not None:
            self.telemetry.scheduling_decision(job, decision)
        if decision.selected_worker_id is None:
            raise NoEligibleWorkerError("no eligible worker for job requirements")
        reservation = self.registry.reserve(
            worker_job_id=job.worker_job_id,
            worker_id=decision.selected_worker_id,
            requirements=job.requirements,
            now=now,
        )
        if self.telemetry is not None:
            self.telemetry.reservation(job, reservation, event="reserved")
        return ScheduledPlacement(decision=decision, reservation=reservation)

    def schedule_to_worker(
        self,
        job: WorkerJobRequest,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledPlacement:
        """Reserve exactly one requested Worker; never fall back to another candidate."""

        self.registry.expire_heartbeats(now=now)
        self.registry.expire_reservations(now=now)
        evaluation = self.evaluate_worker(job, worker_id, now=now)
        decision = SchedulingDecision(
            worker_job_id=job.worker_job_id,
            selected_worker_id=worker_id if evaluation.accepted else None,
            evaluations=(evaluation,),
        )
        if self.telemetry is not None:
            self.telemetry.scheduling_decision(job, decision)
        if not evaluation.accepted:
            reason_codes = ", ".join(reason.code.value for reason in evaluation.reasons)
            raise NoEligibleWorkerError(
                f"requested worker {worker_id} is not eligible: {reason_codes or 'rejected'}"
            )
        reservation = self.registry.reserve(
            worker_job_id=job.worker_job_id,
            worker_id=worker_id,
            requirements=job.requirements,
            now=now,
        )
        if self.telemetry is not None:
            self.telemetry.reservation(job, reservation, event="reserved")
        return ScheduledPlacement(decision=decision, reservation=reservation)

    def _evaluate_worker(
        self,
        worker: WorkerRecord,
        node: NodeRecord,
        job: WorkerJobRequest,
        *,
        now: datetime | None,
        pressure_snapshots: dict[str, HostPressureSnapshot | None],
    ) -> CandidateEvaluation:
        available = self.registry.available_node_resources(node.node_id)
        evaluation = evaluate_candidate(
            worker=worker,
            node=node,
            requirements=job.requirements,
            available=available,
            available_concurrency=self.registry.available_concurrency(worker.worker_id),
        )

        # Pressure admission is deliberately last: ordinary capability/resource eligibility is
        # authoritative, and no pressure decision may reserve or dispatch work itself. One
        # snapshot is shared by all otherwise-eligible Workers on the same Node in this evaluation
        # so stateful/delta-based providers are sampled consistently.
        if not evaluation.accepted or self.pressure_policy is None:
            return evaluation

        snapshot = self._pressure_snapshot(node.node_id, pressure_snapshots)
        workload_class = self._workload_class(job)
        admission = self._pressure_decision(
            job=job,
            node=node,
            worker=worker,
            available=available,
            snapshot=snapshot,
            now=now,
            workload_class=workload_class,
        )
        if self.pressure_telemetry is not None:
            if snapshot is not None:
                self.pressure_telemetry.snapshot(node.node_id, snapshot)
            self.pressure_telemetry.admission(
                job,
                node_id=node.node_id,
                worker_id=worker.worker_id,
                decision=admission,
                workload_class=workload_class,
            )
        if admission.admits:
            return evaluation
        return CandidateEvaluation(
            worker_id=worker.worker_id,
            node_id=node.node_id,
            accepted=False,
            score=0,
            reasons=(self._pressure_rejection(admission),),
        )

    def _pressure_snapshot(
        self,
        node_id: str,
        snapshots: dict[str, HostPressureSnapshot | None],
    ) -> HostPressureSnapshot | None:
        if node_id not in snapshots:
            snapshots[node_id] = (
                None
                if self.pressure_provider is None
                else self.pressure_provider.snapshot_for_node(node_id)
            )
        return snapshots[node_id]

    def _pressure_decision(
        self,
        *,
        job: WorkerJobRequest,
        node: NodeRecord,
        worker: WorkerRecord,
        available: ResourceSnapshot,
        snapshot: HostPressureSnapshot | None,
        now: datetime | None,
        workload_class: str | None,
    ) -> AdmissionDecision:
        policy = self.pressure_policy
        assert policy is not None
        return policy.decide(
            node=node,
            worker=worker,
            requirements=job.requirements,
            available=available,
            snapshot=snapshot,
            now=now,
            workload_class=workload_class,
        )

    def _workload_class(self, job: WorkerJobRequest) -> str | None:
        return None if self.workload_class_resolver is None else self.workload_class_resolver(job)

    @staticmethod
    def _pressure_rejection(admission: AdmissionDecision) -> RejectionReason:
        # Existing scheduler reason codes remain stable in this contract slice. The structured
        # AdmissionDecision carries the precise pressure action/reasons; the scheduler maps the
        # result conservatively onto the existing unhealthy/draining vocabulary for callers that
        # only understand CandidateEvaluation today.
        code = (
            RejectionCode.NODE_DRAINING
            if admission.action is AdmissionAction.BLOCK_FOR_MAINTENANCE
            else RejectionCode.NODE_UNHEALTHY
        )
        reason_codes = ",".join(reason.code.value for reason in admission.reasons) or "pressure"
        return RejectionReason(
            code=code,
            message=f"pressure admission {admission.action.value}: {reason_codes}",
        )
