from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    APPLICATION_BUILD_WORKER_INPUT_KEY,
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
    DistributedRegistry,
    DistributedRuntime,
    JsonDistributedStateStore,
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


class _RunningWorkerDispatcher:
    def __init__(self, worker_id: str) -> None:
        self._worker_id = worker_id
        self.jobs: dict[str, WorkerJobRequest] = {}
        self.dispatch_calls = 0
        self.get_calls = 0

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
            backend_ref=f"restart:{job.worker_job_id}",
        )

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        self.get_calls += 1
        return ExecutionSnapshot(run_id=job.execution.run_id, status=RunStatus.RUNNING)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(run_id=job.execution.run_id, status=RunStatus.CANCELLED)


async def _release_and_binding() -> tuple[
    ApplicationRelease,
    InMemoryApplicationReleaseRepository,
    InMemoryRunWorkspaceBindingRepository,
    ExecutionRequest,
    str,
    str,
]:
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
    release = ApplicationRelease(
        application_id="restart-app",
        display_name="Restart App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_snapshot_id=snapshot_id,
        workspace_content_checksum=checksum,
        source_revision="source-revision-restart",
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
    operation = OperationContext(
        correlation_id="issue-749-restart",
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
    return release, releases, bindings, request, workspace_id, snapshot_id


def test_control_plane_restart_reconciles_same_application_build_without_redispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (
            release,
            releases,
            bindings,
            request,
            workspace_id,
            snapshot_id,
        ) = await _release_and_binding()
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        state_path = tmp_path / "distributed-state.json"
        base_time = datetime.now(UTC)
        node_id = new_id("node")
        worker_id = new_id("worker")
        registration = RegistrationRequest(
            node=NodeRecord(
                node_id=node_id,
                display_name="restart-worker",
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
        dispatcher = _RunningWorkerDispatcher(worker_id)

        first_registry = DistributedRegistry()
        first_runtime = DistributedRuntime(
            first_registry,
            state_store=JsonDistributedStateStore(state_path),
        )
        first_runtime.register(registration, now=base_time)
        first_runtime.attach_worker(dispatcher)
        first_lifecycle = DistributedApplicationBuildLifecycleBackend(
            releases,
            files,
            bindings,
            first_runtime,
        )

        first_handle = await first_lifecycle.start(request)
        assert dispatcher.dispatch_calls == 1
        assert state_path.exists()
        first_record = first_runtime.records()[0]
        canonical_worker_job_id = first_record.job.worker_job_id

        restored_registry = DistributedRegistry()
        restored_runtime = DistributedRuntime(
            restored_registry,
            state_store=JsonDistributedStateStore(state_path),
        )
        restored_runtime.register(registration, now=base_time + timedelta(seconds=1))
        restored_runtime.attach_worker(dispatcher)
        restored_lifecycle = DistributedApplicationBuildLifecycleBackend(
            releases,
            files,
            bindings,
            restored_runtime,
        )

        recovered = await restored_lifecycle.get(request.run_id, request.context)
        assert recovered.status is RunStatus.RUNNING
        restored_record = restored_runtime.get_record(canonical_worker_job_id)
        assert restored_record.job.execution.run_id == request.run_id
        assert restored_record.job.workspace_ref == workspace_id
        assert restored_record.job.snapshot_ref == snapshot_id
        assert restored_record.job.dispatch_attempt == 1
        payload = restored_record.job.execution.input[APPLICATION_BUILD_WORKER_INPUT_KEY]
        assert isinstance(payload, dict)
        assert payload["release_id"] == release.release_id
        assert payload["source_revision"] == release.source_revision
        assert payload["workspace_id"] == workspace_id
        assert payload["workspace_snapshot_id"] == snapshot_id
        assert payload["workspace_content_checksum"] == release.workspace_content_checksum

        repeated_handle = await restored_lifecycle.start(request)
        assert repeated_handle.run_id == first_handle.run_id
        assert repeated_handle.backend_ref == first_handle.backend_ref
        assert dispatcher.dispatch_calls == 1
        assert len(restored_runtime.records()) == 1

    asyncio.run(scenario())


def test_selected_worker_disappearing_before_dispatch_fails_retryably_without_stale_claim(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        (
            _release,
            releases,
            bindings,
            request,
            _workspace_id,
            _snapshot_id,
        ) = await _release_and_binding()
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        registry = DistributedRegistry()
        runtime = DistributedRuntime(registry)
        node_id = new_id("node")
        worker_id = new_id("worker")
        runtime.register(
            RegistrationRequest(
                node=NodeRecord(
                    node_id=node_id,
                    display_name="vanishing-worker",
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
        lifecycle = DistributedApplicationBuildLifecycleBackend(
            releases,
            files,
            bindings,
            runtime,
        )

        with pytest.raises(ContractError) as unavailable:
            await lifecycle.start(request)
        assert unavailable.value.code is ErrorCode.UNAVAILABLE
        assert unavailable.value.retryable
        assert not registry.active_reservations()
        assert not runtime.records()

        dispatcher = _RunningWorkerDispatcher(worker_id)
        runtime.attach_worker(dispatcher)
        recovered = await lifecycle.start(request)
        assert recovered.run_id == request.run_id
        assert dispatcher.dispatch_calls == 1
        assert len(runtime.records()) == 1

    asyncio.run(scenario())
