from __future__ import annotations

import asyncio
from pathlib import Path

from executor_contract_suite import ExecutorContractSuite

from ai_multi_agent_platform.adapters.forge import (
    ForgeArtifact,
    ForgeClientRequest,
    ForgeClientResult,
    ForgeExecutionStatus,
    ForgeExecutor,
    ForgeHealth,
)
from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
)


class FakeForgeClient:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.requests: list[ForgeClientRequest] = []

    async def execute(self, request: ForgeClientRequest) -> ForgeClientResult:
        self.requests.append(request)
        if request.action == "echo":
            text = str(request.arguments.get("text", ""))
            return ForgeClientResult(
                status=ForgeExecutionStatus.SUCCEEDED,
                execution_id="forge-exec-1",
                result_code=0,
                output={"text": text},
                stdout=text,
            )
        if request.action == "fail":
            message = str(request.arguments.get("message", "controlled failure"))
            code_value = request.arguments.get("code", 1)
            code = code_value if isinstance(code_value, int) else 1
            return ForgeClientResult(
                status=ForgeExecutionStatus.FAILED,
                execution_id="forge-exec-2",
                result_code=code,
                stderr=message,
                error_code="execution_failed",
                error_message=message,
            )
        if request.action == "sleep":
            seconds_value = request.arguments.get("seconds", 0.0)
            seconds = float(seconds_value) if isinstance(seconds_value, (int, float)) else 0.0
            await asyncio.sleep(seconds)
            return ForgeClientResult(
                status=ForgeExecutionStatus.SUCCEEDED,
                execution_id="forge-exec-3",
                result_code=0,
                output={"slept_seconds": seconds},
            )
        if request.action == "write_artifact":
            relative = str(request.arguments.get("path", "artifact.txt"))
            workspace = Path(request.workspace_path)
            destination = (workspace / relative).resolve()
            if destination != workspace and workspace not in destination.parents:
                return ForgeClientResult(
                    status=ForgeExecutionStatus.FAILED,
                    execution_id="forge-exec-4",
                    error_code="invalid_request",
                    error_message="artifact path escapes execution workspace",
                )
            content = str(request.arguments.get("content", ""))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            return ForgeClientResult(
                status=ForgeExecutionStatus.SUCCEEDED,
                execution_id="forge-exec-4",
                result_code=0,
                artifacts=(
                    ForgeArtifact(
                        relative_path=relative,
                        media_type="text/plain",
                        size_bytes=len(content.encode("utf-8")),
                    ),
                ),
            )
        return ForgeClientResult(
            status=ForgeExecutionStatus.FAILED,
            error_code="unsupported_capability",
            error_message=f"unsupported action: {request.action}",
        )

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> ForgeHealth:
        return ForgeHealth(
            healthy=True,
            capabilities=("echo", "write_artifact", "fail", "sleep"),
            metadata={"transport": "fake"},
        )


class TestForgeExecutorContract(ExecutorContractSuite):
    def build_executor(self, tmp_path: Path) -> tuple[Executor, str]:
        workspace = tmp_path / "workspaces" / "run-1"
        workspace.mkdir(parents=True)
        return (
            ForgeExecutor(
                FakeForgeClient(),
                tmp_path / "workspaces",
                capabilities=("echo", "write_artifact", "fail", "sleep"),
            ),
            "run-1",
        )


def _executor(tmp_path: Path) -> tuple[ForgeExecutor, FakeForgeClient]:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = FakeForgeClient()
    executor = ForgeExecutor(
        client,
        tmp_path / "workspaces",
        capabilities=("echo", "write_artifact", "fail", "sleep"),
    )
    return executor, client


def test_forge_ids_are_namespaced_and_canonical_ids_are_preserved(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(
                task_id="task-platform",
                run_id="run-platform",
                step_id="step-platform",
                correlation_id="corr-platform",
                action="echo",
                workspace="run-1",
                arguments={"text": "hello"},
            )
        )
    )
    assert result.task_id == "task-platform"
    assert result.run_id == "run-platform"
    assert result.step_id == "step-platform"
    assert result.correlation_id == "corr-platform"
    assert result.adapter_metadata["forge"]["execution_id"] == "forge-exec-1"


def test_health_is_translated_without_making_forge_canonical(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    descriptor = asyncio.run(executor.health())
    assert descriptor.executor_id == "forge"
    assert descriptor.healthy is True
    assert "echo" in descriptor.capabilities
    assert descriptor.metadata["transport"] == "fake"
    assert descriptor.metadata["canonical_lifecycle_owner"] == "platform"


def test_inflight_cancellation_is_forwarded_by_run_reference(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
    token = CancellationToken()
    request = ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        correlation_id="corr-1",
        action="sleep",
        workspace="run-1",
        arguments={"seconds": 1.0},
        cancellation=token,
    )

    async def scenario() -> ExecutionResult:
        execution = asyncio.create_task(executor.execute(request))
        await asyncio.sleep(0.01)
        token.cancel()
        return await execution

    result = asyncio.run(scenario())
    assert result.status is ExecutionStatus.CANCELLED
    assert "run-1" in client.cancelled


def test_backend_availability_signal_is_normalized_not_retried(tmp_path: Path) -> None:
    class UnavailableForgeClient(FakeForgeClient):
        async def execute(self, request: ForgeClientRequest) -> ForgeClientResult:
            self.requests.append(request)
            return ForgeClientResult(
                status=ForgeExecutionStatus.FAILED,
                execution_id="forge-unavailable",
                error_code="unavailable",
                error_message="backend unavailable",
                retryable=True,
                retry_after_seconds=30.0,
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = UnavailableForgeClient()
    executor = ForgeExecutor(
        client,
        tmp_path / "workspaces",
        capabilities=("echo",),
    )
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                run_id="run-1",
                correlation_id="corr-1",
                action="echo",
                workspace="run-1",
            )
        )
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INTERNAL
    assert result.error.retryable is True
    assert result.adapter_metadata["forge"]["retry_after_seconds"] == 30.0
    assert len(client.requests) == 1
