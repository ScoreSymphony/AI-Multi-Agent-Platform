from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.adapters.agent_sandbox import (
    AgentSandboxClientRequest,
    AgentSandboxExecutor,
)
from ai_multi_agent_platform.execution import (
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionStatus,
)

_SECRET = "ISSUE798-PROVIDER-SECRET-CANARY"


class SecretBearingFailureClient:
    async def execute(self, request: AgentSandboxClientRequest) -> None:
        raise RuntimeError(f"provider transport failed with token={_SECRET}")

    async def cancel(self, request_ref: str) -> None:
        return

    async def health(self) -> None:
        raise RuntimeError(f"provider health URL contained token={_SECRET}")


def _executor(tmp_path: Path) -> AgentSandboxExecutor:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    return AgentSandboxExecutor(
        SecretBearingFailureClient(),
        tmp_path / "workspaces",
        capabilities=("echo",),
    )


def test_provider_exception_text_is_not_copied_into_execution_evidence(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
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
