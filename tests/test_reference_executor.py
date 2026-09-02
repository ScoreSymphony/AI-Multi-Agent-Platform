from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionStatus,
    Executor,
    ExecutorRegistry,
    ReferenceExecutor,
)


def request(workspace: str, action: str, **arguments: object) -> ExecutionRequest:
    return ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        step_id="step-1",
        correlation_id="corr-1",
        action=action,
        workspace=workspace,
        arguments=arguments,  # type: ignore[arg-type]
    )


def build_executor(tmp_path: Path) -> tuple[Executor, str]:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    return ReferenceExecutor(tmp_path / "workspaces"), "run-1"


def test_success_and_identity_preservation(tmp_path: Path) -> None:
    executor, workspace = build_executor(tmp_path)
    result = asyncio.run(executor.execute(request(workspace, "echo", text="hello")))
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.stdout == "hello"
    assert result.task_id == "task-1"
    assert result.run_id == "run-1"
    assert result.correlation_id == "corr-1"
    assert result.step_id == "step-1"


def test_controlled_failure_is_canonical(tmp_path: Path) -> None:
    executor, workspace = build_executor(tmp_path)
    result = asyncio.run(executor.execute(request(workspace, "fail", message="boom", code=7)))
    assert result.status is ExecutionStatus.FAILED
    assert result.result_code == 7
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.EXECUTION_FAILED


def test_timeout_is_deterministic(tmp_path: Path) -> None:
    executor, workspace = build_executor(tmp_path)
    req = ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        correlation_id="corr-1",
        action="sleep",
        workspace=workspace,
        arguments={"seconds": 0.05},
        timeout_seconds=0.001,
    )
    result = asyncio.run(executor.execute(req))
    assert result.status is ExecutionStatus.TIMED_OUT
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.TIMEOUT


def test_cancellation_is_deterministic(tmp_path: Path) -> None:
    executor, workspace = build_executor(tmp_path)
    token = CancellationToken()
    token.cancel()
    req = ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        correlation_id="corr-1",
        action="sleep",
        workspace=workspace,
        cancellation=token,
    )
    result = asyncio.run(executor.execute(req))
    assert result.status is ExecutionStatus.CANCELLED


def test_unsupported_capability(tmp_path: Path) -> None:
    executor, workspace = build_executor(tmp_path)
    result = asyncio.run(executor.execute(request(workspace, "shell")))
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.UNSUPPORTED_CAPABILITY


def test_missing_workspace_is_canonical_failure(tmp_path: Path) -> None:
    executor = ReferenceExecutor(tmp_path / "root")
    result = asyncio.run(executor.execute(request("missing", "echo")))
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.WORKSPACE_ERROR


def test_workspace_traversal_is_blocked(tmp_path: Path) -> None:
    executor, _ = build_executor(tmp_path)
    result = asyncio.run(executor.execute(request("../outside", "echo")))
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.WORKSPACE_ERROR


def test_artifact_collection_and_write_boundary(tmp_path: Path) -> None:
    executor, workspace = build_executor(tmp_path)
    result = asyncio.run(
        executor.execute(request(workspace, "write_artifact", path="out/result.txt", content="ok"))
    )
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.artifacts[0].relative_path == "out/result.txt"
    assert (tmp_path / "workspaces" / workspace / "out" / "result.txt").read_text() == "ok"

    escaped = asyncio.run(
        executor.execute(request(workspace, "write_artifact", path="../../escape.txt", content="no"))
    )
    assert escaped.error is not None
    assert escaped.error.category is ExecutionErrorCategory.INVALID_REQUEST
    assert not (tmp_path / "escape.txt").exists()


def test_registry_selection_is_configuration_driven(tmp_path: Path) -> None:
    executor = ReferenceExecutor(tmp_path)
    registry, default = ExecutorRegistry.from_config({"local": executor}, default="local")
    assert default is executor
    assert registry.select("local") is executor


def test_health_and_capability_metadata(tmp_path: Path) -> None:
    executor = ReferenceExecutor(tmp_path)
    descriptor = asyncio.run(executor.health())
    assert descriptor.executor_id == "reference"
    assert "echo" in descriptor.capabilities
    assert descriptor.metadata["arbitrary_commands"] is False
