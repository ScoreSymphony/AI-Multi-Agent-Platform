from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
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
from ai_multi_agent_platform.distributed.transport import WorkerTransportEndpoint
from ai_multi_agent_platform.distributed.worker_protocol import WorkerRequestCredentials
from ai_multi_agent_platform.domain import new_id
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
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


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
            display_name="issue-240-restart-node",
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


def _credentials(secret: str, nonce: str, issued_at: datetime) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=issued_at,
        request_id=nonce,
        correlation_id=nonce,
    )


def test_graceful_deregister_then_reregister_reattaches_same_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        registration = _registration()
        worker_id = registration.workers[0].worker_id
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
        runtime = DistributedRuntime(DistributedRegistry())
        transport = InProcessMessageTransport(provider_id="issue-240-restart")
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        service = DeploymentWorkerProtocolService(
            runtime,
            authentication=authentication,
            authorization=authorization,
            transport=transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: (_ for _ in ()).throw(
                AssertionError("workspace context must not be used by workspace-free jobs")
            ),
        )

        executor_root = (tmp_path / "worker").resolve()
        ExecutorLifecycleBackend.ensure_workspace(executor_root, "reference")
        endpoint_task = asyncio.create_task(
            WorkerTransportEndpoint(
                LocalWorker(
                    worker_id,
                    ExecutorLifecycleBackend(
                        ReferenceExecutor(executor_root),
                        workspace="reference",
                    ),
                ),
                transport,
            ).serve()
        )
        try:
            first = datetime.now(UTC)
            await service.register(
                registration,
                _credentials(credential.secret, "issue-240-register-first", first),
                now=first,
            )
            second = first + timedelta(seconds=1)
            await service.deregister_worker(
                worker_id,
                registration.node.node_id,
                _credentials(credential.secret, "issue-240-deregister", second),
                now=second,
            )
            third = second + timedelta(seconds=1)
            await service.register(
                registration,
                _credentials(credential.secret, "issue-240-register-second", third),
                now=third,
            )

            job = WorkerJobRequest(
                execution=ExecutionRequest(
                    run_id=new_id("run"),
                    subject_type="task",
                    subject_id=new_id("task"),
                    context=OperationContext(
                        correlation_id="issue-240-after-reregister",
                        owner_type="service",
                        owner_id="service:issue-240",
                        project_id=new_id("project"),
                    ),
                    input={"plan_ref": "after-reregister"},
                ),
                requirements=JobRequirements(
                    executor_type="reference",
                    capability_refs=("execution:general",),
                    runtime="python",
                ),
            )
            record = await runtime.dispatch(job, now=third)
            assert record.worker_id == worker_id
            result = await runtime.result(job.worker_job_id)
            assert result is not None
            assert result.worker_id == worker_id
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())
