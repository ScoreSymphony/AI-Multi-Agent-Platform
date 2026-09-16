from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.distributed.models import (
    AcceleratorResource,
    CandidateEvaluation,
    JobRequirements,
    NodeRecord,
    NodeStatus,
    RejectionCode,
    ResourceSnapshot,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.placement_policy import (
    evaluate_candidate,
    score_candidate,
    select_worker,
)
from ai_multi_agent_platform.domain import new_id


def _resources(*, cpu: float = 8.0, ram: int = 16_000, vram: int = 16_000) -> ResourceSnapshot:
    accelerators = (
        AcceleratorResource(
            accelerator_id="gpu-0",
            memory_total_bytes=vram,
            memory_available_bytes=vram,
        ),
    ) if vram else ()
    return ResourceSnapshot(
        cpu_cores_total=cpu,
        cpu_cores_available=cpu,
        ram_total_bytes=ram,
        ram_available_bytes=ram,
        storage_total_bytes=100_000,
        storage_available_bytes=100_000,
        accelerators=accelerators,
    )


def _node() -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name="unit-node",
        resources=_resources(),
        labels=("gpu", "local"),
        os_name="linux",
        architecture="x86_64",
        supported_runtimes=("python",),
        model_refs=("model:base",),
        trust_level="trusted",
        locality_refs=("zone:a",),
    )


def _worker(node: NodeRecord) -> WorkerRecord:
    return WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        capability_refs=("tool:read",),
        supported_runtimes=("python",),
        model_refs=("model:base",),
        concurrency_limit=4,
        locality_refs=("workspace:alpha",),
    )


def _evaluate(
    *,
    node: NodeRecord | None = None,
    worker: WorkerRecord | None = None,
    requirements: JobRequirements | None = None,
    available: ResourceSnapshot | None = None,
    available_concurrency: int = 4,
) -> CandidateEvaluation:
    node = node or _node()
    worker = worker or _worker(node)
    return evaluate_candidate(
        worker=worker,
        node=node,
        requirements=requirements or JobRequirements(),
        available=available or node.resources,
        available_concurrency=available_concurrency,
    )


def _codes(evaluation: CandidateEvaluation) -> set[RejectionCode]:
    return {reason.code for reason in evaluation.reasons}


def test_exact_resource_boundaries_are_eligible() -> None:
    node = _node()
    worker = _worker(node)
    requirements = JobRequirements(
        executor_type="reference",
        capability_refs=("tool:read",),
        cpu_cores_min=8.0,
        ram_min_bytes=16_000,
        storage_min_bytes=100_000,
        gpu="required",
        vram_min_bytes=16_000,
        model_ref="model:base",
        runtime="python",
        os_name="linux",
        architecture="x86_64",
        network_required=True,
        required_labels=("gpu",),
        allowed_trust_levels=("trusted",),
        concurrency_units=4,
    )

    evaluation = _evaluate(node=node, worker=worker, requirements=requirements)

    assert evaluation.accepted is True
    assert evaluation.reasons == ()


def test_health_and_draining_rejections_are_deterministic() -> None:
    node = _node()
    worker = _worker(node)

    node_evaluation = _evaluate(node=replace(node, status=NodeStatus.OFFLINE), worker=worker)
    worker_evaluation = _evaluate(node=node, worker=replace(worker, status=WorkerStatus.UNHEALTHY))
    draining_evaluation = _evaluate(node=replace(node, draining=True), worker=worker)

    assert _codes(node_evaluation) == {RejectionCode.NODE_OFFLINE}
    assert _codes(worker_evaluation) == {RejectionCode.WORKER_UNHEALTHY}
    assert _codes(draining_evaluation) == {RejectionCode.NODE_DRAINING}


def test_capability_runtime_and_resource_failures_accumulate() -> None:
    node = _node()
    worker = _worker(node)
    evaluation = _evaluate(
        node=node,
        worker=worker,
        requirements=JobRequirements(
            executor_type="missing",
            capability_refs=("tool:write",),
            runtime="nodejs",
            cpu_cores_min=9.0,
            ram_min_bytes=17_000,
            storage_min_bytes=100_001,
        ),
    )

    assert _codes(evaluation) == {
        RejectionCode.EXECUTOR_UNSUPPORTED,
        RejectionCode.CAPABILITY_UNSUPPORTED,
        RejectionCode.RUNTIME_UNSUPPORTED,
        RejectionCode.CPU_INSUFFICIENT,
        RejectionCode.RAM_INSUFFICIENT,
        RejectionCode.STORAGE_INSUFFICIENT,
    }
    assert evaluation.score == 0


def test_constraints_trust_network_and_concurrency_failures_accumulate() -> None:
    node = _node()
    worker = _worker(node)
    evaluation = _evaluate(
        node=replace(node, network_available=False),
        worker=worker,
        requirements=JobRequirements(
            os_name="windows",
            architecture="arm64",
            required_labels=("missing",),
            anti_affinity_node_ids=(node.node_id,),
            allowed_trust_levels=("sandboxed",),
            network_required=True,
            concurrency_units=2,
        ),
        available_concurrency=1,
    )

    assert _codes(evaluation) == {
        RejectionCode.OS_UNSUPPORTED,
        RejectionCode.ARCHITECTURE_UNSUPPORTED,
        RejectionCode.TRUST_INSUFFICIENT,
        RejectionCode.LABEL_MISMATCH,
        RejectionCode.ANTI_AFFINITY,
        RejectionCode.NETWORK_UNAVAILABLE,
        RejectionCode.CONCURRENCY_EXHAUSTED,
    }


def test_gpu_vram_and_model_failures_are_independent_of_registry_io() -> None:
    node = _node()
    worker = _worker(node)
    no_gpu = _resources(vram=0)
    evaluation = _evaluate(
        node=node,
        worker=worker,
        requirements=JobRequirements(
            gpu="required",
            vram_min_bytes=1,
            model_ref="model:missing",
        ),
        available=no_gpu,
    )

    assert _codes(evaluation) == {
        RejectionCode.GPU_REQUIRED,
        RejectionCode.VRAM_INSUFFICIENT,
        RejectionCode.MODEL_UNAVAILABLE,
    }

    cpu_only = _evaluate(
        node=node,
        worker=worker,
        requirements=JobRequirements(gpu="forbidden"),
    )
    assert _codes(cpu_only) == {RejectionCode.GPU_REQUIRED}


def test_preference_score_has_explicit_additive_precedence() -> None:
    node = _node()
    worker = _worker(node)
    requirements = JobRequirements(
        preferred_worker_ids=(worker.worker_id,),
        preferred_node_ids=(node.node_id,),
        preferred_labels=("gpu",),
        locality_refs=("zone:a", "workspace:alpha"),
        model_ref="model:base",
        runtime="python",
    )

    assert score_candidate(worker=worker, node=node, requirements=requirements) == 1785


def test_selection_ignores_rejections_then_uses_score_and_stable_worker_id() -> None:
    worker_a = new_id("worker")
    worker_b = new_id("worker")
    node_id = new_id("node")
    evaluations = (
        CandidateEvaluation(worker_id=worker_a, node_id=node_id, accepted=True, score=10),
        CandidateEvaluation(worker_id=worker_b, node_id=node_id, accepted=True, score=20),
        CandidateEvaluation(
            worker_id=new_id("worker"),
            node_id=node_id,
            accepted=False,
            score=1000,
        ),
    )

    assert select_worker(evaluations) == worker_b

    tied = tuple(replace(item, score=20) for item in evaluations[:2])
    assert select_worker(tied) == min(worker_a, worker_b)
    assert select_worker(()) is None
