from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

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
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
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


class _TerminalResultDispatcher:
    def __init__(
        self,
        worker_id: str,
        identity: dict[str, JsonValue],
        *,
        artifact_refs: tuple[str, ...] = (),
    ) -> None:
        self._worker_id = worker_id
        self._identity = dict(identity)
        self.artifact_refs = artifact_refs
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
            backend_ref=f"result-evidence:{job.worker_job_id}",
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
        return WorkerJobResult(
            worker_job_id=worker_job_id,
            worker_id=self.worker_id,
            status=JobResultStatus.SUCCEEDED,
            execution=snapshot,
            artifact_refs=self.artifact_refs,
        )


class _ChecksumRejectingFileProvider(LocalFileProvider):
    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool:
        await self.get_file(file_id, context)
        return False


@dataclass(slots=True)
class _Harness:
    release: ApplicationRelease
    lifecycle: DistributedApplicationBuildLifecycleBackend
    runtime: DistributedRuntime
    dispatcher: _TerminalResultDispatcher
    files: LocalFileProvider
    operation: OperationContext
    request: ExecutionRequest
    task_id: str
    run_id: str
    workspace_id: str
    snapshot_id: str


def _worker_result_identity(
    release: ApplicationRelease,
    target: BuildTargetState,
) -> dict[str, JsonValue]:
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


async def _setup(
    tmp_path: Path,
    *,
    provenance_overrides: dict[str, JsonValue] | None = None,
    artifact_refs: tuple[str, ...] = (),
    reject_checksum: bool = False,
) -> _Harness:
    task_id = new_id("task")
    run_id = new_id("run")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    snapshot_id = new_id("workspace_snapshot")
    checksum = "2" * 64
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
        application_id="result-evidence-app",
        display_name="Result Evidence App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_snapshot_id=snapshot_id,
        workspace_content_checksum=checksum,
        source_revision="source-revision-result-evidence",
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

    files: LocalFileProvider
    if reject_checksum:
        files = _ChecksumRejectingFileProvider(
            tmp_path / "files",
            tmp_path / "files.sqlite3",
        )
    else:
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")

    registry = DistributedRegistry()
    node_id = new_id("node")
    worker_id = new_id("worker")
    registry.register(
        RegistrationRequest(
            node=NodeRecord(
                node_id=node_id,
                display_name="result-evidence-worker",
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
    identity = _worker_result_identity(release, target_state)
    if provenance_overrides:
        identity.update(provenance_overrides)
    dispatcher = _TerminalResultDispatcher(
        worker_id,
        identity,
        artifact_refs=artifact_refs,
    )
    runtime.attach_worker(dispatcher)
    lifecycle = DistributedApplicationBuildLifecycleBackend(
        releases,
        files,
        bindings,
        runtime,
    )
    operation = OperationContext(
        correlation_id="issue-749-result-evidence",
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
    await lifecycle.start(request)
    return _Harness(
        release=release,
        lifecycle=lifecycle,
        runtime=runtime,
        dispatcher=dispatcher,
        files=files,
        operation=operation,
        request=request,
        task_id=task_id,
        run_id=run_id,
        workspace_id=workspace_id,
        snapshot_id=snapshot_id,
    )


def _file_context(harness: _Harness) -> DataAccessContext:
    return DataAccessContext(
        operation=harness.operation,
        actor_ref="user:tester",
        task_id=harness.task_id,
        run_id=harness.run_id,
    )


def test_remote_build_rejects_worker_result_with_mismatched_provenance(tmp_path: Path) -> None:
    async def scenario() -> None:
        harness = await _setup(
            tmp_path,
            provenance_overrides={"source_revision": "wrong-source-revision"},
        )

        snapshot = await harness.lifecycle.get(harness.run_id, harness.operation)

        assert snapshot.status is RunStatus.FAILED
        assert "provenance differs from requested build" in str(snapshot.output["stderr"])
        assert harness.dispatcher.dispatch_calls == 1
        assert harness.dispatcher.result_calls == 1
        assert len(harness.runtime.records()) == 1

    asyncio.run(scenario())


def test_remote_build_rejects_success_without_exact_canonical_artifact(tmp_path: Path) -> None:
    async def scenario() -> None:
        artifact_id = new_id("artifact")
        harness = await _setup(tmp_path, artifact_refs=(artifact_id,))

        snapshot = await harness.lifecycle.get(harness.run_id, harness.operation)

        assert snapshot.status is RunStatus.FAILED
        assert "not returned as one canonical changed File/Artifact" in str(
            snapshot.output["stderr"]
        )
        assert harness.dispatcher.dispatch_calls == 1
        assert harness.dispatcher.result_calls == 1
        assert len(harness.runtime.records()) == 1

    asyncio.run(scenario())


def test_remote_build_rejects_artifact_when_canonical_file_checksum_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        artifact_id = new_id("artifact")
        harness = await _setup(
            tmp_path,
            artifact_refs=(artifact_id,),
            reject_checksum=True,
        )
        context = _file_context(harness)
        record = await harness.files.create_file(
            b"remote-package",
            context,
            content_type="application/octet-stream",
            metadata={
                "workspace_id": harness.workspace_id,
                "workspace_snapshot_id": harness.snapshot_id,
                "relative_path": "dist/app.bin",
            },
        )
        await harness.files.link_artifact(record.file_id, artifact_id, context)

        snapshot = await harness.lifecycle.get(harness.run_id, harness.operation)

        assert snapshot.status is RunStatus.FAILED
        assert "failed canonical File checksum verification" in str(snapshot.output["stderr"])
        assert harness.dispatcher.dispatch_calls == 1
        assert harness.dispatcher.result_calls == 1
        assert len(harness.runtime.records()) == 1

    asyncio.run(scenario())
