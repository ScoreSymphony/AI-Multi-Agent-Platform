from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.application_distribution import (
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    DistributedApplicationBuildLifecycleBackend,
    PackageType,
    ReleaseChannel,
    ReleaseVisibility,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionSnapshot,
    OperationContext,
)
from ai_multi_agent_platform.contracts import (
    ExecutionRequest as KernelExecutionRequest,
)
from ai_multi_agent_platform.distributed.registry import RegistryError
from ai_multi_agent_platform.distributed.runtime import DispatchState
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    RunWorkspaceBinding,
)


class _FailingRuntime:
    def __init__(self, *, record: object | None = None) -> None:
        self.record = record

    async def dispatch(self, job: object) -> object:
        del job
        raise RuntimeError("private-dispatch-token")

    def get_record(self, worker_job_id: str) -> object:
        del worker_job_id
        if self.record is None:
            raise RegistryError("unknown dispatched worker job")
        return self.record

    async def reconcile(self) -> None:
        return None

    async def result(self, worker_job_id: str) -> object:
        del worker_job_id
        raise RuntimeError("private-result-token")


class _BoundaryBackend(DistributedApplicationBuildLifecycleBackend):
    def __init__(
        self,
        release: ApplicationRelease,
        target: BuildTargetState,
        bindings: InMemoryRunWorkspaceBindingRepository,
        runtime: _FailingRuntime,
    ) -> None:
        super().__init__(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            bindings,
            runtime,  # type: ignore[arg-type]
        )
        self._test_release = release
        self._test_target = target

    async def _release_target_for_run(
        self,
        run_id: str,
    ) -> tuple[ApplicationRelease, BuildTargetState]:
        assert run_id == self._test_target.run_id
        return self._test_release, self._test_target

    def _node_id(self, record: object) -> None:
        del record
        return None


def _fixture() -> tuple[
    ApplicationRelease,
    BuildTargetState,
    KernelExecutionRequest,
    RunWorkspaceBinding,
]:
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    snapshot_id = new_id("workspace_snapshot")
    task_id = new_id("task")
    run_id = new_id("run")
    target = BuildTarget(
        target_id="linux-x64",
        os_name="linux",
        architecture="x86_64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.tar.gz",
    )
    target_state = BuildTargetState(
        target=target,
        status=BuildTargetStatus.RUNNING,
        task_id=task_id,
        run_id=run_id,
    )
    release = ApplicationRelease(
        application_id="boundary-test",
        display_name="Boundary Test",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_snapshot_id=snapshot_id,
        workspace_content_checksum="a" * 64,
        source_revision="boundary-source",
        build_specification=BuildSpecification(
            command=("python", "-m", "build"),
            targets=(target,),
        ),
        creator_ref="user:test",
        targets=(target_state,),
    )
    request = KernelExecutionRequest(
        run_id=run_id,
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(
            correlation_id="distributed-build-boundary",
            project_id=project_id,
        ),
    )
    binding = RunWorkspaceBinding(
        run_id=run_id,
        task_id=task_id,
        workspace_id=workspace_id,
        workspace_snapshot_id=snapshot_id,
        content_checksum=release.workspace_content_checksum,
    )
    return release, target_state, request, binding


def test_unexpected_dispatch_failure_is_contained_and_terminal() -> None:
    async def scenario() -> None:
        release, target, request, binding = _fixture()
        bindings = InMemoryRunWorkspaceBindingRepository()
        await bindings.bind(binding)
        backend = _BoundaryBackend(release, target, bindings, _FailingRuntime())

        with pytest.raises(ContractError) as caught:
            await backend.start(request)

        assert caught.value.code is ErrorCode.BACKEND_ERROR
        assert caught.value.retryable is False
        assert "private-dispatch-token" not in str(caught.value)
        assert isinstance(caught.value.__cause__, RuntimeError)

    asyncio.run(scenario())


def test_unexpected_result_failure_is_contained_and_terminal() -> None:
    async def scenario() -> None:
        release, target, request, _binding = _fixture()
        record = SimpleNamespace(
            state=DispatchState.TERMINAL,
            snapshot=ExecutionSnapshot(run_id=request.run_id, status=RunStatus.SUCCEEDED),
            worker_id=new_id("worker"),
            job=SimpleNamespace(worker_job_id=new_id("worker_job"), execution=request),
        )
        backend = _BoundaryBackend(
            release,
            target,
            InMemoryRunWorkspaceBindingRepository(),
            _FailingRuntime(record=record),
        )

        with pytest.raises(ContractError) as caught:
            await backend.get(request.run_id, request.context)

        assert caught.value.code is ErrorCode.BACKEND_ERROR
        assert caught.value.retryable is False
        assert "private-result-token" not in str(caught.value)
        assert isinstance(caught.value.__cause__, RuntimeError)

    asyncio.run(scenario())
