from __future__ import annotations

import asyncio
from pathlib import Path

from executor_contract_suite import ExecutorContractSuite

from ai_multi_agent_platform.adapters.agent_sandbox import (
    AGENT_SANDBOX_EVALUATED_REVISION,
    AgentSandboxArtifact,
    AgentSandboxClientRequest,
    AgentSandboxClientResult,
    AgentSandboxExecutionStatus,
    AgentSandboxExecutor,
    AgentSandboxHealth,
    AgentSandboxSecurityProfile,
)
from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
)


class FakeAgentSandboxClient:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.requests: list[AgentSandboxClientRequest] = []

    async def execute(
        self,
        request: AgentSandboxClientRequest,
    ) -> AgentSandboxClientResult:
        self.requests.append(request)
        if request.action == "echo":
            text = str(request.arguments.get("text", ""))
            return AgentSandboxClientResult(
                status=AgentSandboxExecutionStatus.SUCCEEDED,
                sandbox_id="sandbox-provider-1",
                session_id="session-provider-1",
                result_code=0,
                output={"text": text},
                stdout=text,
            )
        if request.action == "fail":
            message = str(request.arguments.get("message", "controlled failure"))
            code_value = request.arguments.get("code", 1)
            code = code_value if isinstance(code_value, int) else 1
            return AgentSandboxClientResult(
                status=AgentSandboxExecutionStatus.FAILED,
                sandbox_id="sandbox-provider-2",
                result_code=code,
                stderr=message,
                error_code="execution_failed",
                error_message=message,
            )
        if request.action == "sleep":
            seconds_value = request.arguments.get("seconds", 0.0)
            seconds = float(seconds_value) if isinstance(seconds_value, (int, float)) else 0.0
            await asyncio.sleep(seconds)
            return AgentSandboxClientResult(
                status=AgentSandboxExecutionStatus.SUCCEEDED,
                sandbox_id="sandbox-provider-3",
                result_code=0,
                output={"slept_seconds": seconds},
            )
        if request.action == "write_artifact":
            relative = str(request.arguments.get("path", "artifact.txt"))
            workspace = Path(request.workspace_path)
            destination = (workspace / relative).resolve()
            if destination != workspace and workspace not in destination.parents:
                return AgentSandboxClientResult(
                    status=AgentSandboxExecutionStatus.FAILED,
                    sandbox_id="sandbox-provider-4",
                    error_code="invalid_request",
                    error_message="artifact path escapes execution workspace",
                )
            content = str(request.arguments.get("content", ""))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            return AgentSandboxClientResult(
                status=AgentSandboxExecutionStatus.SUCCEEDED,
                sandbox_id="sandbox-provider-4",
                snapshot_id="snapshot-provider-1",
                result_code=0,
                artifacts=(
                    AgentSandboxArtifact(
                        relative_path=relative,
                        media_type="text/plain",
                        size_bytes=len(content.encode("utf-8")),
                    ),
                ),
            )
        return AgentSandboxClientResult(
            status=AgentSandboxExecutionStatus.FAILED,
            error_code="unsupported_capability",
            error_message=f"unsupported action: {request.action}",
        )

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> AgentSandboxHealth:
        return AgentSandboxHealth(
            healthy=True,
            capabilities=("echo", "write_artifact", "fail", "sleep"),
            metadata={"transport": "fake"},
        )


class TestAgentSandboxExecutorContract(ExecutorContractSuite):
    def build_executor(self, tmp_path: Path) -> tuple[Executor, str]:
        workspace = tmp_path / "workspaces" / "run-1"
        workspace.mkdir(parents=True)
        return (
            AgentSandboxExecutor(
                FakeAgentSandboxClient(),
                tmp_path / "workspaces",
                capabilities=("echo", "write_artifact", "fail", "sleep"),
            ),
            "run-1",
        )


def _executor(
    tmp_path: Path,
    *,
    security_profile: AgentSandboxSecurityProfile | None = None,
) -> tuple[AgentSandboxExecutor, FakeAgentSandboxClient]:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = FakeAgentSandboxClient()
    executor = AgentSandboxExecutor(
        client,
        tmp_path / "workspaces",
        capabilities=("echo", "write_artifact", "fail", "sleep"),
        security_profile=security_profile,
    )
    return executor, client


def test_provider_ids_are_namespaced_and_canonical_ids_are_preserved(
    tmp_path: Path,
) -> None:
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
    assert result.adapter_metadata["agent_sandbox"]["sandbox_id"] == "sandbox-provider-1"
    assert result.adapter_metadata["agent_sandbox"]["session_id"] == "session-provider-1"


def test_provider_request_refs_are_private_and_unique_per_execution(
    tmp_path: Path,
) -> None:
    executor, client = _executor(tmp_path)
    request = ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        correlation_id="corr-1",
        action="echo",
        workspace="run-1",
    )

    async def scenario() -> tuple[ExecutionResult, ExecutionResult]:
        first, second = await asyncio.gather(
            executor.execute(request),
            executor.execute(request),
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first.status is ExecutionStatus.SUCCEEDED
    assert second.status is ExecutionStatus.SUCCEEDED
    refs = [provider_request.request_ref for provider_request in client.requests]
    assert len(refs) == 2
    assert len(set(refs)) == 2
    assert "run-1" not in refs


def test_security_profile_defaults_to_no_internet_access(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
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
    assert result.status is ExecutionStatus.SUCCEEDED
    assert client.requests[0].security_profile.allow_internet_access is False
    assert executor.descriptor.metadata["internet_access_default"] is False


def test_explicit_network_profile_remains_provider_private(tmp_path: Path) -> None:
    security_profile = AgentSandboxSecurityProfile(
        allow_internet_access=False,
        allow_out=("api.example.test",),
        deny_out=("0.0.0.0/0", "::/0"),
    )
    executor, client = _executor(tmp_path, security_profile=security_profile)
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
    assert result.status is ExecutionStatus.SUCCEEDED
    assert client.requests[0].security_profile == security_profile
    assert "security_profile" not in result.output
    assert "allow_out" not in result.adapter_metadata["agent_sandbox"]


def test_health_is_translated_without_making_provider_canonical(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    descriptor = asyncio.run(executor.health())
    assert descriptor.executor_id == "agent-sandbox"
    assert descriptor.healthy is True
    assert "echo" in descriptor.capabilities
    assert descriptor.metadata["transport"] == "fake"
    assert descriptor.metadata["canonical_lifecycle_owner"] == "platform"
    assert descriptor.metadata["evaluated_revision"] == AGENT_SANDBOX_EVALUATED_REVISION


def test_inflight_cancellation_is_forwarded_once_by_private_request_ref(
    tmp_path: Path,
) -> None:
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
    assert len(client.requests) == 1
    assert client.cancelled == [client.requests[0].request_ref]
    assert client.cancelled[0] != "run-1"


def test_timeout_is_forwarded_once_by_private_request_ref(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                run_id="run-1",
                correlation_id="corr-1",
                action="sleep",
                workspace="run-1",
                arguments={"seconds": 1.0},
                timeout_seconds=0.001,
            )
        )
    )
    assert result.status is ExecutionStatus.TIMED_OUT
    assert len(client.requests) == 1
    assert client.cancelled == [client.requests[0].request_ref]
    assert client.cancelled[0] != "run-1"


def test_provider_cannot_report_artifact_outside_canonical_workspace(
    tmp_path: Path,
) -> None:
    class EscapingClient(FakeAgentSandboxClient):
        async def execute(
            self,
            request: AgentSandboxClientRequest,
        ) -> AgentSandboxClientResult:
            self.requests.append(request)
            return AgentSandboxClientResult(
                status=AgentSandboxExecutionStatus.SUCCEEDED,
                sandbox_id="sandbox-escape",
                artifacts=(AgentSandboxArtifact(relative_path="../../escape.txt"),),
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    executor = AgentSandboxExecutor(
        EscapingClient(),
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
    assert "outside the execution workspace" in result.error.message


def test_backend_unavailability_is_normalized_without_adapter_retry(
    tmp_path: Path,
) -> None:
    class UnavailableClient(FakeAgentSandboxClient):
        async def execute(
            self,
            request: AgentSandboxClientRequest,
        ) -> AgentSandboxClientResult:
            self.requests.append(request)
            return AgentSandboxClientResult(
                status=AgentSandboxExecutionStatus.FAILED,
                sandbox_id="sandbox-unavailable",
                error_code="sandbox_unavailable",
                error_message="sandbox provider unavailable",
                retryable=True,
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = UnavailableClient()
    executor = AgentSandboxExecutor(
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
    assert len(client.requests) == 1


def test_environment_projection_fails_closed_without_provider_dispatch(
    tmp_path: Path,
) -> None:
    executor, client = _executor(tmp_path)
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                run_id="run-1",
                correlation_id="corr-1",
                action="echo",
                workspace="run-1",
                environment={"SYNTHETIC_SECRET": "issue-798-canary"},
            )
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INVALID_REQUEST
    assert "#34-safe" in result.error.message
    assert client.requests == []


def test_untrusted_provider_metadata_is_filtered_before_canonical_evidence(
    tmp_path: Path,
) -> None:
    class MetadataClient(FakeAgentSandboxClient):
        async def execute(
            self,
            request: AgentSandboxClientRequest,
        ) -> AgentSandboxClientResult:
            self.requests.append(request)
            return AgentSandboxClientResult(
                status=AgentSandboxExecutionStatus.SUCCEEDED,
                sandbox_id="sandbox-metadata",
                metadata={
                    "transport": "fake",
                    "image_digest": "sha256:abc",
                    "secret": "do-not-persist",
                },
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = MetadataClient()
    executor = AgentSandboxExecutor(
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

    metadata = result.adapter_metadata["agent_sandbox"]
    assert metadata["transport"] == "fake"
    assert metadata["image_digest"] == "sha256:abc"
    assert "secret" not in metadata
