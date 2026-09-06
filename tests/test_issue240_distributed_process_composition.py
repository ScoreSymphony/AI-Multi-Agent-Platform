from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
)
from ai_multi_agent_platform.deployment.distributed_worker import (
    DistributedWorkerProcess,
    DistributedWorkerProcessConfig,
)
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.transport import (
    TransportWorkerDispatcher,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.distributed.worker_protocol import (
    WorkerHeartbeatRequest,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.distributed.worker_protocol_http import WorkerProtocolHTTPClient
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
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
from ai_multi_agent_platform.workspaces import Workspace, WorkspaceProvider


class _ProtocolClient:
    def __init__(self) -> None:
        self.registrations: list[RegistrationRequest] = []
        self.heartbeats: list[WorkerHeartbeatRequest] = []
        self.deregistrations: list[tuple[str, str]] = []

    async def register(self, request: RegistrationRequest) -> object:
        self.registrations.append(request)
        return object()

    async def heartbeat(self, request: WorkerHeartbeatRequest) -> object:
        self.heartbeats.append(request)
        return object()

    async def deregister_worker(self, worker_id: str, node_id: str) -> None:
        self.deregistrations.append((worker_id, node_id))


def _registration() -> RegistrationRequest:
    node_id = new_id("node")
    worker_id = new_id("worker")
    resources = ResourceSnapshot(
        cpu_cores_total=4,
        cpu_cores_available=4,
        ram_total_bytes=8_000_000,
        ram_available_bytes=8_000_000,
        storage_total_bytes=50_000_000,
        storage_available_bytes=50_000_000,
    )
    return RegistrationRequest(
        node=NodeRecord(
            node_id=node_id,
            display_name="issue-240-process-node",
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


def _job() -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(
                correlation_id="issue-240-process-dispatch",
                owner_type="service",
                owner_id="service:issue-240",
                project_id=new_id("project"),
            ),
            input={"plan_ref": "distributed worker echo"},
        ),
        requirements=JobRequirements(
            executor_type="reference",
            capability_refs=("execution:general",),
            runtime="python",
        ),
        artifact_refs=(new_id("artifact"),),
    )


def test_worker_process_registers_heartbeats_dispatches_and_deregisters(tmp_path: Path) -> None:
    async def scenario() -> None:
        registration = _registration()
        worker_id = registration.workers[0].worker_id
        protocol = _ProtocolClient()
        transport = InProcessMessageTransport(provider_id="issue-240-worker-process")
        process = DistributedWorkerProcess(
            DistributedWorkerProcessConfig(
                registration=registration,
                worker_id=worker_id,
                workspace_root=(tmp_path / "worker").resolve(),
                heartbeat_interval_seconds=0.01,
            ),
            protocol=cast(WorkerProtocolHTTPClient, protocol),
            transport=transport,
        )
        process_task = asyncio.create_task(process.run())
        try:
            for _ in range(100):
                if protocol.heartbeats:
                    break
                await asyncio.sleep(0.005)
            assert protocol.registrations == [registration]
            assert protocol.heartbeats
            assert protocol.heartbeats[0].service_identity_ref == worker_id

            dispatcher = TransportWorkerDispatcher(worker_id, transport)
            job = _job()
            handle = await dispatcher.dispatch(job)
            assert handle.run_id == job.execution.run_id
            snapshot = await dispatcher.get(job.worker_job_id)
            assert snapshot.status is RunStatus.SUCCEEDED
            result = await dispatcher.result(job.worker_job_id)
            assert result is not None
            assert result.artifact_refs == job.artifact_refs
        finally:
            process.stop()
            await process_task
            await transport.close(graceful=False)

        assert protocol.deregistrations == [(worker_id, registration.node.node_id)]

    asyncio.run(scenario())


def test_authenticated_registration_attaches_transport_dispatcher_to_runtime(tmp_path: Path) -> None:
    async def scenario() -> None:
        registration = _registration()
        worker = registration.workers[0]
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
        credential = authentication.create_worker_credential(worker.worker_id, scope=scope)
        authorization = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=worker.worker_id,
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
        runtime = DistributedRuntime(DistributedRegistry())
        transport = InProcessMessageTransport(provider_id="issue-240-control-process")
        service = DeploymentWorkerProtocolService(
            runtime,
            authentication=authentication,
            authorization=authorization,
            transport=transport,
            workspaces=cast(WorkspaceProvider, object()),
            files=cast(FileProvider, object()),
            context_resolver=lambda workspace: _unreachable_context(workspace),
        )

        executor_root = (tmp_path / "remote-endpoint").resolve()
        ExecutorLifecycleBackend.ensure_workspace(executor_root, "reference")
        endpoint = WorkerTransportEndpoint(
            LocalWorker(
                worker.worker_id,
                ExecutorLifecycleBackend(ReferenceExecutor(executor_root), workspace="reference"),
            ),
            transport,
        )
        endpoint_task = asyncio.create_task(endpoint.serve())
        try:
            now = datetime.now(UTC)
            await service.register(
                registration,
                WorkerRequestCredentials(
                    token=credential.secret,
                    nonce="issue-240-control-registration",
                    issued_at=now,
                    request_id="issue-240-register",
                    correlation_id="issue-240-register",
                ),
                now=now,
            )
            job = _job()
            record = await runtime.dispatch(job)
            assert record.worker_id == worker.worker_id
            assert record.handle is not None
            assert record.handle.run_id == job.execution.run_id
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def _unreachable_context(workspace: Workspace) -> object:
    raise AssertionError(f"Workspace resolver should not be called for this test: {workspace.id}")
