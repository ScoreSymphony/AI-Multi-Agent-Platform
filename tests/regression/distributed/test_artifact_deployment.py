from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionRequest,
    OperationContext,
    ToolInvocation,
)
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
    WORKSPACE_ARTIFACT_CAPABILITY_ID,
    ArtifactPublishingWorkerDispatcher,
    CanonicalWorkspaceArtifactPublisher,
    DistributedExecutorArtifactProvider,
    DistributedRegistry,
    DistributedRuntime,
    JobResultStatus,
    LocalWorker,
    MaterializingWorkerDispatcher,
    NodeRecord,
    RegistrationRequest,
    WorkerJobRequest,
    WorkerRecord,
    WorkspaceJobMaterializationResolver,
)
from ai_multi_agent_platform.distributed.artifact_capability_provider import (
    DISTRIBUTED_WORKSPACE_ARTIFACT_TOOL_REF,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
    WorkspaceAccessMode,
    WorkspaceType,
)
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


def test_scheduler_backed_artifact_provider_uses_current_worker_eligibility() -> None:
    async def scenario() -> None:
        first_node_id = new_id("node")
        second_node_id = new_id("node")
        first_worker_id = new_id("worker")
        second_worker_id = new_id("worker")
        runtime = DistributedRuntime(DistributedRegistry())
        runtime.register(
            RegistrationRequest(
                node=NodeRecord(node_id=first_node_id, display_name="artifact-node-a"),
                workers=(
                    WorkerRecord(
                        worker_id=first_worker_id,
                        node_id=first_node_id,
                        supported_executors=("reference",),
                        capability_refs=(WORKSPACE_ARTIFACT_CAPABILITY_ID,),
                    ),
                ),
            )
        )
        runtime.register(
            RegistrationRequest(
                node=NodeRecord(node_id=second_node_id, display_name="artifact-node-b"),
                workers=(
                    WorkerRecord(
                        worker_id=second_worker_id,
                        node_id=second_node_id,
                        supported_executors=("reference",),
                        capability_refs=(WORKSPACE_ARTIFACT_CAPABILITY_ID,),
                    ),
                ),
            )
        )

        task_id = new_id("task")
        run_id = new_id("run")
        agent_id = new_id("agent")
        bindings = InMemoryRunWorkspaceBindingRepository()
        await bindings.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=task_id,
                workspace_id=new_id("workspace"),
                workspace_snapshot_id=new_id("workspace_snapshot"),
                content_checksum="0" * 64,
            )
        )
        provider = DistributedExecutorArtifactProvider(
            runtime,
            workspace_bindings=bindings,
        )
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(provider)

        registration, resolved_provider = capabilities.resolve(
            WORKSPACE_ARTIFACT_CAPABILITY_ID,
            version="1.0",
        )
        assert resolved_provider is provider
        assert registration.provider_id == "distributed.executor.reference-artifact"
        assert registration.worker_id is None
        assert registration.node_id is None
        assert len(capabilities.inventory_providers()) == 1

        runtime.set_worker_draining(first_worker_id, draining=True)
        runtime.set_node_maintenance(second_node_id, maintenance=True)
        repeated_registration, repeated_provider = capabilities.resolve(
            WORKSPACE_ARTIFACT_CAPABILITY_ID,
            version="1.0",
        )
        assert repeated_registration == registration
        assert repeated_provider is provider

        try:
            await provider.invoke(
                ToolInvocation(
                    invocation_id="issue-46-scheduler-backed-artifact",
                    tool_ref=DISTRIBUTED_WORKSPACE_ARTIFACT_TOOL_REF,
                    arguments={"path": "out/evidence.txt", "content": "evidence"},
                    context=OperationContext(
                        correlation_id=task_id,
                        causation_id=new_id("tool_invocation"),
                    ),
                    task_id=task_id,
                    run_id=run_id,
                    agent_id=agent_id,
                )
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.UNAVAILABLE
        else:
            raise AssertionError("artifact invocation must honor current drain/maintenance state")

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
