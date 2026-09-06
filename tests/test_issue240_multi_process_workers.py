from __future__ import annotations

import asyncio
import multiprocessing
import queue
from datetime import UTC, datetime
from pathlib import Path

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
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
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

_TRANSPORT_KEY = "issue-240-multi-process-key"


def _resources() -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_cores_total=8,
        cpu_cores_available=8,
        ram_total_bytes=32_000_000,
        ram_available_bytes=32_000_000,
        storage_total_bytes=100_000_000,
        storage_available_bytes=100_000_000,
    )


def _worker(node_id: str, worker_id: str) -> WorkerRecord:
    return WorkerRecord(
        worker_id=worker_id,
        node_id=node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
        capability_refs=("execution:general",),
        concurrency_limit=1,
    )


def _parent_registration(node_id: str, first: str, second: str) -> RegistrationRequest:
    return RegistrationRequest(
        node=NodeRecord(
            node_id=node_id,
            display_name="issue-240-two-process-node",
            resources=_resources(),
            supported_runtimes=("python",),
            capability_refs=("execution:general",),
        ),
        workers=(_worker(node_id, first), _worker(node_id, second)),
        service_identity_ref=first,
    )


def _child_registration(node_id: str, worker_id: str) -> RegistrationRequest:
    return RegistrationRequest(
        node=NodeRecord(
            node_id=node_id,
            display_name="issue-240-process-child",
            resources=_resources(),
            supported_runtimes=("python",),
            capability_refs=("execution:general",),
        ),
        workers=(_worker(node_id, worker_id),),
        service_identity_ref=worker_id,
    )


def _worker_process(
    host: str,
    port: int,
    node_id: str,
    worker_id: str,
    workspace_root: str,
    ready: object,
) -> None:
    async def serve() -> None:
        transport = TcpMessageTransport(
            host,
            port,
            authentication_key=_TRANSPORT_KEY,
            provider_id=f"issue-240-process:{worker_id}",
        )
        process = DistributedWorkerProcess(
            DistributedWorkerProcessConfig(
                registration=_child_registration(node_id, worker_id),
                worker_id=worker_id,
                workspace_root=Path(workspace_root).resolve(),
                reporting=False,
            ),
            protocol=None,
            transport=transport,
        )
        task = asyncio.create_task(process.run())
        await asyncio.sleep(0.05)
        ready.put(worker_id)  # type: ignore[attr-defined]
        try:
            await task
        finally:
            process.stop()
            await transport.close(graceful=False)

    asyncio.run(serve())


def _security(reporter_id: str):
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
    credential = authentication.create_worker_credential(reporter_id, scope=scope)
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=reporter_id,
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


def _job(project_id: str, marker: str) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(
                correlation_id=f"issue-240-multiprocess:{marker}",
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


def test_control_plane_dispatches_to_two_independent_worker_processes(tmp_path: Path) -> None:
    async def scenario() -> None:
        node_id = new_id("node")
        first = new_id("worker")
        second = new_id("worker")
        registration = _parent_registration(node_id, first, second)
        authentication, authorization, reporter_secret = _security(first)

        broker = TcpMessageBroker(authentication_key=_TRANSPORT_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=_TRANSPORT_KEY,
            provider_id="issue-240-multiprocess-control",
        )
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
        runtime = DistributedRuntime(DistributedRegistry())
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

        ctx = multiprocessing.get_context("spawn")
        ready = ctx.Queue()
        processes = [
            ctx.Process(
                target=_worker_process,
                args=(
                    broker.host,
                    broker.port,
                    node_id,
                    worker_id,
                    str(tmp_path / f"worker-{index}"),
                    ready,
                ),
            )
            for index, worker_id in enumerate((first, second), start=1)
        ]
        for process in processes:
            process.start()

        try:
            observed: set[str] = set()
            for _ in processes:
                try:
                    observed.add(await asyncio.to_thread(ready.get, True, 10))
                except queue.Empty as exc:
                    raise AssertionError("Worker process did not become ready") from exc
            assert observed == {first, second}

            now = datetime.now(UTC)
            await service.register(
                registration,
                WorkerRequestCredentials(
                    token=reporter_secret,
                    nonce="issue-240-multiprocess-register",
                    issued_at=now,
                    request_id="issue-240-multiprocess-register",
                    correlation_id="issue-240-multiprocess-register",
                ),
                now=now,
            )

            project_id = new_id("project")
            first_job = _job(project_id, "first-worker")
            second_job = _job(project_id, "second-worker")
            first_record = await runtime.dispatch_to_worker(first_job, first)
            second_record = await runtime.dispatch_to_worker(second_job, second)
            assert first_record.worker_id == first
            assert second_record.worker_id == second

            first_result = await runtime.result(first_job.worker_job_id)
            second_result = await runtime.result(second_job.worker_job_id)
            assert first_result is not None
            assert second_result is not None
            assert first_result.worker_id == first
            assert second_result.worker_id == second
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
            for process in processes:
                process.join(timeout=5)
            ready.close()
            ready.join_thread()
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())
