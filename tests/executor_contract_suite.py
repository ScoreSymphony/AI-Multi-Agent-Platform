from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionStatus,
    Executor,
)


class ExecutorContractSuite:
    """Reusable backend-neutral contract tests for Executor implementations.

    Concrete test classes provide an executor and isolated workspace. Future
    Forge or other executor adapters can reuse this suite by subclassing it.
    Implementations used with this suite must expose the deterministic contract
    test actions: echo, fail, sleep, and write_artifact.
    """

    def build_executor(self, tmp_path: Path) -> tuple[Executor, str]:
        raise NotImplementedError

    @staticmethod
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

    def test_success_and_identity_preservation(self, tmp_path: Path) -> None:
        executor, workspace = self.build_executor(tmp_path)
        result = asyncio.run(executor.execute(self.request(workspace, "echo", text="hello")))
        assert result.status is ExecutionStatus.SUCCEEDED
        assert result.stdout == "hello"
        assert result.task_id == "task-1"
        assert result.run_id == "run-1"
        assert result.correlation_id == "corr-1"
        assert result.step_id == "step-1"

    def test_controlled_failure_is_canonical(self, tmp_path: Path) -> None:
        executor, workspace = self.build_executor(tmp_path)
        result = asyncio.run(
            executor.execute(self.request(workspace, "fail", message="boom", code=7))
        )
        assert result.status is ExecutionStatus.FAILED
        assert result.result_code == 7
        assert result.error is not None
        assert result.error.category is ExecutionErrorCategory.EXECUTION_FAILED

    def test_timeout_is_deterministic(self, tmp_path: Path) -> None:
        executor, workspace = self.build_executor(tmp_path)
        request = ExecutionRequest(
            task_id="task-1",
            run_id="run-1",
            correlation_id="corr-1",
            action="sleep",
            workspace=workspace,
            arguments={"seconds": 0.05},
            timeout_seconds=0.001,
        )
        result = asyncio.run(executor.execute(request))
        assert result.status is ExecutionStatus.TIMED_OUT
        assert result.error is not None
        assert result.error.category is ExecutionErrorCategory.TIMEOUT

    def test_cancellation_is_deterministic(self, tmp_path: Path) -> None:
        executor, workspace = self.build_executor(tmp_path)
        token = CancellationToken()
        token.cancel()
        request = ExecutionRequest(
            task_id="task-1",
            run_id="run-1",
            correlation_id="corr-1",
            action="sleep",
            workspace=workspace,
            cancellation=token,
        )
        result = asyncio.run(executor.execute(request))
        assert result.status is ExecutionStatus.CANCELLED
        assert result.error is not None
        assert result.error.category is ExecutionErrorCategory.CANCELLED

    def test_unsupported_capability_is_canonical(self, tmp_path: Path) -> None:
        executor, workspace = self.build_executor(tmp_path)
        result = asyncio.run(executor.execute(self.request(workspace, "unsupported-action")))
        assert result.status is ExecutionStatus.FAILED
        assert result.error is not None
        assert result.error.category is ExecutionErrorCategory.UNSUPPORTED_CAPABILITY

    def test_missing_workspace_is_canonical_failure(self, tmp_path: Path) -> None:
        executor, _ = self.build_executor(tmp_path)
        result = asyncio.run(executor.execute(self.request("missing-workspace", "echo")))
        assert result.status is ExecutionStatus.FAILED
        assert result.error is not None
        assert result.error.category is ExecutionErrorCategory.WORKSPACE_ERROR

    def test_workspace_traversal_is_blocked(self, tmp_path: Path) -> None:
        executor, _ = self.build_executor(tmp_path)
        result = asyncio.run(executor.execute(self.request("../outside", "echo")))
        assert result.status is ExecutionStatus.FAILED
        assert result.error is not None
        assert result.error.category is ExecutionErrorCategory.WORKSPACE_ERROR

    def test_artifact_evidence_and_write_boundary(self, tmp_path: Path) -> None:
        executor, workspace = self.build_executor(tmp_path)
        result = asyncio.run(
            executor.execute(
                self.request(
                    workspace,
                    "write_artifact",
                    path="out/result.txt",
                    content="ok",
                )
            )
        )
        assert result.status is ExecutionStatus.SUCCEEDED
        assert len(result.artifacts) == 1
        assert result.artifacts[0].relative_path == "out/result.txt"

        escaped = asyncio.run(
            executor.execute(
                self.request(
                    workspace,
                    "write_artifact",
                    path="../../escape.txt",
                    content="no",
                )
            )
        )
        assert escaped.status is ExecutionStatus.FAILED
        assert escaped.error is not None
        assert escaped.error.category is ExecutionErrorCategory.INVALID_REQUEST
        assert not (tmp_path / "escape.txt").exists()
