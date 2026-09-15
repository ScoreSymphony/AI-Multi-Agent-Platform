from __future__ import annotations

import asyncio
from pathlib import Path

from executor_contract_suite import ExecutorContractSuite

from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionRequest,
    ExecutionStatus,
    Executor,
    ExecutorRegistry,
    ReferenceExecutor,
)


class TestReferenceExecutorContract(ExecutorContractSuite):
    def build_executor(self, tmp_path: Path) -> tuple[Executor, str]:
        workspace = tmp_path / "workspaces" / "run-1"
        workspace.mkdir(parents=True)
        return ReferenceExecutor(tmp_path / "workspaces"), "run-1"


def test_registry_selection_is_configuration_driven(tmp_path: Path) -> None:
    executor = ReferenceExecutor(tmp_path)
    registry, default = ExecutorRegistry.from_config(
        {"local": executor},
        default="local",
    )
    assert default is executor
    assert registry.select("local") is executor


def test_health_and_capability_metadata(tmp_path: Path) -> None:
    executor = ReferenceExecutor(tmp_path)
    descriptor = asyncio.run(executor.health())
    assert descriptor.executor_id == "reference"
    assert "echo" in descriptor.capabilities
    assert descriptor.metadata["arbitrary_commands"] is False


def test_write_artifact_preserves_exact_utf8_bytes(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "run-1"
    workspace.mkdir(parents=True)
    executor = ReferenceExecutor(workspace_root)
    content = "line one\nline two\n"
    request = ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        step_id="step-1",
        correlation_id="corr-1",
        action="write_artifact",
        workspace="run-1",
        arguments={"path": "evidence.txt", "content": content},
    )

    result = asyncio.run(executor.execute(request))

    expected = content.encode("utf-8")
    assert result.status is ExecutionStatus.SUCCEEDED
    assert (workspace / "evidence.txt").read_bytes() == expected
    assert result.artifacts[0].size_bytes == len(expected)


def test_inflight_cancellation_is_acknowledged_by_reference_executor(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "run-1"
    workspace.mkdir(parents=True)
    executor = ReferenceExecutor(workspace_root)
    token = CancellationToken()
    request = ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        step_id="step-1",
        correlation_id="corr-1",
        action="sleep",
        workspace="run-1",
        arguments={"seconds": 1.0},
        cancellation=token,
    )

    async def scenario() -> None:
        execution = asyncio.create_task(executor.execute(request))
        await asyncio.sleep(0.01)
        token.cancel()
        result = await execution

        assert result.status is ExecutionStatus.CANCELLED
        assert result.error is not None
        assert result.error.category.value == "cancelled"
        assert result.task_id == request.task_id
        assert result.run_id == request.run_id
        assert result.step_id == request.step_id
        assert result.correlation_id == request.correlation_id

    asyncio.run(scenario())
