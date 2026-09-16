from __future__ import annotations

import asyncio
from pathlib import Path

from executor_contract_suite import ExecutorContractSuite

from ai_multi_agent_platform.adapters.openshell import (
    OPENSHELL_EVALUATED_REVISION,
    OPENSHELL_REVIEWED_RUNTIME_PROFILE,
    OpenShellArtifact,
    OpenShellClientRequest,
    OpenShellClientResult,
    OpenShellExecutionStatus,
    OpenShellExecutor,
    OpenShellHealth,
    OpenShellPolicyProjection,
)
from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
)


class FakeOpenShellClient:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.requests: list[OpenShellClientRequest] = []

    async def execute(self, request: OpenShellClientRequest) -> OpenShellClientResult:
        self.requests.append(request)
        if request.action == "echo":
            text = str(request.arguments.get("text", ""))
            return OpenShellClientResult(
                status=OpenShellExecutionStatus.SUCCEEDED,
                sandbox_id="openshell-sandbox-1",
                gateway_id="openshell-gateway-1",
                runtime_id="docker-runtime-1",
                result_code=0,
                output={"text": text},
                stdout=text,
                metadata={"transport": "fake", "compute_driver": request.runtime_profile},
            )
        if request.action == "fail":
            message = str(request.arguments.get("message", "controlled failure"))
            code_value = request.arguments.get("code", 1)
            code = code_value if isinstance(code_value, int) else 1
            return OpenShellClientResult(
                status=OpenShellExecutionStatus.FAILED,
                sandbox_id="openshell-sandbox-2",
                result_code=code,
                stderr=message,
                error_code="execution_failed",
                error_message=message,
            )
        if request.action == "sleep":
            seconds_value = request.arguments.get("seconds", 0.0)
            seconds = float(seconds_value) if isinstance(seconds_value, (int, float)) else 0.0
            await asyncio.sleep(seconds)
            return OpenShellClientResult(
                status=OpenShellExecutionStatus.SUCCEEDED,
                sandbox_id="openshell-sandbox-3",
                result_code=0,
                output={"slept_seconds": seconds},
            )
        if request.action == "write_artifact":
            relative = str(request.arguments.get("path", "artifact.txt"))
            workspace = Path(request.workspace_path)
            destination = (workspace / relative).resolve()
            if destination != workspace and workspace not in destination.parents:
                return OpenShellClientResult(
                    status=OpenShellExecutionStatus.FAILED,
                    sandbox_id="openshell-sandbox-4",
                    error_code="invalid_request",
                    error_message="artifact path escapes execution workspace",
                )
            content = str(request.arguments.get("content", ""))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            return OpenShellClientResult(
                status=OpenShellExecutionStatus.SUCCEEDED,
                sandbox_id="openshell-sandbox-4",
                result_code=0,
                artifacts=(
                    OpenShellArtifact(
                        relative_path=relative,
                        media_type="text/plain",
                        size_bytes=len(content.encode("utf-8")),
                    ),
                ),
            )
        return OpenShellClientResult(
            status=OpenShellExecutionStatus.FAILED,
            error_code="unsupported_capability",
            error_message=f"unsupported action: {request.action}",
        )

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> OpenShellHealth:
        return OpenShellHealth(
            healthy=True,
            capabilities=("echo", "write_artifact", "fail", "sleep"),
            metadata={"transport": "fake", "runtime_profile": OPENSHELL_REVIEWED_RUNTIME_PROFILE},
        )


class TestOpenShellExecutorContract(ExecutorContractSuite):
    def build_executor(self, tmp_path: Path) -> tuple[Executor, str]:
        workspace = tmp_path / "workspaces" / "run-1"
        workspace.mkdir(parents=True)
        return (
            OpenShellExecutor(
                FakeOpenShellClient(),
                tmp_path / "workspaces",
                capabilities=("echo", "write_artifact", "fail", "sleep"),
            ),
            "run-1",
        )


def _executor(
    tmp_path: Path,
    *,
    policy_projection: OpenShellPolicyProjection | None = None,
) -> tuple[OpenShellExecutor, FakeOpenShellClient]:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = FakeOpenShellClient()
    executor = OpenShellExecutor(
        client,
        tmp_path / "workspaces",
        capabilities=("echo", "write_artifact", "fail", "sleep"),
        policy_projection=policy_projection,
    )
    return executor, client


def test_provider_ids_are_namespaced_and_canonical_ids_are_preserved(tmp_path: Path) -> None:
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
    assert result.adapter_metadata["openshell"]["sandbox_id"] == "openshell-sandbox-1"
    assert result.adapter_metadata["openshell"]["gateway_id"] == "openshell-gateway-1"
    assert result.adapter_metadata["openshell"]["runtime_id"] == "docker-runtime-1"


def test_provider_request_refs_are_private_and_unique_per_execution(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
    request = ExecutionRequest(
        task_id="task-1",
        run_id="run-1",
        correlation_id="corr-1",
        action="echo",
        workspace="run-1",
    )

    async def scenario() -> tuple[ExecutionResult, ExecutionResult]:
        first, second = await asyncio.gather(executor.execute(request), executor.execute(request))
        return first, second

    first, second = asyncio.run(scenario())
    assert first.status is ExecutionStatus.SUCCEEDED
    assert second.status is ExecutionStatus.SUCCEEDED
    refs = [provider_request.request_ref for provider_request in client.requests]
    assert len(refs) == 2
    assert len(set(refs)) == 2
    assert "run-1" not in refs


def test_reviewed_docker_profile_and_default_deny_policy_are_provider_private(
    tmp_path: Path,
) -> None:
    projection = OpenShellPolicyProjection(
        default_deny_egress=True,
        allowed_hosts=("api.example.test",),
        allowed_http=("GET /v1/data",),
    )
    executor, client = _executor(tmp_path, policy_projection=projection)
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
    assert client.requests[0].runtime_profile == OPENSHELL_REVIEWED_RUNTIME_PROFILE
    assert client.requests[0].policy_projection == projection
    assert executor.descriptor.metadata["default_deny_egress"] is True
    assert "allowed_hosts" not in result.output
    assert "allowed_http" not in result.adapter_metadata["openshell"]


def test_health_keeps_platform_lifecycle_and_policy_authority(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    descriptor = asyncio.run(executor.health())
    assert descriptor.executor_id == "openshell"
    assert descriptor.healthy is True
    assert descriptor.metadata["canonical_lifecycle_owner"] == "platform"
    assert descriptor.metadata["canonical_policy_owner"] == "platform"
    assert descriptor.metadata["evaluated_revision"] == OPENSHELL_EVALUATED_REVISION
    assert descriptor.metadata["runtime_profile"] == OPENSHELL_REVIEWED_RUNTIME_PROFILE


def test_direct_environment_projection_fails_closed_without_provider_dispatch(
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
                environment={"SYNTHETIC_SECRET": "provider-credential-canary"},
            )
        )
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INVALID_REQUEST
    assert "safe scoped provider/credential binding path" in result.error.message
    assert "provider-credential-canary" not in result.error.message
    assert client.requests == []


def test_workspace_escape_is_rejected_before_provider_dispatch(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                run_id="run-1",
                correlation_id="corr-1",
                action="echo",
                workspace="../escape",
            )
        )
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.WORKSPACE_ERROR
    assert client.requests == []


def test_provider_cannot_promote_artifact_outside_canonical_workspace(tmp_path: Path) -> None:
    class EscapingClient(FakeOpenShellClient):
        async def execute(self, request: OpenShellClientRequest) -> OpenShellClientResult:
            self.requests.append(request)
            return OpenShellClientResult(
                status=OpenShellExecutionStatus.SUCCEEDED,
                sandbox_id="openshell-escape",
                artifacts=(OpenShellArtifact(relative_path="../../escape.txt"),),
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    executor = OpenShellExecutor(
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


def test_timeout_and_cancellation_are_forwarded_by_private_request_ref(tmp_path: Path) -> None:
    timeout_executor, timeout_client = _executor(tmp_path / "timeout")
    timeout_result = asyncio.run(
        timeout_executor.execute(
            ExecutionRequest(
                task_id="task-timeout",
                run_id="run-1",
                correlation_id="corr-timeout",
                action="sleep",
                workspace="run-1",
                arguments={"seconds": 1.0},
                timeout_seconds=0.001,
            )
        )
    )
    assert timeout_result.status is ExecutionStatus.TIMED_OUT
    assert timeout_client.cancelled == [timeout_client.requests[0].request_ref]

    cancel_executor, cancel_client = _executor(tmp_path / "cancel")
    token = CancellationToken()
    request = ExecutionRequest(
        task_id="task-cancel",
        run_id="run-1",
        correlation_id="corr-cancel",
        action="sleep",
        workspace="run-1",
        arguments={"seconds": 1.0},
        cancellation=token,
    )

    async def scenario() -> ExecutionResult:
        execution = asyncio.create_task(cancel_executor.execute(request))
        await asyncio.sleep(0.01)
        token.cancel()
        return await execution

    cancel_result = asyncio.run(scenario())
    assert cancel_result.status is ExecutionStatus.CANCELLED
    assert cancel_client.cancelled == [cancel_client.requests[0].request_ref]


def test_hanging_provider_cancel_cannot_block_canonical_timeout_result(tmp_path: Path) -> None:
    class HangingCancelClient(FakeOpenShellClient):
        async def cancel(self, request_ref: str) -> None:
            self.cancelled.append(request_ref)
            await asyncio.Event().wait()

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = HangingCancelClient()
    executor = OpenShellExecutor(
        client,
        tmp_path / "workspaces",
        capabilities=("sleep",),
        cancel_timeout_seconds=0.01,
    )
    request = ExecutionRequest(
        task_id="task-timeout",
        run_id="run-1",
        correlation_id="corr-timeout",
        action="sleep",
        workspace="run-1",
        arguments={"seconds": 1.0},
        timeout_seconds=0.001,
    )

    async def scenario() -> ExecutionResult | None:
        execution = asyncio.create_task(executor.execute(request))
        done, _ = await asyncio.wait({execution}, timeout=0.2)
        if not done:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            return None
        return execution.result()

    result = asyncio.run(scenario())
    assert result is not None
    assert result.status is ExecutionStatus.TIMED_OUT
    assert client.cancelled == [client.requests[0].request_ref]


def test_infrastructure_failures_are_redacted_and_not_retried_by_adapter(tmp_path: Path) -> None:
    class UnavailableClient(FakeOpenShellClient):
        async def execute(self, request: OpenShellClientRequest) -> OpenShellClientResult:
            self.requests.append(request)
            return OpenShellClientResult(
                status=OpenShellExecutionStatus.FAILED,
                sandbox_id="secret-provider-id",
                error_code="gateway_unavailable",
                error_message="gateway failed with internal token=secret",
                stderr="internal token=secret",
                retryable=True,
                metadata={"transport": "grpc", "credential": "must-not-leak"},
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = UnavailableClient()
    executor = OpenShellExecutor(client, tmp_path / "workspaces", capabilities=("echo",))
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
    assert result.error.message == "OpenShell provider execution failed"
    assert "secret" not in result.stderr
    assert "credential" not in result.adapter_metadata["openshell"]
    assert len(client.requests) == 1
