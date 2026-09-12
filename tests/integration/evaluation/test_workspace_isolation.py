from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionStatus,
)
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.evaluation import (
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationFixture,
    KernelEvaluationCaseExecutor,
    StaticEvaluationFixtureResolver,
    WorkspaceEvaluationIsolation,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceFile,
)


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="evaluation-workspace-test",
            owner_type="service",
            owner_id="evaluation-tests",
            project_id=project_id,
        ),
        actor_ref="service:evaluation-tests",
    )


def _attempt(case: EvaluationCase, repetition_index: int) -> EvaluationAttempt:
    return EvaluationAttempt(
        evaluation_run_id="evaluation_run_workspace",
        case_id=case.case_id,
        case_version=case.version,
        repetition_index=repetition_index,
        seed=100 + repetition_index,
    )


def test_workspace_isolation_rehydrates_fixture_without_cross_attempt_contamination(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        owner = OwnerRef(type="service", id="evaluation-tests")
        data_context = _data_context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        record = await files.create_file(
            b"clean fixture\n",
            data_context,
            content_type="text/plain",
        )
        fixture_file = WorkspaceFile(
            relative_path="fixture/input.txt",
            file_id=record.file_id,
            sha256=record.sha256,
        )
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        isolation = WorkspaceEvaluationIsolation(
            workspace_provider=workspaces,
            project_id=project_id,
            owner_ref=owner,
            fixture_resolver=StaticEvaluationFixtureResolver(
                (
                    EvaluationFixture(
                        fixture_id="fixture.clean",
                        files=(fixture_file,),
                    ),
                )
            ),
        )
        case = EvaluationCase(
            case_id="case.workspace-isolation",
            name="Workspace isolation",
            version="1",
            fixtures=("fixture.clean",),
        )

        first_attempt = _attempt(case, 0)
        await isolation.reset_case(case=case, attempt=first_attempt)
        first = await isolation.setup_case(case=case, attempt=first_attempt)
        assert first.workspace_materialization_id is not None
        first_root = workspaces.local_path(first.workspace_materialization_id)
        first_input = first_root / "fixture" / "input.txt"
        assert first_input.read_text() == "clean fixture\n"
        first_input.write_text("contaminated\n")
        await isolation.teardown_case(
            case=case,
            attempt=first_attempt,
            execution_context=first,
            succeeded=True,
        )
        with pytest.raises(ContractError):
            workspaces.local_path(first.workspace_materialization_id)

        second_attempt = _attempt(case, 1)
        await isolation.reset_case(case=case, attempt=second_attempt)
        second = await isolation.setup_case(case=case, attempt=second_attempt)
        assert second.workspace_materialization_id is not None
        second_root = workspaces.local_path(second.workspace_materialization_id)
        second_input = second_root / "fixture" / "input.txt"

        assert second.workspace_id != first.workspace_id
        assert second.workspace_snapshot_id != first.workspace_snapshot_id
        assert second.workspace_materialization_id != first.workspace_materialization_id
        assert second_input.read_text() == "clean fixture\n"
        await isolation.teardown_case(
            case=case,
            attempt=second_attempt,
            execution_context=second,
            succeeded=True,
        )

    asyncio.run(scenario())


def test_workspace_isolation_rejects_declared_fixture_without_resolver(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
        isolation = WorkspaceEvaluationIsolation(
            workspace_provider=workspaces,
            project_id=project_id,
            owner_ref=OwnerRef(type="service", id="evaluation-tests"),
        )
        case = EvaluationCase(
            case_id="case.fixture-contract",
            name="Fixture contract",
            version="1",
            fixtures=("fixture.required",),
        )
        attempt = _attempt(case, 0)

        with pytest.raises(ValueError, match="no fixture resolver"):
            await isolation.setup_case(case=case, attempt=attempt)

    asyncio.run(scenario())


class BindingAwareLifecycle(FakeLifecycleBackend):
    def __init__(self, bindings: InMemoryRunWorkspaceBindingRepository) -> None:
        super().__init__()
        self._bindings = bindings
        self.binding_seen_before_start = False

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        binding = await self._bindings.get(request.run_id)
        self.binding_seen_before_start = binding is not None
        handle = await super().start(request)
        self.complete(
            request.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"workspace_bound": self.binding_seen_before_start},
        )
        return handle


def test_kernel_evaluation_binds_workspace_snapshot_before_run_start() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        bindings = InMemoryRunWorkspaceBindingRepository()
        lifecycle = BindingAwareLifecycle(bindings)
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        executor = KernelEvaluationCaseExecutor(
            kernel=kernel,
            owner_type="service",
            owner_id="evaluation-tests",
            project_id=project_id,
            run_workspace_bindings=bindings,
            poll_interval_seconds=0.001,
        )
        case = EvaluationCase(
            case_id="case.bound-workspace",
            name="Bound workspace",
            version="1",
        )
        attempt = _attempt(case, 0)
        workspace_id = new_id("workspace")
        snapshot_id = new_id("workspace_snapshot")
        execution_context = EvaluationExecutionContext(
            attempt_id=attempt.attempt_id,
            project_id=project_id,
            owner_type="service",
            owner_id="evaluation-tests",
            workspace_id=workspace_id,
            workspace_snapshot_id=snapshot_id,
            workspace_content_checksum="a" * 64,
            workspace_materialization_id=new_id("materialization"),
            execution_workspace=new_id("materialization"),
        )

        observation = await executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=execution_context,
        )

        assert lifecycle.binding_seen_before_start is True
        assert observation.run_id is not None
        binding = await bindings.get(observation.run_id)
        assert binding is not None
        assert binding.workspace_id == workspace_id
        assert binding.workspace_snapshot_id == snapshot_id
        workspace_data = observation.data["workspace"]
        assert isinstance(workspace_data, dict)
        assert workspace_data["workspace_id"] == workspace_id
        assert workspace_data["workspace_snapshot_id"] == snapshot_id

    asyncio.run(scenario())
