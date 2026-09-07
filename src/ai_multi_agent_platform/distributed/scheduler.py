"""Deterministic capability/resource scheduler for local and remote workers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from .models import (
    CandidateEvaluation,
    JobRequirements,
    NodeRecord,
    NodeStatus,
    RejectionCode,
    RejectionReason,
    Reservation,
    SchedulingDecision,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from .pressure import (
    AdmissionAction,
    AdmissionDecision,
    PressureAdmissionPolicy,
    PressureSnapshotProvider,
)
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

    Optional issue-#500 pressure admission augments the existing #14 scheduler.  The scheduler
    remains the sole placement/reservation authority: pressure code can only admit or reject a
    candidate before reservation and never creates a second dispatch/lifecycle path.
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

    def evaluate(
        self,
        job: WorkerJobRequest,
        *,
        now: datetime | None = None,
    ) -> SchedulingDecision:
        evaluations = tuple(
            self._evaluate_worker(
                worker,
                self.registry.get_node(worker.node_id),
                job,
                now=now,
            )
            for worker in self.registry.list_workers()
        )
        accepted = [evaluation for evaluation in evaluations if evaluation.accepted]
        selected = None
        if accepted:
            selected = sorted(
                accepted,
                key=lambda evaluation: (-evaluation.score, evaluation.worker_id),
            )[0].worker_id
        return SchedulingDecision(
            worker_job_id=job.worker_job_id,
            selected_worker_id=selected,
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
        return self._evaluate_worker(worker, node, job, now=now)

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
        snapshot = (
            None
            if self.pressure_provider is None
            else self.pressure_provider.snapshot_for_node(node.node_id)
        )
        workload_class = (
            None if self.workload_class_resolver is None else self.workload_class_resolver(job)
        )
        return self.pressure_policy.decide(
            node=node,
            worker=worker,
            requirements=job.requirements,
            available=available,
            snapshot=snapshot,
            now=now,
            workload_class=workload_class,
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
    ) -> CandidateEvaluation:
        requirements = job.requirements
        reasons: list[RejectionReason] = []

        if node.status is NodeStatus.OFFLINE:
            reasons.append(self._reason(RejectionCode.NODE_OFFLINE, "node is offline"))
        elif node.status is NodeStatus.MAINTENANCE:
            reasons.append(self._reason(RejectionCode.NODE_UNHEALTHY, "node is in maintenance"))
        elif node.status is NodeStatus.DEGRADED:
            reasons.append(self._reason(RejectionCode.NODE_UNHEALTHY, "node is degraded"))
        if node.draining or node.maintenance:
            reasons.append(self._reason(RejectionCode.NODE_DRAINING, "node rejects new work"))

        if worker.status is WorkerStatus.OFFLINE:
            reasons.append(self._reason(RejectionCode.WORKER_OFFLINE, "worker is offline"))
        elif worker.status is WorkerStatus.UNHEALTHY:
            reasons.append(self._reason(RejectionCode.WORKER_UNHEALTHY, "worker is unhealthy"))
        elif worker.status is WorkerStatus.DEGRADED:
            reasons.append(self._reason(RejectionCode.WORKER_UNHEALTHY, "worker is degraded"))
        if worker.draining:
            reasons.append(self._reason(RejectionCode.WORKER_DRAINING, "worker rejects new work"))

        if (
            requirements.executor_type is not None
            and requirements.executor_type not in worker.supported_executors
        ):
            reasons.append(
                self._reason(RejectionCode.EXECUTOR_UNSUPPORTED, "required executor unavailable")
            )
        missing_capabilities = set(requirements.capability_refs) - set(worker.capability_refs)
        if missing_capabilities:
            reasons.append(
                self._reason(
                    RejectionCode.CAPABILITY_UNSUPPORTED,
                    "required capability unavailable",
                )
            )

        runtimes = set(node.supported_runtimes) | set(worker.supported_runtimes)
        if requirements.runtime is not None and requirements.runtime not in runtimes:
            reasons.append(self._reason(RejectionCode.RUNTIME_UNSUPPORTED, "runtime unavailable"))
        if requirements.os_name is not None and requirements.os_name != node.os_name:
            reasons.append(self._reason(RejectionCode.OS_UNSUPPORTED, "OS constraint mismatch"))

        available = self.registry.available_node_resources(node.node_id)
        if requirements.cpu_cores_min > available.cpu_cores_available:
            reasons.append(self._reason(RejectionCode.CPU_INSUFFICIENT, "insufficient CPU"))
        if requirements.ram_min_bytes > available.ram_available_bytes:
            reasons.append(self._reason(RejectionCode.RAM_INSUFFICIENT, "insufficient RAM"))
        if requirements.storage_min_bytes > available.storage_available_bytes:
            reasons.append(self._reason(RejectionCode.STORAGE_INSUFFICIENT, "insufficient storage"))

        if requirements.gpu == "required" and not available.accelerators:
            reasons.append(self._reason(RejectionCode.GPU_REQUIRED, "accelerator required"))
        if requirements.gpu == "forbidden" and node.resources.accelerators:
            reasons.append(self._reason(RejectionCode.GPU_REQUIRED, "CPU-only placement required"))
        if (
            requirements.vram_min_bytes > 0
            and available.max_available_accelerator_memory_bytes < requirements.vram_min_bytes
        ):
            reasons.append(self._reason(RejectionCode.VRAM_INSUFFICIENT, "insufficient VRAM"))

        models = set(node.model_refs) | set(worker.model_refs)
        if requirements.model_ref is not None and requirements.model_ref not in models:
            reasons.append(
                self._reason(RejectionCode.MODEL_UNAVAILABLE, "required model unavailable")
            )
        if (
            requirements.allowed_trust_levels
            and node.trust_level not in requirements.allowed_trust_levels
        ):
            reasons.append(
                self._reason(RejectionCode.TRUST_INSUFFICIENT, "node trust level not allowed")
            )

        labels = set(node.labels)
        if set(requirements.required_labels) - labels:
            reasons.append(self._reason(RejectionCode.LABEL_MISMATCH, "required label missing"))
        if node.node_id in requirements.anti_affinity_node_ids:
            reasons.append(
                self._reason(RejectionCode.ANTI_AFFINITY, "node excluded by anti-affinity")
            )
        if requirements.network_required and not node.network_available:
            reasons.append(self._reason(RejectionCode.NETWORK_UNAVAILABLE, "network unavailable"))

        if requirements.concurrency_units > self.registry.available_concurrency(worker.worker_id):
            reasons.append(
                self._reason(RejectionCode.CONCURRENCY_EXHAUSTED, "worker concurrency exhausted")
            )

        # Pressure admission is deliberately last: ordinary #14 capability/resource eligibility
        # is authoritative, and no pressure decision may reserve or dispatch work itself.
        if not reasons and self.pressure_policy is not None:
            admission = self.pressure_admission(job, worker.worker_id, now=now)
            assert admission is not None
            if not admission.admits:
                reasons.append(self._pressure_rejection(admission))

        score = self._score(worker, node, requirements) if not reasons else 0
        return CandidateEvaluation(
            worker_id=worker.worker_id,
            node_id=node.node_id,
            accepted=not reasons,
            score=score,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _score(worker: WorkerRecord, node: NodeRecord, requirements: JobRequirements) -> int:
        """Score only explicit preferences; tie-breaks are handled by canonical worker ID."""

        score = 0
        if worker.worker_id in requirements.preferred_worker_ids:
            score += 1000
        if node.node_id in requirements.preferred_node_ids:
            score += 500
        score += 50 * len(set(requirements.preferred_labels) & set(node.labels))
        locality = set(node.locality_refs) | set(worker.locality_refs)
        score += 100 * len(set(requirements.locality_refs) & locality)
        if requirements.model_ref is not None and requirements.model_ref in worker.model_refs:
            score += 25
        if requirements.runtime is not None and requirements.runtime in worker.supported_runtimes:
            score += 10
        return score

    @staticmethod
    def _pressure_rejection(admission: AdmissionDecision) -> RejectionReason:
        # Existing #14 reason codes remain stable in this first contract slice.  The structured
        # AdmissionDecision carries the precise pressure action/reasons; the scheduler maps the
        # result conservatively onto the existing unhealthy/draining vocabulary for callers that
        # only understand #14 CandidateEvaluation today.
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

    @staticmethod
    def _reason(code: RejectionCode, message: str) -> RejectionReason:
        return RejectionReason(code=code, message=message)
