from __future__ import annotations

from ai_multi_agent_platform.application_distribution import (
    BuildSpecification,
    BuildTarget,
    PackageType,
    job_requirements_for_target,
)
from ai_multi_agent_platform.application_distribution.execution import APPLICATION_BUILD_ACTION
from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import (
    DeterministicScheduler,
    DistributedRegistry,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    RejectionCode,
    ResourceSnapshot,
    SchedulingDecision,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.domain import new_id


def _target(
    target_id: str,
    *,
    os_name: str,
    architecture: str,
    required_capabilities: tuple[str, ...] = (),
) -> BuildTarget:
    return BuildTarget(
        target_id=target_id,
        os_name=os_name,
        architecture=architecture,
        package_type=PackageType.ARCHIVE,
        output_path=f"dist/{target_id}.bin",
        required_capabilities=required_capabilities,
    )


def _job(specification: BuildSpecification, target: BuildTarget) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(correlation_id=f"issue-749-placement-{target.target_id}"),
        ),
        requirements=job_requirements_for_target(specification, target),
    )


def _register_worker(
    registry: DistributedRegistry,
    *,
    display_name: str,
    os_name: str = "linux",
    architecture: str = "x86_64",
    capability_refs: tuple[str, ...] = (APPLICATION_BUILD_ACTION,),
    supported_runtimes: tuple[str, ...] = (),
    trust_level: str = "standard",
    cpu_cores: float = 4.0,
    node_status: NodeStatus = NodeStatus.ONLINE,
    worker_status: WorkerStatus = WorkerStatus.HEALTHY,
    worker_draining: bool = False,
) -> tuple[str, str]:
    node_id = new_id("node")
    worker_id = new_id("worker")
    registry.register(
        RegistrationRequest(
            node=NodeRecord(
                node_id=node_id,
                display_name=display_name,
                os_name=os_name,
                architecture=architecture,
                supported_runtimes=supported_runtimes,
                trust_level=trust_level,
                status=node_status,
                resources=ResourceSnapshot(
                    cpu_cores_total=cpu_cores,
                    cpu_cores_available=cpu_cores,
                ),
            ),
            workers=(
                WorkerRecord(
                    worker_id=worker_id,
                    node_id=node_id,
                    capability_refs=capability_refs,
                    supported_runtimes=supported_runtimes,
                    status=worker_status,
                    draining=worker_draining,
                ),
            ),
            service_identity_ref=worker_id,
        )
    )
    return node_id, worker_id


def _reason_codes(decision: SchedulingDecision, worker_id: str) -> set[RejectionCode]:
    evaluation = next(item for item in decision.evaluations if item.worker_id == worker_id)
    return {reason.code for reason in evaluation.reasons}


def test_application_targets_use_canonical_os_placement_and_deterministic_tie_breaking() -> None:
    registry = DistributedRegistry()
    first_linux_node, first_linux = _register_worker(
        registry,
        display_name="linux-a",
        os_name="linux",
    )
    second_linux_node, second_linux = _register_worker(
        registry,
        display_name="linux-b",
        os_name="linux",
    )
    windows_node, windows_worker = _register_worker(
        registry,
        display_name="windows-a",
        os_name="windows",
    )
    del first_linux_node, second_linux_node, windows_node

    linux = _target("linux-x64", os_name="linux", architecture="x86_64")
    windows = _target("windows-x64", os_name="windows", architecture="x86_64")
    specification = BuildSpecification(
        command=("builder",),
        targets=(linux, windows),
    )
    scheduler = DeterministicScheduler(registry)

    linux_decision = scheduler.evaluate(_job(specification, linux))
    windows_decision = scheduler.evaluate(_job(specification, windows))

    assert linux_decision.selected_worker_id == min(first_linux, second_linux)
    assert windows_decision.selected_worker_id == windows_worker
    assert RejectionCode.OS_UNSUPPORTED in _reason_codes(linux_decision, windows_worker)
    assert RejectionCode.OS_UNSUPPORTED in _reason_codes(windows_decision, first_linux)
    assert RejectionCode.OS_UNSUPPORTED in _reason_codes(windows_decision, second_linux)


def test_application_placement_exposes_architecture_capability_resource_and_health_rejections() -> (
    None
):
    registry = DistributedRegistry()
    target = _target(
        "linux-x64",
        os_name="linux",
        architecture="x86_64",
        required_capabilities=("toolchain:python",),
    )
    specification = BuildSpecification(
        command=("builder",),
        targets=(target,),
        resource_hints={"cpu_cores": 2},
    )
    capabilities = (APPLICATION_BUILD_ACTION, "toolchain:python")

    _, eligible = _register_worker(
        registry,
        display_name="eligible",
        capability_refs=capabilities,
    )
    _, wrong_architecture = _register_worker(
        registry,
        display_name="wrong-architecture",
        architecture="arm64",
        capability_refs=capabilities,
    )
    _, missing_capability = _register_worker(
        registry,
        display_name="missing-capability",
    )
    _, insufficient_cpu = _register_worker(
        registry,
        display_name="insufficient-cpu",
        capability_refs=capabilities,
        cpu_cores=1.0,
    )
    _, offline = _register_worker(
        registry,
        display_name="offline",
        capability_refs=capabilities,
        worker_status=WorkerStatus.OFFLINE,
    )
    _, unhealthy = _register_worker(
        registry,
        display_name="unhealthy",
        capability_refs=capabilities,
        worker_status=WorkerStatus.UNHEALTHY,
    )
    _, draining = _register_worker(
        registry,
        display_name="draining",
        capability_refs=capabilities,
        worker_draining=True,
    )

    decision = DeterministicScheduler(registry).evaluate(_job(specification, target))

    assert decision.selected_worker_id == eligible
    assert RejectionCode.ARCHITECTURE_UNSUPPORTED in _reason_codes(decision, wrong_architecture)
    assert RejectionCode.CAPABILITY_UNSUPPORTED in _reason_codes(decision, missing_capability)
    assert RejectionCode.CPU_INSUFFICIENT in _reason_codes(decision, insufficient_cpu)
    assert RejectionCode.WORKER_OFFLINE in _reason_codes(decision, offline)
    assert RejectionCode.WORKER_UNHEALTHY in _reason_codes(decision, unhealthy)
    assert RejectionCode.WORKER_DRAINING in _reason_codes(decision, draining)


def test_application_placement_exposes_runtime_and_trust_policy_rejections() -> None:
    registry = DistributedRegistry()
    target = _target("linux-x64", os_name="linux", architecture="x86_64")
    specification = BuildSpecification(
        command=("builder",),
        targets=(target,),
        resource_hints={
            "runtime": "python3.13",
            "allowed_trust_levels": ["trusted"],
        },
    )

    _, eligible = _register_worker(
        registry,
        display_name="trusted-python",
        supported_runtimes=("python3.13",),
        trust_level="trusted",
    )
    _, missing_runtime = _register_worker(
        registry,
        display_name="trusted-no-python",
        trust_level="trusted",
    )
    _, insufficient_trust = _register_worker(
        registry,
        display_name="standard-python",
        supported_runtimes=("python3.13",),
        trust_level="standard",
    )

    decision = DeterministicScheduler(registry).evaluate(_job(specification, target))

    assert decision.selected_worker_id == eligible
    assert RejectionCode.RUNTIME_UNSUPPORTED in _reason_codes(decision, missing_runtime)
    assert RejectionCode.TRUST_INSUFFICIENT in _reason_codes(decision, insufficient_trust)


def test_unsupported_application_target_has_no_selected_worker_and_keeps_rejection_evidence() -> (
    None
):
    registry = DistributedRegistry()
    _, linux_worker = _register_worker(
        registry,
        display_name="linux-only",
        os_name="linux",
        architecture="x86_64",
    )
    unsupported = _target(
        "macos-arm64",
        os_name="macos",
        architecture="arm64",
    )
    specification = BuildSpecification(
        command=("builder",),
        targets=(unsupported,),
    )

    decision = DeterministicScheduler(registry).evaluate(_job(specification, unsupported))

    assert decision.selected_worker_id is None
    assert RejectionCode.OS_UNSUPPORTED in _reason_codes(decision, linux_worker)
    assert RejectionCode.ARCHITECTURE_UNSUPPORTED in _reason_codes(decision, linux_worker)
