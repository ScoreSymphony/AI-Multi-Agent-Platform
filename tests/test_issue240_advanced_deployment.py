from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PureWindowsPath

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.deployment import (
    AdvancedDeploymentProfile,
    AdvancedDeploymentProfileError,
    load_advanced_deployment_profile,
    load_single_node_config,
    parse_advanced_deployment_profile,
)
from ai_multi_agent_platform.distributed import (
    DeterministicScheduler,
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    JobRequirements,
    LocalWorker,
    NoEligibleWorkerError,
    RejectionCode,
    TransportWorkerDispatcher,
    WorkerHeartbeatRequest,
    WorkerJobRequest,
    WorkerProtocolService,
    WorkerRequestCredentials,
    WorkerTransportCodec,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    CredentialScope,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    ScryptPasswordHasher,
)
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend

PROFILES = Path("deploy/distributed/profiles")
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _profile(name: str) -> AdvancedDeploymentProfile:
    return load_advanced_deployment_profile(PROFILES / name)


def _job(*, requirements: JobRequirements | None = None) -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id=f"issue-240:{task_id}"),
        ),
        requirements=requirements or JobRequirements(executor_type="reference"),
        workspace_ref=new_id("workspace"),
        snapshot_ref=new_id("workspace_snapshot"),
        artifact_refs=(new_id("artifact"),),
    )


def _registered_runtime(profile_name: str) -> DistributedRuntime:
    runtime = DistributedRuntime(DistributedRegistry())
    for request in _profile(profile_name).registration_requests:
        runtime.register(request, now=NOW)
    return runtime


def _worker_security(reporter_id: str):
    authentication = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )
    actions = frozenset(
        {
            AuthorizationAction.CREATE,
            AuthorizationAction.MODIFY,
            AuthorizationAction.DELETE,
        }
    )
    resource_types = frozenset({ResourceType.NODE, ResourceType.WORKER})
    credential = authentication.create_worker_credential(
        reporter_id,
        scope=CredentialScope(actions=actions, resource_types=resource_types),
        now=NOW,
    )
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=reporter_id,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=actions,
                resource_types=resource_types,
            ),
        )
    )
    return authentication, authorization, credential.secret


def _credentials(secret: str, nonce: str, *, when: datetime = NOW) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=when,
        tls_peer_ref="spiffe://example/issue-240-worker",
        request_id=f"issue-240-{nonce}",
        correlation_id=f"issue-240-{nonce}",
    )


def test_required_reference_profiles_are_valid_and_keep_identity_separate() -> None:
    names = (
        "multi-local-workers.json",
        "remote-worker.json",
        "cpu-control-gpu-worker.json",
        "heterogeneous-three-node.json",
    )

    profiles = tuple(_profile(name) for name in names)

    assert [profile.profile_id for profile in profiles] == [
        "multi-local-workers",
        "control-plane-plus-remote-worker",
        "cpu-control-plus-accelerator-worker",
        "heterogeneous-three-node",
    ]
    for profile in profiles:
        canonical_ids = {item.node.node_id for item in profile.nodes} | {
            worker.worker_id for item in profile.nodes for worker in item.workers
        }
        assert not canonical_ids.intersection(item.binding.host_ref for item in profile.nodes)


def test_profile_rejects_plaintext_credential_material() -> None:
    raw = json.loads((PROFILES / "remote-worker.json").read_text(encoding="utf-8"))
    raw["nodes"][0]["deployment"]["token"] = "plaintext-must-never-be-valid-config"

    with pytest.raises(AdvancedDeploymentProfileError, match="plaintext secret field"):
        parse_advanced_deployment_profile(raw)


def test_multi_local_workers_register_and_dispatch_through_canonical_scheduler() -> None:
    profile = _profile("multi-local-workers.json")
    runtime = _registered_runtime("multi-local-workers.json")
    workers = profile.nodes[0].workers
    for worker in workers:
        runtime.attach_worker(LocalWorker(worker.worker_id, FakeLifecycleBackend()))

    async def scenario() -> None:
        records = [await runtime.dispatch(_job(), now=NOW) for _ in range(3)]
        expected_first = min(worker.worker_id for worker in workers)
        expected_second = max(worker.worker_id for worker in workers)

        assert records[0].worker_id == expected_first
        assert records[1].worker_id == expected_first
        assert records[2].worker_id == expected_second
        assert all(record.state is DispatchState.DISPATCHED for record in records)

    assert len(runtime.registry.list_nodes()) == 1
    assert len(runtime.registry.list_workers()) == 2
    asyncio.run(scenario())


def test_remote_profile_requires_tls_and_canonical_secret_reference() -> None:
    remote = _profile("remote-worker.json").nodes[0]

    assert remote.binding.connection_mode == "remote"
    assert remote.binding.tls_required is True
    assert remote.binding.credential_reference is not None
    assert remote.binding.credential_reference.scope == "worker_protocol"
    assert remote.registration_request().service_identity_ref == remote.reporter_worker_id


def test_authenticated_remote_registration_reregistration_and_heartbeat() -> None:
    remote = _profile("remote-worker.json").nodes[0]
    reporter_id = remote.reporter_worker_id
    assert reporter_id is not None
    authentication, authorization, secret = _worker_security(reporter_id)
    runtime = DistributedRuntime(DistributedRegistry())
    service = WorkerProtocolService(
        runtime,
        authentication=authentication,
        authorization=authorization,
    )

    async def scenario() -> None:
        first = await service.register(
            remote.registration_request(),
            _credentials(secret, "register-1"),
            now=NOW,
        )
        second = await service.register(
            remote.registration_request(),
            _credentials(secret, "register-2", when=NOW + timedelta(seconds=1)),
            now=NOW + timedelta(seconds=1),
        )
        await service.heartbeat(
            WorkerHeartbeatRequest(
                heartbeat=Heartbeat(
                    node_id=remote.node.node_id,
                    sequence=1,
                    observed_at=NOW + timedelta(seconds=2),
                    resources=remote.node.resources,
                    workers=remote.workers,
                ),
                service_identity_ref=reporter_id,
            ),
            _credentials(secret, "heartbeat-1", when=NOW + timedelta(seconds=2)),
            now=NOW + timedelta(seconds=2),
        )

        assert first.node_id == second.node_id == remote.node.node_id
        assert first.reporter_worker_id == second.reporter_worker_id == reporter_id
        assert len(runtime.registry.list_nodes()) == 1
        assert len(runtime.registry.list_workers()) == 1
        heartbeat_at = runtime.registry.get_worker(reporter_id).last_heartbeat_at
        assert heartbeat_at == NOW + timedelta(seconds=2)

    asyncio.run(scenario())


def test_authenticated_remote_profile_dispatches_through_transport_contract() -> None:
    remote = _profile("remote-worker.json").nodes[0]
    reporter_id = remote.reporter_worker_id
    assert reporter_id is not None
    worker = remote.workers[0]
    authentication, authorization, secret = _worker_security(reporter_id)
    runtime = DistributedRuntime(DistributedRegistry())
    service = WorkerProtocolService(
        runtime,
        authentication=authentication,
        authorization=authorization,
    )

    async def scenario() -> None:
        await service.register(
            remote.registration_request(),
            _credentials(secret, "dispatch-register"),
            now=NOW,
        )
        transport = InProcessMessageTransport(provider_id="issue-240-network-adapter-fixture")
        endpoint = WorkerTransportEndpoint(
            LocalWorker(worker.worker_id, FakeLifecycleBackend()),
            transport,
        )
        endpoint_task = asyncio.create_task(endpoint.serve())
        await asyncio.sleep(0)
        runtime.attach_worker(TransportWorkerDispatcher(worker.worker_id, transport))
        job = _job()
        try:
            record = await runtime.dispatch(job, now=NOW)
            assert record.worker_id == worker.worker_id
            assert record.job.execution.run_id == job.execution.run_id
            assert record.job.workspace_ref == job.workspace_ref
            assert record.job.artifact_refs == job.artifact_refs
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_worker_loss_marks_active_remote_job_lost_and_reregistration_recovers() -> None:
    remote = _profile("remote-worker.json").nodes[0]
    registry = DistributedRegistry(heartbeat_timeout=timedelta(seconds=30))
    runtime = DistributedRuntime(registry)
    runtime.register(remote.registration_request(), now=NOW)
    lifecycle = FakeLifecycleBackend()
    runtime.attach_worker(LocalWorker(remote.workers[0].worker_id, lifecycle))

    async def scenario() -> None:
        job = _job()
        dispatched = await runtime.dispatch(job, now=NOW)
        assert dispatched.state is DispatchState.DISPATCHED

        lost = await runtime.reconcile(now=NOW + timedelta(seconds=31))
        assert lost[0].state is DispatchState.LOST
        assert lost[0].last_error == "worker_unreachable"

        runtime.register(remote.registration_request(), now=NOW + timedelta(seconds=32))
        recovered = await runtime.reconcile(now=NOW + timedelta(seconds=33))
        assert recovered[0].state is DispatchState.DISPATCHED
        assert recovered[0].worker_id == remote.workers[0].worker_id

    asyncio.run(scenario())


def test_worker_loss_expires_liveness_and_blocks_new_placement() -> None:
    profile = _profile("remote-worker.json")
    registry = DistributedRegistry(heartbeat_timeout=timedelta(seconds=30))
    registry.register(profile.registration_requests[0], now=NOW)

    expired = registry.expire_heartbeats(now=NOW + timedelta(seconds=31))

    assert expired == (profile.nodes[0].node.node_id,)
    with pytest.raises(NoEligibleWorkerError):
        DeterministicScheduler(registry).schedule(_job(), now=NOW + timedelta(seconds=31))


def test_cpu_control_plus_accelerator_worker_places_gpu_required_job_remotely() -> None:
    profile = _profile("cpu-control-gpu-worker.json")
    runtime = _registered_runtime("cpu-control-gpu-worker.json")
    accelerated = profile.nodes[1].workers[0]
    job = _job(
        requirements=JobRequirements(
            executor_type="reference",
            gpu="required",
            vram_min_bytes=12 * 1024**3,
            model_ref="model:local-large",
        )
    )

    decision = DeterministicScheduler(runtime.registry).evaluate(job)

    assert decision.selected_worker_id == accelerated.worker_id
    cpu_worker_id = profile.nodes[0].workers[0].worker_id
    cpu_evaluation = next(
        item for item in decision.evaluations if item.worker_id == cpu_worker_id
    )
    rejection_codes = {reason.code for reason in cpu_evaluation.reasons}
    assert RejectionCode.GPU_REQUIRED in rejection_codes
    assert RejectionCode.MODEL_UNAVAILABLE in rejection_codes


def test_draining_and_maintenance_exclude_accelerator_worker() -> None:
    profile = _profile("cpu-control-gpu-worker.json")
    accelerated_node = profile.nodes[1]
    requirements = JobRequirements(
        executor_type="reference",
        gpu="required",
        vram_min_bytes=1,
    )

    draining_runtime = _registered_runtime("cpu-control-gpu-worker.json")
    draining_runtime.set_worker_draining(accelerated_node.workers[0].worker_id, draining=True)
    with pytest.raises(NoEligibleWorkerError):
        DeterministicScheduler(draining_runtime.registry).schedule(
            _job(requirements=requirements),
            now=NOW,
        )

    maintenance_runtime = _registered_runtime("cpu-control-gpu-worker.json")
    maintenance_runtime.set_node_maintenance(accelerated_node.node.node_id, maintenance=True)
    with pytest.raises(NoEligibleWorkerError):
        DeterministicScheduler(maintenance_runtime.registry).schedule(
            _job(requirements=requirements),
            now=NOW,
        )


def test_heterogeneous_selection_uses_capability_locality_and_os_not_host_role() -> None:
    profile = _profile("heterogeneous-three-node.json")
    runtime = _registered_runtime("heterogeneous-three-node.json")
    data_local = profile.nodes[2].workers[0]

    locality_decision = DeterministicScheduler(runtime.registry).evaluate(
        _job(
            requirements=JobRequirements(
                executor_type="reference",
                capability_refs=("workspace:data-local",),
                locality_refs=("workspace:archive",),
            )
        )
    )
    windows_decision = DeterministicScheduler(runtime.registry).evaluate(
        _job(requirements=JobRequirements(executor_type="reference", os_name="windows"))
    )

    assert locality_decision.selected_worker_id == data_local.worker_id
    assert windows_decision.selected_worker_id == profile.nodes[1].workers[0].worker_id
    assert profile.nodes[2].node.architecture == "aarch64"
    assert isinstance(profile.nodes[1].binding.workspace_root, PureWindowsPath)


def test_workspace_root_is_local_only_and_refs_survive_transport_codec() -> None:
    remote = _profile("remote-worker.json").nodes[0]
    job = _job()
    assert job.workspace_ref is not None

    local_path = remote.binding.workspace_path(job.workspace_ref)
    encoded = WorkerTransportCodec.encode_job(job)
    decoded = WorkerTransportCodec.decode_job(encoded)

    assert local_path == remote.binding.workspace_root / job.workspace_ref
    assert decoded.workspace_ref == job.workspace_ref
    assert decoded.snapshot_ref == job.snapshot_ref
    assert decoded.artifact_refs == job.artifact_refs
    assert str(remote.binding.workspace_root) not in repr(encoded)


def test_optional_model_service_degradation_does_not_break_general_execution() -> None:
    profile = _profile("cpu-control-gpu-worker.json")
    runtime = _registered_runtime("cpu-control-gpu-worker.json")
    accelerated = profile.nodes[1]
    degraded_request = replace(
        accelerated.registration_request(),
        node=replace(accelerated.node, model_refs=()),
        workers=tuple(replace(worker, model_refs=()) for worker in accelerated.workers),
    )
    runtime.register(degraded_request, now=NOW + timedelta(seconds=1))

    model_decision = DeterministicScheduler(runtime.registry).evaluate(
        _job(
            requirements=JobRequirements(
                executor_type="reference",
                gpu="required",
                model_ref="model:local-large",
            )
        )
    )
    general_decision = DeterministicScheduler(runtime.registry).evaluate(_job())

    assert model_decision.selected_worker_id is None
    assert general_decision.selected_worker_id is not None


def test_optional_services_can_all_be_absent_without_invalidating_workers() -> None:
    profile = _profile("multi-local-workers.json")

    assert profile.optional_services
    assert all(not service.enabled for service in profile.optional_services)
    assert len(profile.registration_requests) == 1
    assert len(profile.registration_requests[0].workers) == 2


def test_single_node_baseline_does_not_depend_on_advanced_profiles() -> None:
    config = load_single_node_config({})

    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.secure_cookie is True
