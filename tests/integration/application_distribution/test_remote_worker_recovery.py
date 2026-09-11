from __future__ import annotations

import asyncio
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
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    NodeRecord,
    RegistrationRequest,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
)


class _RecoverableWorkerDispatcher:
    def __init__(self, worker_id: str) -> None:
        self._worker_id = worker_id
        self.jobs: dict[str, WorkerJobRequest] = {}
        self.dispatch_calls = 0
        self.get_calls = 0
        self.cancel_calls = 0

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self.jobs.get(job.worker_job_id)
        if existing is not None:
            assert existing == job
            return ExecutionHandle(
                run_id=job.execution.run_id,
                backend_ref=f"recovery:{job.worker_job_id}",
            )
        self.jobs[job.worker_job_id] = job
        self.dispatch_calls += 1
        return ExecutionHandle(
            run_id=job.execution.run_id,
            backend_ref=f"recovery:{job.worker_job_id}",
        )

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        self.get_calls += 1
        return ExecutionSnapshot(run_id=job.execution.run_id, status=RunStatus.RUNNING)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        self.cancel_calls += 1
        return ExecutionSnapshot(run_id=job.execution.run_id, status=RunStatus.CANCELLED)


def test_remote_application_build_reconnect_and_cancel_preserve_single_canonical_job(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        run_id = new_id("run")
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        snapshot_id = new_id("workspace_snapshot")
        checksum = "1" * 64
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
        release = ApplicationRelease(
            application_id="recovery-app",
            display_name="Recovery App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_snapshot_id=snapshot_id,
            workspace_content_checksum=checksum,
            source_revision="source-revision-recovery",
            build_specification=specification,
            creator_ref="user:tester",
            status=ReleaseStatus.BUILDING,
            targets=(
                BuildTargetState(
                    target=target,
                    status=BuildTargetStatus.RUNNING,
                    task_id=task_id,
                    run_id=run_id,
                ),
            ),
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
                    display_name="recovery-worker",
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
        dispatcher = _RecoverableWorkerDispatcher(worker_id)
        runtime.attach_worker(dispatcher)
        lifecycle = DistributedApplicationBuildLifecycleBackend(
            releases,
            files,
            bindings,
            runtime,
        )
        operation = OperationContext(
            correlation_id="issue-749-recovery",
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

        first = await lifecycle.start(request)
        duplicate = await lifecycle.start(request)
        assert duplicate == first
        assert dispatcher.dispatch_calls == 1
        assert len(runtime.records()) == 1
        record = runtime.records()[0]
        canonical_worker_job_id = record.job.worker_job_id
        assert record.worker_id == worker_id
        assert record.job.execution.run_id == run_id
        assert record.job.workspace_ref == workspace_id
        assert record.job.snapshot_ref == snapshot_id

        runtime.detach_worker(worker_id)
        with pytest.raises(ContractError) as lost_error:
            await lifecycle.get(run_id, operation)
        assert lost_error.value.code is ErrorCode.UNAVAILABLE
        assert lost_error.value.retryable
        lost = runtime.get_record(canonical_worker_job_id)
        assert lost.state is DispatchState.LOST
        assert lost.last_error == "worker_unreachable"

        runtime.attach_worker(dispatcher)
        recovered = await lifecycle.get(run_id, operation)
        assert recovered.status is RunStatus.RUNNING
        after_reconnect = runtime.get_record(canonical_worker_job_id)
        assert after_reconnect.state is DispatchState.RUNNING
        assert after_reconnect.worker_id == worker_id
        assert after_reconnect.job.dispatch_attempt == 1
        assert len(runtime.records()) == 1
        assert dispatcher.dispatch_calls == 1

        runtime.detach_worker(worker_id)
        with pytest.raises(ContractError) as pending_error:
            await lifecycle.cancel(run_id, operation)
        assert pending_error.value.code is ErrorCode.UNAVAILABLE
        assert pending_error.value.retryable
        pending = runtime.get_record(canonical_worker_job_id)
        assert pending.state is DispatchState.CANCEL_PENDING

        runtime.attach_worker(dispatcher)
        cancelled = await lifecycle.get(run_id, operation)
        assert cancelled.status is RunStatus.CANCELLED
        terminal = runtime.get_record(canonical_worker_job_id)
        assert terminal.state is DispatchState.TERMINAL
        assert terminal.worker_id == worker_id
        assert terminal.job.worker_job_id == canonical_worker_job_id
        assert terminal.job.dispatch_attempt == 1
        assert len(runtime.records()) == 1
        assert dispatcher.dispatch_calls == 1
        assert dispatcher.cancel_calls == 1
        assert not registry.active_reservations()

    asyncio.run(scenario())
