from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    OperationContext,
)
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
)
from ai_multi_agent_platform.deployment.worker_presence import WorkerPresenceEndpoint
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    JobRequirements,
    JsonDistributedStateStore,
    LocalWorker,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.scheduler import NoEligibleWorkerError
from ai_multi_agent_platform.distributed.transport import WorkerTransportEndpoint
from ai_multi_agent_platform.distributed.worker_protocol import (
    WorkerHeartbeatRequest,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
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
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


class _RunningBackend:
    """Worker-side backend that remains live while its Control Plane is reconstructed."""

    def __init__(self) -> None:
        self.run_ids: set[str] = set()

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self.run_ids.add(request.run_id)
        return ExecutionHandle(run_id=request.run_id, backend_ref="issue707-running-backend")

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        if run_id not in self.run_ids:
            raise RuntimeError("unknown worker-side Run")
        return ExecutionSnapshot(run_id=run_id, status=RunStatus.RUNNING)

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        self.run_ids.discard(run_id)
        return ExecutionSnapshot(run_id=run_id, status=RunStatus.CANCELLED)


def _registration() -> RegistrationRequest:
    node_id = new_id("node")
    worker_id = new_id("worker")
    resources = ResourceSnapshot(
        cpu_cores_total=4,
        cpu_cores_available=4,
        ram_total_bytes=16_000_000,
        ram_available_bytes=16_000_000,
        storage_total_bytes=100_000_000,
        storage_available_bytes=100_000_000,
    )
    return RegistrationRequest(
        node=NodeRecord(
            node_id=node_id,
            display_name="issue-707-restart-node",
            resources=resources,
            supported_runtimes=("python",),
            capability_refs=("execution:general",),
        ),
        workers=(
            WorkerRecord(
                worker_id=worker_id,
                node_id=node_id,
                supported_executors=("reference",),
                supported_runtimes=("python",),
                capability_refs=("execution:general",),
            ),
        ),
        service_identity_ref=worker_id,
    )


def _worker_security(
    worker_id: str,
) -> tuple[
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    str,
]:
    authentication = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )
    scope = CredentialScope(
        actions=frozenset(
            {
                AuthorizationAction.CREATE,
                AuthorizationAction.MODIFY,
                AuthorizationAction.DELETE,
            }
        ),
        resource_types=frozenset({ResourceType.NODE, ResourceType.WORKER}),
    )
    credential = authentication.create_worker_credential(worker_id, scope=scope)
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=worker_id,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.CREATE,
                        AuthorizationAction.MODIFY,
                        AuthorizationAction.DELETE,
                    }
                ),
                resource_types=frozenset({ResourceType.NODE, ResourceType.WORKER}),
            ),
        )
    )
    return authentication, authorization, credential.secret


def _credentials(secret: str, nonce: str) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=datetime.now(UTC),
        request_id=nonce,
        correlation_id=nonce,
    )


def _heartbeat(registration: RegistrationRequest, worker_id: str) -> WorkerHeartbeatRequest:
    return WorkerHeartbeatRequest(
        heartbeat=Heartbeat(
            node_id=registration.node.node_id,
            sequence=1,
            resources=registration.node.resources,
            node_status=NodeStatus.ONLINE,
            workers=registration.workers,
        ),
        service_identity_ref=worker_id,
    )


def _job(correlation_id: str) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(correlation_id=correlation_id),
        ),
        requirements=JobRequirements(executor_type="reference"),
    )


def test_persisted_remote_run_is_reconciled_before_worker_http_reregistration(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registration = _registration()
        worker_id = registration.workers[0].worker_id
        authentication, authorization, secret = _worker_security(worker_id)
        transport = InProcessMessageTransport(provider_id="issue-707-control-restart")
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        state_path = tmp_path / "distributed-runtime-state.json"
        backend = _RunningBackend()
        worker = LocalWorker(worker_id, backend)
        transport_task = asyncio.create_task(WorkerTransportEndpoint(worker, transport).serve())
        presence_task = asyncio.create_task(WorkerPresenceEndpoint(worker_id, transport).serve())

        try:
            first_runtime = DistributedRuntime(
                DistributedRegistry(),
                state_store=JsonDistributedStateStore(state_path),
            )
            first_service = DeploymentWorkerProtocolService(
                first_runtime,
                authentication=authentication,
                authorization=authorization,
                transport=transport,
                workspaces=workspaces,
                files=files,
                context_resolver=lambda _workspace: (_ for _ in ()).throw(
                    AssertionError("workspace context must not be used")
                ),
                presence_timeout_seconds=1.0,
            )
            await first_service.register(
                registration,
                _credentials(secret, "issue707-register-before-restart"),
            )

            job = WorkerJobRequest(
                execution=ExecutionRequest(
                    run_id=new_id("run"),
                    subject_type="task",
                    subject_id=new_id("task"),
                    context=OperationContext(
                        correlation_id="issue707-inflight-before-restart",
                        owner_type="service",
                        owner_id="service:issue707",
                        project_id=new_id("project"),
                    ),
                    input={"scenario": "control-plane-restart"},
                ),
                requirements=JobRequirements(
                    executor_type="reference",
                    capability_refs=("execution:general",),
                    runtime="python",
                ),
            )
            dispatched = await first_runtime.dispatch(job)
            assert dispatched.state is DispatchState.DISPATCHED
            assert state_path.is_file()

            # Simulate only the Control Plane process disappearing. The Worker transport and
            # presence endpoints stay alive, while a brand-new runtime reconstructs durable state.
            restarted = DistributedRuntime(DistributedRegistry())
            assert restarted.configure_state_store(JsonDistributedStateStore(state_path)) is True
            assert restarted.registry.get_worker(worker_id).status is WorkerStatus.OFFLINE

            restarted_service = DeploymentWorkerProtocolService(
                restarted,
                authentication=authentication,
                authorization=authorization,
                transport=transport,
                workspaces=workspaces,
                files=files,
                context_resolver=lambda _workspace: (_ for _ in ()).throw(
                    AssertionError("workspace context must not be used")
                ),
                presence_timeout_seconds=1.0,
            )
            restored_ids = await restarted_service.restore_reachable_persisted_workers()

            assert restored_ids == (worker_id,)
            recovery_worker = restarted.registry.get_worker(worker_id)
            assert recovery_worker.status is WorkerStatus.DEGRADED
            assert recovery_worker.draining is registration.workers[0].draining
            assert (
                restarted.registry.get_node(registration.node.node_id).draining
                is registration.node.draining
            )

            reconciled = await restarted.reconcile()
            assert len(reconciled) == 1
            assert reconciled[0].state is DispatchState.RUNNING
            assert reconciled[0].snapshot is not None
            assert reconciled[0].snapshot.status is RunStatus.RUNNING
            assert reconciled[0].last_error is None

            # Positive presence evidence is enough to inspect already-owned work. Degraded health
            # still rejects new scheduling until a normal authenticated Worker heartbeat arrives.
            new_job = _job("issue707-no-new-work-during-recovery")
            with pytest.raises(NoEligibleWorkerError):
                await restarted.dispatch(new_job)

            await restarted_service.heartbeat(
                _heartbeat(registration, worker_id),
                _credentials(secret, "issue707-heartbeat-after-restart"),
            )
            refreshed_worker = restarted.registry.get_worker(worker_id)
            assert refreshed_worker.status is WorkerStatus.HEALTHY
            assert refreshed_worker.draining is registration.workers[0].draining
            resumed = await restarted.dispatch(new_job)
            assert resumed.worker_id == worker_id
            assert resumed.state is DispatchState.DISPATCHED
        finally:
            for task in (transport_task, presence_task):
                task.cancel()
            for task in (transport_task, presence_task):
                with suppress(asyncio.CancelledError):
                    await task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_late_heartbeat_attaches_worker_that_missed_startup_presence_probe(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registration = _registration()
        worker_id = registration.workers[0].worker_id
        authentication, authorization, secret = _worker_security(worker_id)
        transport = InProcessMessageTransport(provider_id="issue-707-late-heartbeat")
        files = LocalFileProvider(tmp_path / "late-objects", tmp_path / "late-files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "late-workspaces", files)
        state_path = tmp_path / "late-distributed-runtime-state.json"

        # Persist a known Worker, then reconstruct only the Control Plane while no Worker endpoint
        # is reachable. The one-shot startup presence probe therefore cannot attach a dispatcher.
        first_runtime = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        first_service = DeploymentWorkerProtocolService(
            first_runtime,
            authentication=authentication,
            authorization=authorization,
            transport=transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: (_ for _ in ()).throw(
                AssertionError("workspace context must not be used")
            ),
            presence_timeout_seconds=None,
        )
        await first_service.register(
            registration,
            _credentials(secret, "issue707-register-before-late-worker"),
        )

        restarted = DistributedRuntime(DistributedRegistry())
        assert restarted.configure_state_store(JsonDistributedStateStore(state_path)) is True
        restarted_service = DeploymentWorkerProtocolService(
            restarted,
            authentication=authentication,
            authorization=authorization,
            transport=transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: (_ for _ in ()).throw(
                AssertionError("workspace context must not be used")
            ),
            presence_timeout_seconds=0.05,
        )
        assert await restarted_service.restore_reachable_persisted_workers() == ()
        assert restarted.registry.get_worker(worker_id).status is WorkerStatus.OFFLINE

        backend = _RunningBackend()
        worker = LocalWorker(worker_id, backend)
        transport_task = asyncio.create_task(WorkerTransportEndpoint(worker, transport).serve())
        presence_task = asyncio.create_task(WorkerPresenceEndpoint(worker_id, transport).serve())
        try:
            # A later authenticated heartbeat is accepted against the restored canonical identity.
            # Its current positive presence proof must therefore also attach the missing dispatcher.
            await restarted_service.heartbeat(
                _heartbeat(registration, worker_id),
                _credentials(secret, "issue707-late-heartbeat-after-startup-probe"),
            )
            refreshed_worker = restarted.registry.get_worker(worker_id)
            assert refreshed_worker.status is WorkerStatus.HEALTHY
            assert refreshed_worker.draining is registration.workers[0].draining

            dispatched = await restarted.dispatch(_job("issue707-dispatch-after-late-heartbeat"))
            assert dispatched.worker_id == worker_id
            assert dispatched.state is DispatchState.DISPATCHED
        finally:
            for task in (transport_task, presence_task):
                task.cancel()
            for task in (transport_task, presence_task):
                with suppress(asyncio.CancelledError):
                    await task
            await transport.close(graceful=False)

    asyncio.run(scenario())
