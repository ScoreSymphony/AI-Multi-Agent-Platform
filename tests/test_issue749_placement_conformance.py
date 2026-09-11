from __future__ import annotations

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    BuildSpecification,
    BuildTarget,
    PackageType,
)
from ai_multi_agent_platform.application_distribution.placement import job_requirements_for_target
from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distributed import (
    DeterministicScheduler,
    DistributedRegistry,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.models import SchedulingDecision
from ai_multi_agent_platform.domain import new_id


def _resources(*, cpu: float = 8.0, ram: int = 16_000, storage: int = 100_000) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_cores_total=cpu,
        cpu_cores_available=cpu,
        ram_total_bytes=ram,
        ram_available_bytes=ram,
        storage_total_bytes=storage,
        storage_available_bytes=storage,
    )


def _target(*, os_name: str = "linux", architecture: str = "x86_64") -> BuildTarget:
    return BuildTarget(
        target_id=f"{os_name}-{architecture}",
        os_name=os_name,
        architecture=architecture,
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
        required_capabilities=("toolchain:python",),
    )


def _specification(*, resource_hints: dict[str, JsonValue] | None = None) -> BuildSpecification:
    return BuildSpecification(
        command=("python", "build.py"),
        targets=(_target(),),
        required_capabilities=("builder:archive",),
        resource_hints={} if resource_hints is None else resource_hints,
    )


def _job(specification: BuildSpecification, target: BuildTarget) -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id=f"issue-749:{task_id}"),
        ),
        requirements=job_requirements_for_target(specification, target),
    )


def _register(
    registry: DistributedRegistry,
    *,
    node_id: str,
    worker_id: str,
    os_name: str,
    architecture: str,
    capabilities: tuple[str, ...] = (
        APPLICATION_BUILD_ACTION,
        "builder:archive",
        "toolchain:python",
    ),
    runtimes: tuple[str, ...] = ("python3.13",),
    resources: ResourceSnapshot | None = None,
    worker_status: WorkerStatus = WorkerStatus.HEALTHY,
) -> None:
    registry.register(
        RegistrationRequest(
            node=NodeRecord(
                node_id=node_id,
                display_name=f"{os_name}-{architecture}",
                os_name=os_name,
                architecture=architecture,
                supported_runtimes=runtimes,
                resources=resources or _resources(),
            ),
            workers=(
                WorkerRecord(
                    worker_id=worker_id,
                    node_id=node_id,
                    capability_refs=capabilities,
                    supported_runtimes=runtimes,
                    status=worker_status,
                ),
            ),
            service_identity_ref=worker_id,
        )
    )


def _reason_codes(decision: SchedulingDecision, worker_id: str) -> set[str]:
    evaluation = next(item for item in decision.evaluations if item.worker_id == worker_id)
    return {reason.code.value for reason in evaluation.reasons}


def test_linux_target_selects_only_eligible_linux_worker_deterministically() -> None:
    registry = DistributedRegistry()
    linux_a = new_id("worker")
    linux_b = new_id("worker")
    windows = new_id("worker")
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=linux_a,
        os_name="linux",
        architecture="x86_64",
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=linux_b,
        os_name="linux",
        architecture="x86_64",
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=windows,
        os_name="windows",
        architecture="x86_64",
    )
    target = _target(os_name="linux", architecture="amd64")
    specification = _specification(resource_hints={"runtime": "python3.13"})

    decision = DeterministicScheduler(registry).evaluate(_job(specification, target))

    assert decision.selected_worker_id == min(linux_a, linux_b)
    assert "os_unsupported" in _reason_codes(decision, windows)


def test_windows_target_selects_windows_worker_and_rejects_architecture_mismatch() -> None:
    registry = DistributedRegistry()
    windows_x64 = new_id("worker")
    windows_arm64 = new_id("worker")
    linux_x64 = new_id("worker")
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=windows_x64,
        os_name="windows",
        architecture="x86_64",
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=windows_arm64,
        os_name="windows",
        architecture="arm64",
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=linux_x64,
        os_name="linux",
        architecture="x86_64",
    )
    target = _target(os_name="windows", architecture="amd64")
    specification = _specification(resource_hints={"runtime": "python3.13"})

    decision = DeterministicScheduler(registry).evaluate(_job(specification, target))

    assert decision.selected_worker_id == windows_x64
    assert "architecture_unsupported" in _reason_codes(decision, windows_arm64)
    assert "os_unsupported" in _reason_codes(decision, linux_x64)


def test_application_build_rejection_evidence_remains_canonical_and_explicit() -> None:
    registry = DistributedRegistry()
    missing_capability = new_id("worker")
    missing_runtime = new_id("worker")
    insufficient_resources = new_id("worker")
    unhealthy = new_id("worker")

    _register(
        registry,
        node_id=new_id("node"),
        worker_id=missing_capability,
        os_name="linux",
        architecture="x86_64",
        capabilities=(APPLICATION_BUILD_ACTION, "toolchain:python"),
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=missing_runtime,
        os_name="linux",
        architecture="x86_64",
        runtimes=(),
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=insufficient_resources,
        os_name="linux",
        architecture="x86_64",
        resources=_resources(cpu=1.0, ram=512, storage=1024),
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=unhealthy,
        os_name="linux",
        architecture="x86_64",
        worker_status=WorkerStatus.UNHEALTHY,
    )

    target = _target()
    specification = _specification(
        resource_hints={
            "runtime": "python3.13",
            "cpu_cores": 2,
            "ram_bytes": 1024,
            "storage_bytes": 4096,
        }
    )
    decision = DeterministicScheduler(registry).evaluate(_job(specification, target))

    assert decision.selected_worker_id is None
    assert "capability_unsupported" in _reason_codes(decision, missing_capability)
    assert "runtime_unsupported" in _reason_codes(decision, missing_runtime)
    resource_reasons = _reason_codes(decision, insufficient_resources)
    assert {"cpu_insufficient", "ram_insufficient", "storage_insufficient"} <= resource_reasons
    assert "worker_unhealthy" in _reason_codes(decision, unhealthy)


def test_draining_application_builder_is_excluded_without_application_specific_policy() -> None:
    registry = DistributedRegistry()
    draining = new_id("worker")
    eligible = new_id("worker")
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=draining,
        os_name="linux",
        architecture="x86_64",
    )
    _register(
        registry,
        node_id=new_id("node"),
        worker_id=eligible,
        os_name="linux",
        architecture="x86_64",
    )
    registry.set_worker_draining(draining, draining=True)

    decision = DeterministicScheduler(registry).evaluate(_job(_specification(), _target()))

    assert decision.selected_worker_id == eligible
    assert "worker_draining" in _reason_codes(decision, draining)
