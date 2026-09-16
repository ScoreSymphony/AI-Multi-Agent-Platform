"""Pure deterministic worker eligibility and ranking policy.

This module deliberately owns no registry, reservation, heartbeat, telemetry, pressure sampling,
or dispatch I/O. Callers provide the current resource/concurrency facts explicitly so placement
policy can be validated independently from distributed-runtime infrastructure.
"""

from __future__ import annotations

from .models import (
    CandidateEvaluation,
    JobRequirements,
    NodeRecord,
    NodeStatus,
    RejectionCode,
    RejectionReason,
    ResourceSnapshot,
    WorkerRecord,
    WorkerStatus,
)


def evaluate_candidate(
    *,
    worker: WorkerRecord,
    node: NodeRecord,
    requirements: JobRequirements,
    available: ResourceSnapshot,
    available_concurrency: int,
) -> CandidateEvaluation:
    """Evaluate one worker from explicit immutable scheduling facts."""

    reasons: list[RejectionReason] = []

    if node.status is NodeStatus.OFFLINE:
        reasons.append(_reason(RejectionCode.NODE_OFFLINE, "node is offline"))
    elif node.status is NodeStatus.MAINTENANCE:
        reasons.append(_reason(RejectionCode.NODE_UNHEALTHY, "node is in maintenance"))
    elif node.status is NodeStatus.DEGRADED:
        reasons.append(_reason(RejectionCode.NODE_UNHEALTHY, "node is degraded"))
    if node.draining or node.maintenance:
        reasons.append(_reason(RejectionCode.NODE_DRAINING, "node rejects new work"))

    if worker.status is WorkerStatus.OFFLINE:
        reasons.append(_reason(RejectionCode.WORKER_OFFLINE, "worker is offline"))
    elif worker.status is WorkerStatus.UNHEALTHY:
        reasons.append(_reason(RejectionCode.WORKER_UNHEALTHY, "worker is unhealthy"))
    elif worker.status is WorkerStatus.DEGRADED:
        reasons.append(_reason(RejectionCode.WORKER_UNHEALTHY, "worker is degraded"))
    if worker.draining:
        reasons.append(_reason(RejectionCode.WORKER_DRAINING, "worker rejects new work"))

    if (
        requirements.executor_type is not None
        and requirements.executor_type not in worker.supported_executors
    ):
        reasons.append(
            _reason(RejectionCode.EXECUTOR_UNSUPPORTED, "required executor unavailable")
        )

    missing_capabilities = set(requirements.capability_refs) - set(worker.capability_refs)
    if missing_capabilities:
        reasons.append(
            _reason(RejectionCode.CAPABILITY_UNSUPPORTED, "required capability unavailable")
        )

    runtimes = set(node.supported_runtimes) | set(worker.supported_runtimes)
    if requirements.runtime is not None and requirements.runtime not in runtimes:
        reasons.append(_reason(RejectionCode.RUNTIME_UNSUPPORTED, "runtime unavailable"))
    if requirements.os_name is not None and requirements.os_name != node.os_name:
        reasons.append(_reason(RejectionCode.OS_UNSUPPORTED, "OS constraint mismatch"))
    if requirements.architecture is not None and requirements.architecture != node.architecture:
        reasons.append(
            _reason(RejectionCode.ARCHITECTURE_UNSUPPORTED, "architecture constraint mismatch")
        )

    if requirements.cpu_cores_min > available.cpu_cores_available:
        reasons.append(_reason(RejectionCode.CPU_INSUFFICIENT, "insufficient CPU"))
    if requirements.ram_min_bytes > available.ram_available_bytes:
        reasons.append(_reason(RejectionCode.RAM_INSUFFICIENT, "insufficient RAM"))
    if requirements.storage_min_bytes > available.storage_available_bytes:
        reasons.append(_reason(RejectionCode.STORAGE_INSUFFICIENT, "insufficient storage"))

    if requirements.gpu == "required" and not available.accelerators:
        reasons.append(_reason(RejectionCode.GPU_REQUIRED, "accelerator required"))
    if requirements.gpu == "forbidden" and node.resources.accelerators:
        reasons.append(_reason(RejectionCode.GPU_REQUIRED, "CPU-only placement required"))
    if (
        requirements.vram_min_bytes > 0
        and available.max_available_accelerator_memory_bytes < requirements.vram_min_bytes
    ):
        reasons.append(_reason(RejectionCode.VRAM_INSUFFICIENT, "insufficient VRAM"))

    models = set(node.model_refs) | set(worker.model_refs)
    if requirements.model_ref is not None and requirements.model_ref not in models:
        reasons.append(_reason(RejectionCode.MODEL_UNAVAILABLE, "required model unavailable"))
    if (
        requirements.allowed_trust_levels
        and node.trust_level not in requirements.allowed_trust_levels
    ):
        reasons.append(_reason(RejectionCode.TRUST_INSUFFICIENT, "node trust level not allowed"))

    labels = set(node.labels)
    if set(requirements.required_labels) - labels:
        reasons.append(_reason(RejectionCode.LABEL_MISMATCH, "required label missing"))
    if node.node_id in requirements.anti_affinity_node_ids:
        reasons.append(_reason(RejectionCode.ANTI_AFFINITY, "node excluded by anti-affinity"))
    if requirements.network_required and not node.network_available:
        reasons.append(_reason(RejectionCode.NETWORK_UNAVAILABLE, "network unavailable"))

    if requirements.concurrency_units > available_concurrency:
        reasons.append(
            _reason(RejectionCode.CONCURRENCY_EXHAUSTED, "worker concurrency exhausted")
        )

    score = (
        score_candidate(worker=worker, node=node, requirements=requirements) if not reasons else 0
    )
    return CandidateEvaluation(
        worker_id=worker.worker_id,
        node_id=node.node_id,
        accepted=not reasons,
        score=score,
        reasons=tuple(reasons),
    )


def score_candidate(
    *,
    worker: WorkerRecord,
    node: NodeRecord,
    requirements: JobRequirements,
) -> int:
    """Score only explicit preferences; canonical worker ID remains the tie-breaker."""

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


def select_worker(evaluations: tuple[CandidateEvaluation, ...]) -> str | None:
    """Select the highest-scoring accepted worker with stable ID tie-breaking."""

    accepted = [evaluation for evaluation in evaluations if evaluation.accepted]
    if not accepted:
        return None
    selected = min(
        accepted,
        key=lambda evaluation: (-evaluation.score, evaluation.worker_id),
    )
    return selected.worker_id


def _reason(code: RejectionCode, message: str) -> RejectionReason:
    return RejectionReason(code=code, message=message)
