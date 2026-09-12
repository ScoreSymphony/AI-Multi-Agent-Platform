from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from executor_contract_suite import ExecutorContractSuite

from ai_multi_agent_platform.adapters.swe_rex import (
    SWE_REX_EVALUATED_REVISION,
    SWE_REX_EVALUATED_VERSION,
    SwerexArtifact,
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
    ExecutionResult,
    ExecutionStatus,
    Executor,
)


class FakeSwerexClient:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.requests: list[SwerexClientRequest] = []

    async def execute(self, request: SwerexClientRequest) -> SwerexClientResult:
        self.requests.append(request)
        if request.action == "echo":
            text = str(request.arguments.get("text", ""))
            return SwerexClientResult(
                status=SwerexExecutionStatus.SUCCEEDED,
                deployment_id="deployment-provider-1",
                runtime_id="runtime-provider-1",
                result_code=0,
                output={"text": text},
                stdout=text,
            )
        if request.action == "fail":
            message = str(request.arguments.get("message", "controlled failure"))
            code_value = request.arguments.get("code", 1)
            code = code_value if isinstance(code_value, int) else 1
            return SwerexClientResult(
                status=SwerexExecutionStatus.FAILED,
                deployment_id="deployment-provider-2",
                result_code=code,
                stderr=message,
                error_code="execution_failed",
                error_message=message,
            )
        if request.action == "sleep":
            seconds_value = request.arguments.get("seconds", 0.0)
            seconds = float(seconds_value) if isinstance(seconds_value, (int, float)) else 0.0
            await asyncio.sleep(seconds)
            return SwerexClientResult(
                status=SwerexExecutionStatus.SUCCEEDED,
                deployment_id="deployment-provider-3",
                result_code=0,
                output={"slept_seconds": seconds},
            )
        if request.action == "write_artifact":
            relative = str(request.arguments.get("path", "artifact.txt"))
            workspace = Path(request.workspace_path)
            destination = (workspace / relative).resolve()
            if destination != workspace and workspace not in destination.parents:
                return SwerexClientResult(
                    status=SwerexExecutionStatus.FAILED,
                    deployment_id="deployment-provider-4",
                    error_code="invalid_request",
                    error_message="artifact path escapes execution workspace",
                )
            content = str(request.arguments.get("content", ""))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
            return SwerexClientResult(
                status=SwerexExecutionStatus.SUCCEEDED,
                deployment_id="deployment-provider-4",
                runtime_id="runtime-provider-4",
                result_code=0,
                artifacts=(
                    SwerexArtifact(
                        relative_path=relative,
                        media_type="text/plain",
                        size_bytes=len(content.encode("utf-8")),
                    ),
                ),
            )
        return SwerexClientResult(
            status=SwerexExecutionStatus.FAILED,
            error_code="unsupported_capability",
            error_message=f"unsupported action: {request.action}",
        )

    async def cancel(self, request_ref: str) -> None:
        self.cancelled.append(request_ref)

    async def health(self) -> SwerexHealth:
        return SwerexHealth(
            healthy=True,
            capabilities=("echo", "write_artifact", "fail", "sleep"),
            metadata={
                "runtime_transport": "fake",
                "provider_version": SWE_REX_EVALUATED_VERSION,
            },
        )


class TestSwerexExecutorContract(ExecutorContractSuite):
    def build_executor(self, tmp_path: Path) -> tuple[Executor, str]:
        workspace = tmp_path / "workspaces" / "run-1"
        workspace.mkdir(parents=True)
        return (
            SwerexExecutor(
                FakeSwerexClient(),
                tmp_path / "workspaces",
                capabilities=("echo", "write_artifact", "fail", "sleep"),
            ),
            "run-1",
        )


def _executor(
    tmp_path: Path,
    *,
    backend_kind: str = "docker",
    allow_unsandboxed_local: bool = False,
) -> tuple[SwerexExecutor, FakeSwerexClient]:
    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = FakeSwerexClient()
    executor = SwerexExecutor(
        client,
        tmp_path / "workspaces",
        capabilities=("echo", "write_artifact", "fail", "sleep"),
        backend_kind=backend_kind,
        allow_unsandboxed_local=allow_unsandboxed_local,
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
    metadata = result.adapter_metadata["swe_rex"]
    assert metadata["deployment_id"] == "deployment-provider-1"
    assert metadata["runtime_id"] == "runtime-provider-1"


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
        return await asyncio.gather(executor.execute(request), executor.execute(request))

    first, second = asyncio.run(scenario())
    assert first.status is ExecutionStatus.SUCCEEDED
    assert second.status is ExecutionStatus.SUCCEEDED
    refs = [provider_request.request_ref for provider_request in client.requests]
    assert len(refs) == 2
    assert len(set(refs)) == 2
    assert "run-1" not in refs


def test_unsandboxed_local_backend_requires_explicit_opt_in(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    with pytest.raises(ValueError, match="LocalDeployment executes directly on the host"):
        SwerexExecutor(
            FakeSwerexClient(),
            workspace_root,
            capabilities=("echo",),
            backend_kind="local",
        )

    executor = SwerexExecutor(
        FakeSwerexClient(),
        workspace_root,
        capabilities=("echo",),
        backend_kind="local",
        allow_unsandboxed_local=True,
    )
    assert executor.descriptor.metadata["backend_kind"] == "local"
    assert executor.descriptor.metadata["unsandboxed_local_opt_in"] is True


def test_local_backend_guard_normalizes_case_and_whitespace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    with pytest.raises(ValueError, match="LocalDeployment executes directly on the host"):
        SwerexExecutor(
            FakeSwerexClient(),
            workspace_root,
            capabilities=("echo",),
            backend_kind=" LOCAL ",
        )

    executor = SwerexExecutor(
        FakeSwerexClient(),
        workspace_root,
        capabilities=("echo",),
        backend_kind=" LOCAL ",
        allow_unsandboxed_local=True,
    )
    assert executor.descriptor.metadata["backend_kind"] == "local"


def test_health_preserves_platform_ownership_and_upstream_pin(tmp_path: Path) -> None:
    executor, _ = _executor(tmp_path)
    descriptor = asyncio.run(executor.health())
    assert descriptor.executor_id == "swe-rex"
    assert descriptor.healthy is True
    assert "echo" in descriptor.capabilities
    assert descriptor.metadata["runtime_transport"] == "fake"
    assert descriptor.metadata["canonical_lifecycle_owner"] == "platform"
    assert descriptor.metadata["evaluated_revision"] == SWE_REX_EVALUATED_REVISION
    assert descriptor.metadata["evaluated_version"] == SWE_REX_EVALUATED_VERSION


def test_inflight_cancellation_is_forwarded_by_private_request_ref(tmp_path: Path) -> None:
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


def test_timeout_is_forwarded_by_private_request_ref(tmp_path: Path) -> None:
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


def test_provider_cannot_report_artifact_outside_canonical_workspace(tmp_path: Path) -> None:
    class EscapingClient(FakeSwerexClient):
        async def execute(self, request: SwerexClientRequest) -> SwerexClientResult:
            self.requests.append(request)
            return SwerexClientResult(
                status=SwerexExecutionStatus.SUCCEEDED,
                deployment_id="deployment-escape",
                artifacts=(SwerexArtifact(relative_path="../../escape.txt"),),
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    executor = SwerexExecutor(
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


def test_provider_cannot_claim_uncollected_artifact(tmp_path: Path) -> None:
    class MissingArtifactClient(FakeSwerexClient):
        async def execute(self, request: SwerexClientRequest) -> SwerexClientResult:
            self.requests.append(request)
            return SwerexClientResult(
                status=SwerexExecutionStatus.SUCCEEDED,
                deployment_id="deployment-missing-artifact",
                artifacts=(SwerexArtifact(relative_path="missing.txt"),),
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    executor = SwerexExecutor(
        MissingArtifactClient(),
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
    assert "before canonical collection" in result.error.message


def test_environment_projection_fails_closed_without_provider_dispatch(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                run_id="run-1",
                correlation_id="corr-1",
                action="echo",
                workspace="run-1",
                environment={"SYNTHETIC_SECRET": "issue-861-canary"},
            )
        )
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INVALID_REQUEST
    assert "#34-safe" in result.error.message
    assert client.requests == []


def test_policy_context_is_not_forwarded_as_provider_authority(tmp_path: Path) -> None:
    executor, client = _executor(tmp_path)
    result = asyncio.run(
        executor.execute(
            ExecutionRequest(
                task_id="task-1",
                run_id="run-1",
                correlation_id="corr-1",
                action="echo",
                workspace="run-1",
                policy_context={"claimed_admin": True},
            )
        )
    )
    assert result.status is ExecutionStatus.SUCCEEDED
    assert len(client.requests) == 1
    assert not hasattr(client.requests[0], "policy_context")


def test_untrusted_provider_metadata_is_filtered_before_canonical_evidence(tmp_path: Path) -> None:
    class MetadataClient(FakeSwerexClient):
        async def execute(self, request: SwerexClientRequest) -> SwerexClientResult:
            self.requests.append(request)
            return SwerexClientResult(
                status=SwerexExecutionStatus.SUCCEEDED,
                deployment_id="deployment-metadata",
                metadata={
                    "runtime_transport": "fake",
                    "image": "sha256:abc",
                    "secret": "do-not-persist",
                    "backend_kind": "local",
                },
            )

    workspace = tmp_path / "workspaces" / "run-1"
    workspace.mkdir(parents=True)
    client = MetadataClient()
    executor = SwerexExecutor(
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
    metadata = result.adapter_metadata["swe_rex"]
    assert metadata["runtime_transport"] == "fake"
    assert metadata["image"] == "sha256:abc"
    assert metadata["backend_kind"] == "docker"
    assert "secret" not in metadata
