from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
)
from ai_multi_agent_platform.distributed import (
    WORKSPACE_ARTIFACT_CAPABILITY_ID,
    ArtifactPublishingWorkerDispatcher,
    CanonicalWorkspaceArtifactPublisher,
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    JobResultStatus,
    LocalWorker,
    MaterializingWorkerDispatcher,
    NodeRecord,
    RegistrationRequest,
    WorkerHeartbeatRequest,
    WorkerJobRequest,
    WorkerRecord,
    WorkerRequestCredentials,
    WorkerStatus,
    WorkspaceJobMaterializationResolver,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import PlatformKernel
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
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    WorkspaceAccessMode,
    WorkspaceType,
)
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


def _credentials(secret: str, nonce: str, issued_at: datetime) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=secret,
        nonce=nonce,
        issued_at=issued_at,
        request_id=nonce,
        correlation_id=nonce,
    )


def test_worker_registration_publishes_and_withdraws_artifact_capability(tmp_path: Path) -> None:
    async def scenario() -> None:
        node_id = new_id("node")
        worker_id = new_id("worker")
        worker = WorkerRecord(
            worker_id=worker_id,
            node_id=node_id,
            supported_executors=("reference",),
            capability_refs=(WORKSPACE_ARTIFACT_CAPABILITY_ID,),
        )
        registration = RegistrationRequest(
            node=NodeRecord(node_id=node_id, display_name="issue-46-artifact-node"),
            workers=(worker,),
            service_identity_ref=worker_id,
        )

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
        scope = CredentialScope(
            actions=actions,
            resource_types=frozenset({ResourceType.NODE, ResourceType.WORKER}),
        )
        credential = authentication.create_worker_credential(worker_id, scope=scope)
        authorization = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=worker_id,
                    actor_types=frozenset({ActorType.WORKER}),
                    allowed_actions=actions,
                    resource_types=frozenset({ResourceType.NODE, ResourceType.WORKER}),
                ),
            )
        )

        runtime = DistributedRuntime(DistributedRegistry())
        transport = InProcessMessageTransport(provider_id="issue-46-artifact-provider-lifecycle")
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        capabilities = CapabilityRegistry()
        service = DeploymentWorkerProtocolService(
            runtime,
            authentication=authentication,
            authorization=authorization,
            transport=transport,
            workspaces=workspaces,
            files=files,
            context_resolver=lambda _workspace: (_ for _ in ()).throw(
                AssertionError("artifact provider lifecycle must not materialize a Workspace")
            ),
            capabilities=capabilities,
            workspace_bindings=InMemoryRunWorkspaceBindingRepository(),
        )

        start = datetime.now(UTC)
        try:
            await service.register(
                registration,
                _credentials(credential.secret, "issue-46-artifact-register", start),
                now=start,
            )
            providers = {item.provider_id for item in capabilities.inventory_providers()}
            assert providers == {f"distributed.executor.reference-artifact.{worker_id}"}
            assert [item.capability_id for item in capabilities.inventory_capabilities()] == [
                WORKSPACE_ARTIFACT_CAPABILITY_ID
            ]

            unhealthy_at = start + timedelta(seconds=1)
            await service.heartbeat(
                WorkerHeartbeatRequest(
                    heartbeat=Heartbeat(
                        node_id=node_id,
                        observed_at=unhealthy_at,
                        sequence=2,
                        workers=(replace(worker, status=WorkerStatus.UNHEALTHY),),
                    ),
                    service_identity_ref=worker_id,
                ),
                _credentials(
                    credential.secret,
                    "issue-46-artifact-heartbeat-unhealthy",
                    unhealthy_at,
                ),
                now=unhealthy_at,
            )
            assert capabilities.inventory_providers() == ()
            assert capabilities.inventory_capabilities() == ()

            healthy_at = unhealthy_at + timedelta(seconds=1)
            await service.heartbeat(
                WorkerHeartbeatRequest(
                    heartbeat=Heartbeat(
                        node_id=node_id,
                        observed_at=healthy_at,
                        sequence=3,
                        workers=(replace(worker, status=WorkerStatus.HEALTHY),),
                    ),
                    service_identity_ref=worker_id,
                ),
                _credentials(
                    credential.secret,
                    "issue-46-artifact-heartbeat-healthy",
                    healthy_at,
                ),
                now=healthy_at,
            )
            assert len(capabilities.inventory_providers()) == 1
            assert capabilities.inventory_capabilities()[0].capability_id == (
                WORKSPACE_ARTIFACT_CAPABILITY_ID
            )

            removed_at = healthy_at + timedelta(seconds=1)
            await service.deregister_worker(
                worker_id,
                node_id,
                _credentials(credential.secret, "issue-46-artifact-deregister", removed_at),
                now=removed_at,
            )
            assert capabilities.inventory_providers() == ()
            assert capabilities.inventory_capabilities() == ()
        finally:
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_artifact_decorator_preserves_successful_step_scoped_worker_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
        )
        task = await kernel.create_task(
            idempotency_key="issue-46-step-artifact:create",
            title="Step-scoped Worker output",
            objective="Keep successful Step execution independent from Task artifact publication",
            owner_type="user",
            owner_id="issue-46-step-owner",
            project_id=project_id,
        )
        await kernel.ready_task(
            idempotency_key="issue-46-step-artifact:ready",
            task_id=task.task_id,
        )
        run = await kernel.create_run(
            idempotency_key="issue-46-step-artifact:run",
            task_id=task.task_id,
        )
        operation = OperationContext(
            correlation_id=task.task_id,
            owner_type="user",
            owner_id="issue-46-step-owner",
            project_id=project_id,
        )
        context = DataAccessContext(
            operation=operation,
            actor_ref="user:issue-46-step-owner",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        files = LocalFileProvider(tmp_path / "step-objects", tmp_path / "step-files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "step-control-workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="issue-46-step-owner"),
            workspace_type=WorkspaceType.REMOTE,
            context=context,
            access_mode=WorkspaceAccessMode.READ_WRITE,
        )
        assert workspace.base_snapshot_id is not None
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)

        worker_id = new_id("worker")
        worker_root = tmp_path / "step-worker-root"
        transport = InProcessMessageTransport(provider_id="issue-46-step-artifact-result")
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, transport).serve()
        )
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,
            workspaces,
            files,
            lambda _workspace: context,
        )
        lifecycle = FakeLifecycleBackend()
        materializing = MaterializingWorkerDispatcher(
            LocalWorker(worker_id, lifecycle),
            materializer,
            WorkspaceJobMaterializationResolver(workspaces),
        )
        dispatcher = ArtifactPublishingWorkerDispatcher(
            materializing,
            CanonicalWorkspaceArtifactPublisher(
                workspaces,
                files,
                kernel,
                lambda _workspace: context,
            ),
        )
        job = WorkerJobRequest(
            execution=ExecutionRequest(
                run_id=run.run_id,
                subject_type="step",
                subject_id=new_id("step"),
                context=operation,
                input={},
            ),
            workspace_ref=workspace.id,
            snapshot_ref=snapshot.id,
        )

        try:
            await dispatcher.dispatch(job)
            lifecycle.complete(run.run_id)
            result = await dispatcher.result(job.worker_job_id)
            assert result is not None
            assert result.status is JobResultStatus.SUCCEEDED
            assert result.execution is not None

            evidence = dispatcher.evidence(job.worker_job_id)
            assert evidence.result is not None
            assert evidence.result.changes == ()

            task_state = await kernel.get_task(task.task_id)
            run_state = await kernel.get_run(task.task_id, run.run_id)
            assert task_state.artifact_ids == ()
            assert run_state.artifact_ids == ()

            repeated = await dispatcher.result(job.worker_job_id)
            assert repeated is not None
            assert repeated.status is JobResultStatus.SUCCEEDED
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())
