from __future__ import annotations

import asyncio
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionRequest, ExecutionSnapshot
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.distributed import WorkerJobRequest
from ai_multi_agent_platform.distributed.registry import RegistryError
from ai_multi_agent_platform.distributed.workspace import (
    MaterializingWorkerDispatcher,
    WorkspaceJobMaterializationResolver,
)
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationRequest,
    RemoteMaterializationResult,
    RemoteWorkspaceMaterializer,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceProvider,
    WorkspaceSnapshot,
    WorkspaceType,
)


class _WorkspaceLookup:
    def __init__(self, workspace: Workspace, snapshot: WorkspaceSnapshot) -> None:
        self.workspace = workspace
        self.snapshot = snapshot

    async def get_workspace(self, workspace_id: str) -> Workspace:
        assert workspace_id == self.workspace.id
        return self.workspace

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot:
        assert snapshot_id == self.snapshot.id
        return self.snapshot


class _RemoteMaterializer(RemoteWorkspaceMaterializer):
    def __init__(self, worker_id: str, events: list[str]) -> None:
        self.worker_id = worker_id
        self.events = events
        self.materialize_calls = 0
        self.result_artifact = new_id("artifact")

    async def materialize(
        self,
        request: RemoteMaterializationRequest,
    ) -> RemoteMaterializationReceipt:
        self.materialize_calls += 1
        self.events.append("materialize")
        return RemoteMaterializationReceipt(
            workspace_id=request.workspace_id,
            snapshot_id=request.snapshot_id,
            expected_checksum=request.expected_checksum,
            observed_checksum=request.expected_checksum,
            access_mode=request.access_mode,
            worker_ref=self.worker_id,
            materialization_ref="remote-materialization-14",
        )

    async def collect_result(
        self,
        receipt: RemoteMaterializationReceipt,
    ) -> RemoteMaterializationResult:
        self.events.append("collect")
        return RemoteMaterializationResult(
            workspace_id=receipt.workspace_id,
            snapshot_id=receipt.snapshot_id,
            materialization_ref=receipt.materialization_ref,
            content_checksum=receipt.observed_checksum,
            artifact_ids=(self.result_artifact,),
        )

    async def cleanup(
        self,
        receipt: RemoteMaterializationReceipt,
        outcome: MaterializationOutcome,
    ) -> RemoteCleanupAcknowledgement:
        self.events.append(f"cleanup:{outcome.value}")
        return RemoteCleanupAcknowledgement(
            workspace_id=receipt.workspace_id,
            snapshot_id=receipt.snapshot_id,
            materialization_ref=receipt.materialization_ref,
            outcome=outcome,
            succeeded=True,
        )


class _Dispatcher:
    def __init__(self, worker_id: str, events: list[str], *, fail_first_dispatch: bool = False) -> None:
        self._worker_id = worker_id
        self.events = events
        self.fail_first_dispatch = fail_first_dispatch
        self.dispatch_calls = 0
        self.jobs: dict[str, WorkerJobRequest] = {}
        self.status = RunStatus.RUNNING

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        self.dispatch_calls += 1
        self.events.append("dispatch")
        self.jobs[job.worker_job_id] = job
        if self.fail_first_dispatch and self.dispatch_calls == 1:
            raise RuntimeError("acknowledgement lost")
        return ExecutionHandle(run_id=job.execution.run_id)

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        self.events.append("get")
        return ExecutionSnapshot(run_id=job.execution.run_id, status=self.status)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        self.events.append("cancel")
        self.status = RunStatus.CANCELLED
        return ExecutionSnapshot(run_id=job.execution.run_id, status=self.status)


def _workspace() -> tuple[Workspace, WorkspaceSnapshot]:
    workspace = Workspace(
        project_id=new_id("project"),
        owner_ref=OwnerRef(type="service", id="issue-14"),
        workspace_type=WorkspaceType.REMOTE,
        access_mode=WorkspaceAccessMode.READ_WRITE,
    )
    snapshot = WorkspaceSnapshot(
        workspace_id=workspace.id,
        revision=1,
        files=(),
        content_checksum="a" * 64,
    )
    return workspace, snapshot


def _job(workspace: Workspace, snapshot: WorkspaceSnapshot) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(correlation_id="issue-14-workspace"),
        ),
        workspace_ref=workspace.id,
        snapshot_ref=snapshot.id,
        artifact_refs=(new_id("artifact"),),
    )


def _resolver(workspace: Workspace, snapshot: WorkspaceSnapshot) -> WorkspaceJobMaterializationResolver:
    lookup = cast(WorkspaceProvider, _WorkspaceLookup(workspace, snapshot))
    return WorkspaceJobMaterializationResolver(lookup)


def test_remote_workspace_materializes_before_dispatch_and_finalizes_terminal_result() -> None:
    workspace, snapshot = _workspace()
    worker_id = new_id("worker")
    events: list[str] = []
    materializer = _RemoteMaterializer(worker_id, events)
    inner = _Dispatcher(worker_id, events)
    dispatcher = MaterializingWorkerDispatcher(
        inner,
        materializer,
        _resolver(workspace, snapshot),
    )
    job = _job(workspace, snapshot)

    async def scenario() -> None:
        handle = await dispatcher.dispatch(job)
        assert handle.run_id == job.execution.run_id
        assert events == ["materialize", "dispatch"]

        inner.status = RunStatus.SUCCEEDED
        result = await dispatcher.result(job.worker_job_id)
        assert result is not None
        assert result.status.value == "succeeded"
        assert materializer.result_artifact in result.artifact_refs
        assert events == [
            "materialize",
            "dispatch",
            "get",
            "collect",
            "cleanup:succeeded",
        ]

        evidence = dispatcher.evidence(job.worker_job_id)
        assert evidence.receipt is not None
        assert evidence.receipt.worker_ref == worker_id
        assert evidence.result is not None
        assert evidence.cleanup is not None
        assert evidence.cleanup.succeeded is True

    asyncio.run(scenario())


def test_lost_dispatch_ack_reuses_materialization_instead_of_rematerializing() -> None:
    workspace, snapshot = _workspace()
    worker_id = new_id("worker")
    events: list[str] = []
    materializer = _RemoteMaterializer(worker_id, events)
    inner = _Dispatcher(worker_id, events, fail_first_dispatch=True)
    dispatcher = MaterializingWorkerDispatcher(
        inner,
        materializer,
        _resolver(workspace, snapshot),
    )
    job = _job(workspace, snapshot)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            await dispatcher.dispatch(job)
        assert materializer.materialize_calls == 1
        assert events == ["materialize", "dispatch"]

        handle = await dispatcher.dispatch(job)
        assert handle.run_id == job.execution.run_id
        assert materializer.materialize_calls == 1
        assert events == ["materialize", "dispatch", "dispatch"]

    asyncio.run(scenario())


def test_remote_workspace_cancellation_collects_and_cleans_up_as_cancelled() -> None:
    workspace, snapshot = _workspace()
    worker_id = new_id("worker")
    events: list[str] = []
    materializer = _RemoteMaterializer(worker_id, events)
    inner = _Dispatcher(worker_id, events)
    dispatcher = MaterializingWorkerDispatcher(
        inner,
        materializer,
        _resolver(workspace, snapshot),
    )
    job = _job(workspace, snapshot)

    async def scenario() -> None:
        await dispatcher.dispatch(job)
        cancelled = await dispatcher.cancel(job.worker_job_id)
        assert cancelled.status is RunStatus.CANCELLED
        evidence = dispatcher.evidence(job.worker_job_id)
        assert evidence.cleanup is not None
        assert evidence.cleanup.outcome is MaterializationOutcome.CANCELLED
        assert events[-3:] == ["cancel", "collect", "cleanup:cancelled"]

    asyncio.run(scenario())


def test_workspace_resolver_rejects_incomplete_or_mismatched_canonical_refs() -> None:
    workspace, snapshot = _workspace()
    resolver = _resolver(workspace, snapshot)
    incomplete = WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(correlation_id="issue-14-workspace-incomplete"),
        ),
        workspace_ref=workspace.id,
    )
    other_workspace = Workspace(
        project_id=workspace.project_id,
        owner_ref=workspace.owner_ref,
        workspace_type=WorkspaceType.REMOTE,
    )
    mismatched_lookup = cast(WorkspaceProvider, _WorkspaceLookup(other_workspace, snapshot))
    mismatched_resolver = WorkspaceJobMaterializationResolver(mismatched_lookup)
    mismatched = WorkerJobRequest(
        execution=incomplete.execution,
        workspace_ref=other_workspace.id,
        snapshot_ref=snapshot.id,
    )

    async def scenario() -> None:
        with pytest.raises(RegistryError, match="both workspace_ref and snapshot_ref"):
            await resolver.resolve(incomplete)
        with pytest.raises(RegistryError, match="does not belong"):
            await mismatched_resolver.resolve(mismatched)

    asyncio.run(scenario())


def test_materialization_receipt_cannot_bind_job_to_another_worker() -> None:
    workspace, snapshot = _workspace()
    worker_id = new_id("worker")
    wrong_worker_id = new_id("worker")
    events: list[str] = []
    materializer = _RemoteMaterializer(wrong_worker_id, events)
    inner = _Dispatcher(worker_id, events)
    dispatcher = MaterializingWorkerDispatcher(
        inner,
        materializer,
        _resolver(workspace, snapshot),
    )

    async def scenario() -> None:
        with pytest.raises(RegistryError, match="different Worker"):
            await dispatcher.dispatch(_job(workspace, snapshot))
        assert inner.dispatch_calls == 0

    asyncio.run(scenario())
