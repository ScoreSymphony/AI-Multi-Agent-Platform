from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    DistributedApplicationBuildLifecycleBackend,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    JobResultStatus,
    NodeRecord,
    RegistrationRequest,
    WorkerJobRequest,
    WorkerJobResult,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
)


@dataclass(slots=True)
class _Harness:
    release: ApplicationRelease
    lifecycle: DistributedApplicationBuildLifecycleBackend
    runtime: DistributedRuntime
    files: LocalFileProvider
    operation: OperationContext
    request: ExecutionRequest
    task_id: str
    run_id: str
    workspace_id: str
    snapshot_id: str
    worker_id: str


def _result_identity(release: ApplicationRelease, target: BuildTargetState) -> dict[str, JsonValue]:
    return {
        "release_id": release.release_id,
        "target_id": target.target.target_id,
        "build_specification_id": release.build_specification.spec_id,
        "build_specification_revision": release.build_specification.revision,
        "source_revision": release.source_revision,
        "workspace_id": release.workspace_id,
        "workspace_snapshot_id": release.workspace_snapshot_id,
        "workspace_content_checksum": release.workspace_content_checksum,
        "output_path": target.target.output_path,
    }


class _LostDispatchReplyDispatcher:
    def __init__(self, worker_id: str) -> None:
        self._worker_id = worker_id
        self.jobs: dict[str, WorkerJobRequest] = {}
        self.dispatch_calls = 0
        self.get_calls = 0

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        self.jobs[job.worker_job_id] = job
        self.dispatch_calls += 1
        raise RuntimeError("simulated lost dispatch reply")

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        self.get_calls += 1
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(run_id=job.execution.run_id, status=RunStatus.RUNNING)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(run_id=job.execution.run_id, status=RunStatus.CANCELLED)


class _LostResultReplyDispatcher:
    def __init__(
        self,
        worker_id: str,
        identity: dict[str, JsonValue],
        artifact_id: str,
    ) -> None:
        self._worker_id = worker_id
        self._identity = dict(identity)
        self._artifact_id = artifact_id
        self.jobs: dict[str, WorkerJobRequest] = {}
        self.dispatch_calls = 0
        self.result_calls = 0

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self.jobs.get(job.worker_job_id)
        if existing is not None:
            assert existing == job
        else:
            self.jobs[job.worker_job_id] = job
            self.dispatch_calls += 1
        return ExecutionHandle(
            run_id=job.execution.run_id,
            backend_ref=f"lost-result-reply:{job.worker_job_id}",
        )

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(
            run_id=job.execution.run_id,
            status=RunStatus.SUCCEEDED,
            output={"output": {"application_build_request": dict(self._identity)}},
        )

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(run_id=job.execution.run_id, status=RunStatus.CANCELLED)

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        self.result_calls += 1
        snapshot = await self.get(worker_job_id)
        result = WorkerJobResult(
            worker_job_id=worker_job_id,
            worker_id=self.worker_id,
            status=JobResultStatus.SUCCEEDED,
            execution=snapshot,
            artifact_refs=(self._artifact_id,),
        )
        if self.result_calls == 1:
            raise RuntimeError("simulated lost result reply")
        return result


async def _setup(tmp_path: Path) -> _Harness:
    task_id = new_id("task")
    run_id = new_id("run")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    snapshot_id = new_id("workspace_snapshot")
    checksum = "3" * 64
    target = BuildTarget(
        target_id="linux-x64",
        os_name="linux",
        architecture="x86_64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )
    specification = BuildSpecification(
        command=("python", "build.py"),
        targets=(target,),
    )
    target_state = BuildTargetState(
        target=target,
        status=BuildTargetStatus.RUNNING,
        task_id=task_id,
        run_id=run_id,
    )
    release = ApplicationRelease(
        application_id="lost-reply-app",
        display_name="Lost Reply App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_snapshot_id=snapshot_id,
        workspace_content_checksum=checksum,
        source_revision="source-revision-lost-reply",
        build_specification=specification,
        creator_ref="user:tester",
        status=ReleaseStatus.BUILDING,
        targets=(target_state,),
    )
    releases = InMemoryApplicationReleaseRepository()
    await releases.save(release, expected_revision=0)
    bindings = InMemoryRunWorkspaceBindingRepository()
    await bindings.bind(
        RunWorkspaceBinding(
            run_id=run_id,
            task_id=task_id,
            workspace_id=workspace_id,
            workspace_snapshot_id=snapshot_id,
            content_checksum=checksum,
        )
    )
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    registry = DistributedRegistry()
    node_id = new_id("node")
    worker_id = new_id("worker")
    registry.register(
        RegistrationRequest(
            node=NodeRecord(
                node_id=node_id,
                display_name="lost-reply-worker",
                os_name="linux",
                architecture="x86_64",
            ),
            workers=(
                WorkerRecord(
                    worker_id=worker_id,
                    node_id=node_id,
                    capability_refs=(APPLICATION_BUILD_ACTION,),
                ),
            ),
            service_identity_ref=worker_id,
        )
    )
    runtime = DistributedRuntime(registry)
    lifecycle = DistributedApplicationBuildLifecycleBackend(
        releases,
        files,
        bindings,
        runtime,
    )
    operation = OperationContext(
        correlation_id="issue-749-lost-reply",
        owner_type="user",
        owner_id="tester",
        project_id=project_id,
    )
    request = ExecutionRequest(
        run_id=run_id,
        subject_type="task",
        subject_id=task_id,
        context=operation,
    )
    return _Harness(
        release=release,
        lifecycle=lifecycle,
        runtime=runtime,
        files=files,
        operation=operation,
        request=request,
        task_id=task_id,
        run_id=run_id,
        workspace_id=workspace_id,
        snapshot_id=snapshot_id,
        worker_id=worker_id,
    )


def _file_context(harness: _Harness) -> DataAccessContext:
    return DataAccessContext(
        operation=harness.operation,
        actor_ref="user:tester",
        task_id=harness.task_id,
        run_id=harness.run_id,
    )


def test_lost_dispatch_reply_becomes_retryable_and_retry_reconciles_without_redispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = await _setup(tmp_path)
        dispatcher = _LostDispatchReplyDispatcher(harness.worker_id)
        harness.runtime.attach_worker(dispatcher)

        with pytest.raises(ContractError) as lost_reply:
            await harness.lifecycle.start(harness.request)
        assert lost_reply.value.code is ErrorCode.UNAVAILABLE
        assert lost_reply.value.retryable
        uncertain = harness.runtime.records()[0]
        assert uncertain.state is DispatchState.LOST
        assert uncertain.last_error == "dispatch_outcome_unknown"
        assert dispatcher.dispatch_calls == 1

        recovered = await harness.lifecycle.start(harness.request)

        assert recovered.run_id == harness.run_id
        assert recovered.backend_ref is not None
        assert dispatcher.dispatch_calls == 1
        assert dispatcher.get_calls == 1
        assert len(harness.runtime.records()) == 1
        record = harness.runtime.records()[0]
        assert record.state is DispatchState.RUNNING
        assert record.job.dispatch_attempt == 1

    asyncio.run(scenario())


def test_lost_result_reply_becomes_retryable_and_result_retry_does_not_redispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        harness = await _setup(tmp_path)
        target = harness.release.targets[0]
        artifact_id = new_id("artifact")
        context = _file_context(harness)
        file_record = await harness.files.create_file(
            b"remote-package",
            context,
            content_type="application/octet-stream",
            metadata={
                "workspace_id": harness.workspace_id,
                "workspace_snapshot_id": harness.snapshot_id,
                "relative_path": "dist/app.bin",
            },
        )
        await harness.files.link_artifact(file_record.file_id, artifact_id, context)
        dispatcher = _LostResultReplyDispatcher(
            harness.worker_id,
            _result_identity(harness.release, target),
            artifact_id,
        )
        harness.runtime.attach_worker(dispatcher)
        await harness.lifecycle.start(harness.request)

        with pytest.raises(ContractError) as lost_reply:
            await harness.lifecycle.get(harness.run_id, harness.operation)
        assert lost_reply.value.code is ErrorCode.UNAVAILABLE
        assert lost_reply.value.retryable
        assert dispatcher.dispatch_calls == 1
        assert dispatcher.result_calls == 1
        assert harness.runtime.records()[0].result is None

        recovered = await harness.lifecycle.get(harness.run_id, harness.operation)

        assert recovered.status is RunStatus.SUCCEEDED
        assert dispatcher.dispatch_calls == 1
        assert dispatcher.result_calls == 2
        assert len(harness.runtime.records()) == 1
        assert harness.runtime.records()[0].result is not None

    asyncio.run(scenario())
