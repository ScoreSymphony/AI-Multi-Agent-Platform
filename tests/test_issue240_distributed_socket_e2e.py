from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
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
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
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
from ai_multi_agent_platform.workspaces import WorkspaceAccessMode, WorkspaceFile, WorkspaceType
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider

_TRANSPORT_KEY = "issue-240-composed-e2e-key"
_OUTPUT_BYTES = b"remote worker artifact"


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-240-composed-e2e",
            owner_type="service",
            owner_id="service:issue-240-e2e",
            project_id=project_id,
        ),
        actor_ref="service:issue-240-e2e",
    )


def _registration() -> RegistrationRequest:
    node_id = new_id("node")
    worker_id = new_id("worker")
    resources = ResourceSnapshot(
        cpu_cores_total=8,
        cpu_cores_available=8,
        ram_total_bytes=32_000_000,
        ram_available_bytes=32_000_000,
        storage_total_bytes=100_000_000,
        storage_available_bytes=100_000_000,
    )
    return RegistrationRequest(
        node=NodeRecord(
            node_id=node_id,
            display_name="issue-240-tcp-worker",
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


async def _read_file(files: LocalFileProvider, file_id: str, context: DataAccessContext) -> bytes:
    return b"".join([chunk async for chunk in files.stream_file(file_id, context)])


def test_composed_remote_worker_tcp_workspace_execution_result_and_cleanup(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        source = await files.create_file(b"canonical input", context, content_type="text/plain")
        workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="service", id="issue-240-e2e"),
            workspace_type=WorkspaceType.REMOTE,
            context=context,
            access_mode=WorkspaceAccessMode.READ_WRITE,
            files=(
                WorkspaceFile(
                    relative_path="src/input.txt",
                    file_id=source.file_id,
                    sha256=source.sha256,
                ),
            ),
        )
        assert workspace.base_snapshot_id is not None
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)

        registration = _registration()
        worker_id = registration.workers[0].worker_id
        authentication, authorization, worker_secret = _security(worker_id)
        runtime = DistributedRuntime(DistributedRegistry())

        broker = TcpMessageBroker(authentication_key=_TRANSPORT_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=_TRANSPORT_KEY,
            provider_id="issue-240-e2e-control",
        )
        worker_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=_TRANSPORT_KEY,
            provider_id="issue-240-e2e-worker",
        )
        protocol = DeploymentWorkerProtocolService(
            runtime,
            authentication=authentication,
            authorization=authorization,
            transport=control_transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: context,
        )

        worker_root = (tmp_path / "worker-root").resolve()

        def lifecycle_factory(token: str) -> ExecutorLifecycleBackend:
            execution_root = worker_root / token
            if not execution_root.is_dir():
                raise AssertionError("remote Workspace must be materialized before execution")
            (execution_root / "artifact.txt").write_bytes(_OUTPUT_BYTES)
            return ExecutorLifecycleBackend(
                ReferenceExecutor(worker_root),
                workspace=token,
                action="echo",
            )

        process = DistributedWorkerProcess(
            DistributedWorkerProcessConfig(
                registration=registration,
                worker_id=worker_id,
                workspace_root=worker_root,
                reporting=False,
            ),
            protocol=None,
            transport=worker_transport,
            lifecycle_factory=lifecycle_factory,
        )
        process_task = asyncio.create_task(process.run())
        try:
            await asyncio.sleep(0.05)
            now = datetime.now(UTC)
            await protocol.register(
                registration,
                WorkerRequestCredentials(
                    token=worker_secret,
                    nonce="issue-240-e2e-register",
                    issued_at=now,
                    request_id="issue-240-e2e-register",
                    correlation_id="issue-240-e2e-register",
                ),
                now=now,
            )

            input_artifact = new_id("artifact")
            job = WorkerJobRequest(
                execution=ExecutionRequest(
                    run_id=new_id("run"),
                    subject_type="task",
                    subject_id=new_id("task"),
                    context=OperationContext(
                        correlation_id="issue-240-e2e-dispatch",
                        owner_type="service",
                        owner_id="service:distributed-runtime",
                        project_id=project_id,
                    ),
                    input={"plan_ref": "write a Worker-local artifact"},
                ),
                requirements=JobRequirements(
                    executor_type="reference",
                    capability_refs=("execution:general",),
                    runtime="python",
                ),
                workspace_ref=workspace.id,
                snapshot_ref=snapshot.id,
                artifact_refs=(input_artifact,),
            )

            dispatched = await runtime.dispatch(job)
            assert dispatched.worker_id == worker_id
            result = await runtime.result(job.worker_job_id)
            assert result is not None
            assert result.worker_id == worker_id
            assert input_artifact in result.artifact_refs

            materialized_snapshot = worker_root / workspace.id / snapshot.id
            assert not materialized_snapshot.exists()

            canonical_files = await files.list_files(context)
            outputs = [
                item
                for item in canonical_files
                if item.metadata.get("relative_path") == "artifact.txt"
                and item.metadata.get("workspace_id") == workspace.id
            ]
            assert len(outputs) == 1
            assert await _read_file(files, outputs[0].file_id, context) == _OUTPUT_BYTES
        finally:
            process.stop()
            await process_task
            await worker_transport.close(graceful=False)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())
