from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import LocalFileProvider
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
    NodeRecord,
    NoEligibleWorkerError,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
    WorkerStatus,
)
from ai_multi_agent_platform.distributed.worker_protocol import WorkerRequestCredentials
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.messaging import TcpMessageBroker, TcpMessageTransport
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

_TRANSPORT_KEY = "issue-240-network-loss-restart-key"


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
            display_name="issue-240-restart-tcp-node",
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
                concurrency_limit=1,
            ),
        ),
        service_identity_ref=worker_id,
    )


def _security(worker_id: str):
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


def _credentials(secret: str, nonce: str, when: datetime) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=when,
        request_id=nonce,
        correlation_id=nonce,
    )


def _job(project_id: str, marker: str) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(
                correlation_id=f"issue-240-network-restart:{marker}",
                owner_type="service",
                owner_id="service:issue-240",
                project_id=project_id,
            ),
            input={"plan_ref": marker},
        ),
        requirements=JobRequirements(
            executor_type="reference",
            capability_refs=("execution:general",),
            runtime="python",
        ),
    )


def test_tcp_loss_expires_worker_and_same_identity_reregisters_after_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registration = _registration()
        worker_id = registration.workers[0].worker_id
        authentication, authorization, secret = _security(worker_id)
        runtime = DistributedRuntime(DistributedRegistry())

        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
        broker = TcpMessageBroker(authentication_key=_TRANSPORT_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=_TRANSPORT_KEY,
            provider_id="issue-240-network-restart-control",
        )
        service = DeploymentWorkerProtocolService(
            runtime,
            authentication=authentication,
            authorization=authorization,
            transport=control_transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: (_ for _ in ()).throw(
                AssertionError("workspace context must not be used by workspace-free jobs")
            ),
        )

        async def start_worker(
            instance: str,
        ) -> tuple[
            DistributedWorkerProcess,
            asyncio.Task[None],
            TcpMessageTransport,
        ]:
            transport = TcpMessageTransport(
                broker.host,
                broker.port,
                authentication_key=_TRANSPORT_KEY,
                provider_id=f"issue-240-network-restart-worker:{instance}",
            )
            process = DistributedWorkerProcess(
                DistributedWorkerProcessConfig(
                    registration=registration,
                    worker_id=worker_id,
                    workspace_root=(tmp_path / f"worker-{instance}").resolve(),
                    reporting=False,
                ),
                protocol=None,
                transport=transport,
            )
            task = asyncio.create_task(process.run())
            await asyncio.sleep(0.05)
            return process, task, transport

        first_process, first_task, first_transport = await start_worker("first")
        second_process: DistributedWorkerProcess | None = None
        second_task: asyncio.Task[None] | None = None
        second_transport: TcpMessageTransport | None = None
        try:
            registered_at = datetime.now(UTC)
            await service.register(
                registration,
                _credentials(secret, "issue-240-network-register-1", registered_at),
                now=registered_at,
            )

            project_id = new_id("project")
            before_loss = _job(project_id, "before-loss")
            first_record = await runtime.dispatch(before_loss, now=registered_at)
            assert first_record.worker_id == worker_id
            first_result = await runtime.result(before_loss.worker_job_id)
            assert first_result is not None
            assert first_result.worker_id == worker_id

            # Drop the Worker-side process/transport without protocol deregistration. From the
            # Control Plane this is indistinguishable from a process/network loss.
            first_process.stop()
            await first_task
            await first_transport.close(graceful=False)

            expired_at = registered_at + timedelta(seconds=31)
            await runtime.reconcile(now=expired_at)
            assert runtime.registry.get_worker(worker_id).status is WorkerStatus.OFFLINE
            with pytest.raises(NoEligibleWorkerError):
                await runtime.dispatch(_job(project_id, "while-offline"), now=expired_at)

            second_process, second_task, second_transport = await start_worker("second")
            reregistered_at = expired_at + timedelta(seconds=1)
            await service.register(
                registration,
                _credentials(secret, "issue-240-network-register-2", reregistered_at),
                now=reregistered_at,
            )
            assert runtime.registry.get_worker(worker_id).status is WorkerStatus.HEALTHY

            after_restart = _job(project_id, "after-restart")
            second_record = await runtime.dispatch(after_restart, now=reregistered_at)
            assert second_record.worker_id == worker_id
            second_result = await runtime.result(after_restart.worker_job_id)
            assert second_result is not None
            assert second_result.worker_id == worker_id
        finally:
            if not first_task.done():
                first_process.stop()
                await first_task
            await first_transport.close(graceful=False)
            if second_process is not None and second_task is not None:
                second_process.stop()
                await second_task
            if second_transport is not None:
                await second_transport.close(graceful=False)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())
