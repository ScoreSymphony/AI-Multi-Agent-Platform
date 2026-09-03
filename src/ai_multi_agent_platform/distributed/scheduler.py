"""Deterministic capability/resource scheduler for local and remote workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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
from .registry import DistributedRegistry, RegistryError


class NoEligibleWorkerError(RegistryError):
    """Raised when a scheduling request has no eligible worker."""


@dataclass(frozen=True, slots=True)
class ScheduledPlacement:
    decision: SchedulingDecision
    reservation: Reservation


class DeterministicScheduler:
    """Reference scheduler with explainable filtering and stable tie-breaking."""

    def __init__(self, registry: DistributedRegistry) -> None:
        self.registry = registry

    def evaluate(self, job: WorkerJobRequest) -> SchedulingDecision:
        evaluations = tuple(
            self._evaluate_worker(worker, self.registry.get_node(worker.node_id), job.requirements)
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

    def schedule(
        self,
        job: WorkerJobRequest,
        *,
        now: datetime | None = None,
    ) -> ScheduledPlacement:
        """Select and atomically reserve a deterministic eligible worker."""

        self.registry.expire_heartbeats(now=now)
        self.registry.expire_reservations(now=now)
        decision = self.evaluate(job)
        if decision.selected_worker_id is None:
            raise NoEligibleWorkerError("no eligible worker for job requirements")
        reservation = self.registry.reserve(
            worker_job_id=job.worker_job_id,
            worker_id=decision.selected_worker_id,
            requirements=job.requirements,
            now=now,
        )
        return ScheduledPlacement(decision=decision, reservation=reservation)

    def _evaluate_worker(
        self,
        worker: WorkerRecord,
        node: NodeRecord,
        requirements: JobRequirements,
    ) -> CandidateEvaluation:
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
    def _reason(code: RejectionCode, message: str) -> RejectionReason:
        return RejectionReason(code=code, message=message)
