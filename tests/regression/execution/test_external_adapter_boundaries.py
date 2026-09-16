from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.agent_sandbox import (
    AgentSandboxClientRequest,
    AgentSandboxClientResult,
    AgentSandboxExecutionStatus,
    AgentSandboxExecutor,
    AgentSandboxHealth,
)
from ai_multi_agent_platform.adapters.openshell import (
    OpenShellClientRequest,
    OpenShellClientResult,
    OpenShellExecutionStatus,
    OpenShellExecutor,
    OpenShellHealth,
)
from ai_multi_agent_platform.adapters.swe_rex import (
    SwerexClientRequest,
    SwerexClientResult,
    SwerexExecutionStatus,
    SwerexExecutor,
    SwerexHealth,
)
from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionStatus,
)


class AgentSandboxBoundaryClient:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.started = asyncio.Event()
        self.cancelled: list[str] = []

    async def execute(self, request: AgentSandboxClientRequest) -> AgentSandboxClientResult:
        self.started.set()
        if self.failure is not None:
            raise self.failure
        await asyncio.Event().wait()
        return AgentSandboxClientResult(status=AgentSandboxExecutionStatus.SUCCEEDED)

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> AgentSandboxHealth:
        return AgentSandboxHealth(healthy=True)


class OpenShellBoundaryClient:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.started = asyncio.Event()
        self.cancelled: list[str] = []

    async def execute(self, request: OpenShellClientRequest) -> OpenShellClientResult:
        self.started.set()
        if self.failure is not None:
            raise self.failure
        await asyncio.Event().wait()
        return OpenShellClientResult(status=OpenShellExecutionStatus.SUCCEEDED)

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> OpenShellHealth:
        return OpenShellHealth(healthy=True)


class SwerexBoundaryClient:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.started = asyncio.Event()
        self.cancelled: list[str] = []

    async def execute(self, request: SwerexClientRequest) -> SwerexClientResult:
        self.started.set()
        if self.failure is not None:
            raise self.failure
        await asyncio.Event().wait()
        return SwerexClientResult(status=SwerexExecutionStatus.SUCCEEDED)

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> SwerexHealth:
        return SwerexHealth(healthy=True)


def _request(
    *, timeout_seconds: float | None = None, cancellation: CancellationToken | None = None
) -> ExecutionRequest:
    return ExecutionRequest(
        task_id="task-983-adapter-boundary",
        run_id="run-983",
        correlation_id="corr-983-adapter-boundary",
        action="execute",
        workspace="run-1",
        timeout_seconds=timeout_seconds,
        cancellation=cancellation,
    )


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    (root / "run-1").mkdir(parents=True)
    return root


@pytest.mark.parametrize("adapter", ["agent-sandbox", "openshell", "swe-rex"])
def test_external_executor_timeout_is_explicitly_retryable(tmp_path: Path, adapter: str) -> None:
    root = _workspace(tmp_path)
    if adapter == "agent-sandbox":
        client = AgentSandboxBoundaryClient()
        executor = AgentSandboxExecutor(client, root, capabilities=("execute",))
    elif adapter == "openshell":
        client = OpenShellBoundaryClient()
        executor = OpenShellExecutor(client, root, capabilities=("execute",))
    else:
        client = SwerexBoundaryClient()
        executor = SwerexExecutor(client, root, capabilities=("execute",))

    result = asyncio.run(executor.execute(_request(timeout_seconds=0.001)))

    assert result.status is ExecutionStatus.TIMED_OUT
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.TIMEOUT
    assert result.error.retryable is True
    assert len(client.cancelled) == 1


@pytest.mark.parametrize("adapter", ["agent-sandbox", "openshell", "swe-rex"])
def test_external_executor_unknown_sdk_failure_is_non_retryable_and_redacted(
    tmp_path: Path,
    adapter: str,
) -> None:
    root = _workspace(tmp_path)
    secret = "adapter-provider-sensitive-value"
    failure = RuntimeError(f"sdk payload bearer={secret}")
    if adapter == "agent-sandbox":
        client = AgentSandboxBoundaryClient(failure=failure)
        executor = AgentSandboxExecutor(client, root, capabilities=("execute",))
    elif adapter == "openshell":
        client = OpenShellBoundaryClient(failure=failure)
        executor = OpenShellExecutor(client, root, capabilities=("execute",))
    else:
        client = SwerexBoundaryClient(failure=failure)
        executor = SwerexExecutor(client, root, capabilities=("execute",))

    result = asyncio.run(executor.execute(_request()))

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INTERNAL
    assert result.error.retryable is False
    assert secret not in result.error.message
    assert secret not in result.stderr


@pytest.mark.parametrize("adapter", ["agent-sandbox", "openshell", "swe-rex"])
def test_external_executor_propagates_task_cancellation_after_best_effort_cancel(
    tmp_path: Path,
    adapter: str,
) -> None:
    root = _workspace(tmp_path)
    if adapter == "agent-sandbox":
        client = AgentSandboxBoundaryClient()
        executor = AgentSandboxExecutor(client, root, capabilities=("execute",))
    elif adapter == "openshell":
        client = OpenShellBoundaryClient()
        executor = OpenShellExecutor(client, root, capabilities=("execute",))
    else:
        client = SwerexBoundaryClient()
        executor = SwerexExecutor(client, root, capabilities=("execute",))

    async def scenario() -> None:
        task = asyncio.create_task(executor.execute(_request()))
        await client.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(client.cancelled) == 1


@pytest.mark.parametrize("adapter", ["agent-sandbox", "openshell", "swe-rex"])
def test_canonical_cancellation_token_still_returns_cancelled_result(
    tmp_path: Path,
    adapter: str,
) -> None:
    root = _workspace(tmp_path)
    token = CancellationToken()
    if adapter == "agent-sandbox":
        client = AgentSandboxBoundaryClient()
        executor = AgentSandboxExecutor(client, root, capabilities=("execute",))
    elif adapter == "openshell":
        client = OpenShellBoundaryClient()
        executor = OpenShellExecutor(client, root, capabilities=("execute",))
    else:
        client = SwerexBoundaryClient()
        executor = SwerexExecutor(client, root, capabilities=("execute",))

    async def scenario():
        task = asyncio.create_task(executor.execute(_request(cancellation=token)))
        await client.started.wait()
        token.cancel()
        return await task

    result = asyncio.run(scenario())
    assert result.status is ExecutionStatus.CANCELLED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.CANCELLED
    assert len(client.cancelled) == 1


def test_swe_rex_provider_infrastructure_result_is_redacted(tmp_path: Path) -> None:
    secret = "swe-rex-sensitive-provider-value"

    class InfrastructureFailureClient(SwerexBoundaryClient):
        async def execute(self, request: SwerexClientRequest) -> SwerexClientResult:
            self.started.set()
            return SwerexClientResult(
                status=SwerexExecutionStatus.FAILED,
                deployment_id="deployment-visible",
                error_code="runtime_unavailable",
                error_message=f"runtime failed bearer={secret}",
                stderr=f"stderr bearer={secret}",
                stdout=f"stdout bearer={secret}",
                output={"secret": secret},
                resources={"credential": secret},
                retryable=True,
                metadata={"runtime_transport": "fake", "credential": secret},
            )

    root = _workspace(tmp_path)
    client = InfrastructureFailureClient()
    executor = SwerexExecutor(client, root, capabilities=("execute",))
    result = asyncio.run(executor.execute(_request()))

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.retryable is True
    assert result.error.message == "SWE-ReX provider execution failed"
    assert result.output == {}
    assert result.stdout == ""
    assert result.stderr == "SWE-ReX provider execution failed"
    assert result.resources == {}
    assert "credential" not in result.adapter_metadata["swe_rex"]
    assert secret not in repr(result)
