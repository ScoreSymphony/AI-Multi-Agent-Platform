from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.adapters.agent_sandbox import (
    AgentSandboxArtifact,
    AgentSandboxClientRequest,
    AgentSandboxClientResult,
    AgentSandboxExecutionStatus,
    AgentSandboxExecutor,
    AgentSandboxHealth,
)
from ai_multi_agent_platform.execution import (
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionStatus,
)

_SECRET = "ISSUE798-PROVIDER-SECRET-CANARY"


class SecretBearingFailureClient:
    async def execute(
        self,
        request: AgentSandboxClientRequest,
    ) -> AgentSandboxClientResult:
        raise RuntimeError(f"provider transport failed with token={_SECRET}")

    async def cancel(self, request_ref: str) -> None:
        return

    async def health(self) -> AgentSandboxHealth:
        raise RuntimeError(f"provider health URL contained token={_SECRET}")


class SecretBearingResultFailureClient:
    def __init__(self, *, error_code: str) -> None:
        self._error_code = error_code

    async def execute(
        self,
        request: AgentSandboxClientRequest,
    ) -> AgentSandboxClientResult:
        return AgentSandboxClientResult(
            status=AgentSandboxExecutionStatus.FAILED,
            sandbox_id="sandbox-provider-safe-id",
            result_code=1,
            output={"provider_diagnostic": _SECRET},
            stdout=_SECRET,
            stderr=f"provider stderr token={_SECRET}",
            artifacts=(AgentSandboxArtifact(relative_path=f"{_SECRET}.txt"),),
            started_at=f"invalid-provider-start-{_SECRET}",
            finished_at=f"invalid-provider-finish-{_SECRET}",
            duration_seconds=123.0,
            resources={"provider_diagnostic": _SECRET},
            error_code=self._error_code,
            error_message=f"provider error token={_SECRET}",
            retryable=True,
            metadata={"secret": _SECRET},
        )

    async def cancel(self, request_ref: str) -> None:
        return

    async def health(self) -> AgentSandboxHealth:
        return AgentSandboxHealth(healthy=True)


def _executor(tmp_path: Path) -> AgentSandboxExecutor:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    return AgentSandboxExecutor(
        SecretBearingFailureClient(),
        tmp_path / "workspaces",
        capabilities=("echo",),
    )


def _result_failure_executor(
    tmp_path: Path,
    *,
    error_code: str,
) -> AgentSandboxExecutor:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    return AgentSandboxExecutor(
        SecretBearingResultFailureClient(error_code=error_code),
        tmp_path / "workspaces",
        capabilities=("echo",),
    )


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        correlation_id="corr-1",
        action="echo",
        workspace="run-1",
    )


def test_provider_exception_text_is_not_copied_into_execution_evidence(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    result = asyncio.run(executor.execute(_request()))

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INTERNAL
    assert result.error.retryable is True
    assert result.error.message == "Agent-Sandbox provider execution failed"
    retained = f"{result.stderr} {result.error.message} {result.adapter_metadata}"
    assert _SECRET not in retained


def test_provider_health_exception_text_is_not_copied_into_descriptor(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    descriptor = asyncio.run(executor.health())

    assert descriptor.healthy is False
    assert descriptor.metadata["health_error"] == "provider health check failed"
    assert descriptor.metadata["health_exception_type"] == "RuntimeError"
    assert _SECRET not in str(descriptor.metadata)


def test_returned_provider_infrastructure_failure_is_redacted_fail_closed(
    tmp_path: Path,
) -> None:
    executor = _result_failure_executor(tmp_path, error_code="sandbox_unavailable")
    result = asyncio.run(executor.execute(_request()))

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INTERNAL
    assert result.error.retryable is True
    assert result.error.message == "Agent-Sandbox provider execution failed"
    assert result.error.details == {"agent_sandbox_error_code": "sandbox_unavailable"}
    assert result.output == {}
    assert result.stdout == ""
    assert result.stderr == "Agent-Sandbox provider execution failed"
    assert result.artifacts == ()
    assert result.resources == {}
    assert result.duration_seconds != 123.0
    assert result.adapter_metadata["agent_sandbox"]["error_code"] == "sandbox_unavailable"
    assert _SECRET not in repr(result)


def test_unknown_provider_error_code_is_not_persisted_or_exposed(tmp_path: Path) -> None:
    executor = _result_failure_executor(tmp_path, error_code=f"unknown-{_SECRET}")
    result = asyncio.run(executor.execute(_request()))

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INTERNAL
    assert result.error.message == "Agent-Sandbox provider execution failed"
    assert result.error.details == {}
    assert "error_code" not in result.adapter_metadata["agent_sandbox"]
    assert _SECRET not in repr(result)
