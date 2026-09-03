"""Optional Forge execution adapter behind the platform-owned Executor contract."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.execution.contracts import (
    CancellationToken,
    ExecutionArtifact,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
    ExecutorDescriptor,
)


class ForgeExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ForgeArtifact:
    relative_path: str
    media_type: str = "application/octet-stream"
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ForgeClientRequest:
    """Adapter-private request sent to a Forge transport/client implementation."""

    request_ref: str
    task_id: str
    run_id: str
    correlation_id: str
    action: str
    workspace_path: str
    step_id: str | None = None
    arguments: dict[str, JsonValue] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    policy_context: dict[str, JsonValue] = field(default_factory=dict)
    expected_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ForgeClientResult:
    """Transport-neutral Forge result before canonical platform translation."""

    status: ForgeExecutionStatus
    execution_id: str | None = None
    result_code: int | None = None
    output: dict[str, JsonValue] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[ForgeArtifact, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    resources: dict[str, JsonValue] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    retry_after_seconds: float | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ForgeHealth:
    healthy: bool
    capabilities: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class ForgeClient(Protocol):
    """Minimal backend-private seam; concrete HTTP/process transport comes later."""

    async def execute(self, request: ForgeClientRequest) -> ForgeClientResult: ...

    async def cancel(self, request_ref: str) -> None: ...

    async def health(self) -> ForgeHealth: ...


_ERROR_MAP: dict[str, ExecutionErrorCategory] = {
    "invalid_request": ExecutionErrorCategory.INVALID_REQUEST,
    "unsupported_capability": ExecutionErrorCategory.UNSUPPORTED_CAPABILITY,
    "workspace_error": ExecutionErrorCategory.WORKSPACE_ERROR,
    "execution_failed": ExecutionErrorCategory.EXECUTION_FAILED,
    "timeout": ExecutionErrorCategory.TIMEOUT,
    "cancelled": ExecutionErrorCategory.CANCELLED,
    "internal": ExecutionErrorCategory.INTERNAL,
    "unavailable": ExecutionErrorCategory.INTERNAL,
    "usage_exhausted": ExecutionErrorCategory.INTERNAL,
}


class ForgeExecutor(Executor):
    """Translate canonical execution requests/results to an optional Forge backend."""

    def __init__(
        self,
        client: ForgeClient,
        workspace_root: str | Path,
        *,
        capabilities: tuple[str, ...],
        executor_id: str = "forge",
    ) -> None:
        if not executor_id.strip():
            raise ValueError("executor_id must not be blank")
        self._client = client
        self._root = Path(workspace_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._capabilities = tuple(dict.fromkeys(capabilities))
        self._executor_id = executor_id

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id=self._executor_id,
            capabilities=self._capabilities,
            metadata={
                "adapter": "forge",
                "workspace_root": str(self._root),
                "canonical_lifecycle_owner": "platform",
            },
        )

    async def health(self) -> ExecutorDescriptor:
        try:
            health = await self._client.health()
        except Exception as exc:
            return ExecutorDescriptor(
                executor_id=self._executor_id,
                capabilities=self._capabilities,
                healthy=False,
                metadata={
                    **self.descriptor.metadata,
                    "health_error": str(exc),
                },
            )
        capabilities = health.capabilities or self._capabilities
        return ExecutorDescriptor(
            executor_id=self._executor_id,
            capabilities=capabilities,
            healthy=health.healthy,
            metadata={**self.descriptor.metadata, **health.metadata},
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started_at = datetime.now(UTC).isoformat()
        started = monotonic()

        try:
            workspace = self._workspace(request.workspace)
        except ValueError as exc:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.WORKSPACE_ERROR,
                str(exc),
            )

        if request.action not in self._capabilities:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.UNSUPPORTED_CAPABILITY,
                f"unsupported action: {request.action}",
            )
        if request.cancellation is not None and request.cancellation.cancelled:
            return self._cancelled(request, started_at, started)

        backend_request = ForgeClientRequest(
            request_ref=request.run_id,
            task_id=request.task_id,
            run_id=request.run_id,
            step_id=request.step_id,
            correlation_id=request.correlation_id,
            action=request.action,
            workspace_path=str(workspace),
            arguments=dict(request.arguments),
            environment=dict(request.environment),
            timeout_seconds=request.timeout_seconds,
            policy_context=dict(request.policy_context),
            expected_artifacts=request.expected_artifacts,
        )

        try:
            if request.cancellation is None:
                backend_result = (
                    await self._client.execute(backend_request)
                    if request.timeout_seconds is None
                    else await asyncio.wait_for(
                        self._client.execute(backend_request),
                        request.timeout_seconds,
                    )
                )
            else:
                result_task = asyncio.create_task(self._result_signal(backend_request))
                cancel_task = asyncio.create_task(self._cancel_signal(request.cancellation))
                done, pending = await asyncio.wait(
                    {result_task, cancel_task},
                    timeout=request.timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    await self._cancel_backend(request.run_id)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    return self._failure(
                        request,
                        started_at,
                        started,
                        ExecutionErrorCategory.TIMEOUT,
                        "execution timed out",
                        status=ExecutionStatus.TIMED_OUT,
                    )
                signal_task = next(iter(done))
                signal, result = signal_task.result()
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if signal == "cancel":
                    await self._cancel_backend(request.run_id)
                    return self._cancelled(request, started_at, started)
                if result is None:
                    raise RuntimeError("Forge result signal completed without a result")
                backend_result = result
        except TimeoutError:
            await self._cancel_backend(request.run_id)
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.TIMEOUT,
                "execution timed out",
                status=ExecutionStatus.TIMED_OUT,
            )
        except asyncio.CancelledError:
            await self._cancel_backend(request.run_id)
            return self._cancelled(request, started_at, started)
        except Exception as exc:
            return self._failure(
                request,
                started_at,
                started,
                ExecutionErrorCategory.INTERNAL,
                str(exc),
                retryable=True,
            )

        return self._translate_result(request, backend_result, started_at, started, workspace)

    async def cancel(self, token: CancellationToken) -> None:
        await super().cancel(token)

    async def _result_signal(
        self,
        request: ForgeClientRequest,
    ) -> tuple[str, ForgeClientResult | None]:
        return "result", await self._client.execute(request)

    @staticmethod
    async def _cancel_signal(
        token: CancellationToken,
    ) -> tuple[str, ForgeClientResult | None]:
        await token.wait()
        return "cancel", None

    def _workspace(self, workspace: str) -> Path:
        candidate = (self._root / workspace).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError("workspace escapes configured workspace root")
        if not candidate.exists() or not candidate.is_dir():
            raise ValueError("workspace is missing or unavailable")
        return candidate

    async def _cancel_backend(self, request_ref: str) -> None:
        try:
            await self._client.cancel(request_ref)
        except Exception:
            return

    def _translate_result(
        self,
        request: ExecutionRequest,
        backend: ForgeClientResult,
        started_at: str,
        started: float,
        workspace: Path,
    ) -> ExecutionResult:
        status = {
            ForgeExecutionStatus.SUCCEEDED: ExecutionStatus.SUCCEEDED,
            ForgeExecutionStatus.FAILED: ExecutionStatus.FAILED,
            ForgeExecutionStatus.TIMED_OUT: ExecutionStatus.TIMED_OUT,
            ForgeExecutionStatus.CANCELLED: ExecutionStatus.CANCELLED,
        }[backend.status]

        artifacts: list[ExecutionArtifact] = []
        for artifact in backend.artifacts:
            if not artifact.relative_path.strip():
                return self._failure(
                    request,
                    started_at,
                    started,
                    ExecutionErrorCategory.INTERNAL,
                    "Forge returned an empty artifact path",
                )
            artifact_path = (workspace / artifact.relative_path).resolve()
            if artifact_path != workspace and workspace not in artifact_path.parents:
                return self._failure(
                    request,
                    started_at,
                    started,
                    ExecutionErrorCategory.INTERNAL,
                    "Forge returned artifact evidence outside the execution workspace",
                )
            artifacts.append(
                ExecutionArtifact(
                    relative_path=artifact.relative_path,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                )
            )

        error: ExecutionError | None = None
        if status is not ExecutionStatus.SUCCEEDED:
            category = self._error_category(backend)
            message = backend.error_message or backend.stderr or f"Forge execution {backend.status}"
            details: dict[str, JsonValue] = {}
            if backend.error_code is not None:
                details["forge_error_code"] = backend.error_code
            if backend.retry_after_seconds is not None:
                details["retry_after_seconds"] = backend.retry_after_seconds
            error = ExecutionError(
                category=category,
                message=message,
                retryable=backend.retryable,
                details=details,
            )

        forge_metadata: dict[str, JsonValue] = {
            "backend_status": backend.status.value,
            **backend.metadata,
        }
        if backend.execution_id is not None:
            forge_metadata["execution_id"] = backend.execution_id
        if backend.retry_after_seconds is not None:
            forge_metadata["retry_after_seconds"] = backend.retry_after_seconds
        if backend.error_code is not None:
            forge_metadata["error_code"] = backend.error_code

        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=status,
            result_code=backend.result_code,
            output=backend.output,
            stdout=backend.stdout,
            stderr=backend.stderr,
            artifacts=tuple(artifacts),
            started_at=backend.started_at or started_at,
            finished_at=backend.finished_at or datetime.now(UTC).isoformat(),
            duration_seconds=(
                backend.duration_seconds
                if backend.duration_seconds is not None
                else monotonic() - started
            ),
            resources=backend.resources,
            error=error,
            adapter_metadata={"forge": forge_metadata},
        )

    @staticmethod
    def _error_category(backend: ForgeClientResult) -> ExecutionErrorCategory:
        if backend.status is ForgeExecutionStatus.TIMED_OUT:
            return ExecutionErrorCategory.TIMEOUT
        if backend.status is ForgeExecutionStatus.CANCELLED:
            return ExecutionErrorCategory.CANCELLED
        if backend.error_code is not None:
            return _ERROR_MAP.get(backend.error_code, ExecutionErrorCategory.INTERNAL)
        return ExecutionErrorCategory.EXECUTION_FAILED

    def _failure(
        self,
        request: ExecutionRequest,
        started_at: str,
        started: float,
        category: ExecutionErrorCategory,
        message: str,
        *,
        status: ExecutionStatus = ExecutionStatus.FAILED,
        code: int | None = None,
        retryable: bool = False,
    ) -> ExecutionResult:
        return ExecutionResult(
            task_id=request.task_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            step_id=request.step_id,
            status=status,
            result_code=code,
            stderr=message,
            error=ExecutionError(category=category, message=message, retryable=retryable),
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            duration_seconds=monotonic() - started,
            adapter_metadata={"forge": {"adapter_failure": True}},
        )

    def _cancelled(
        self,
        request: ExecutionRequest,
        started_at: str,
        started: float,
    ) -> ExecutionResult:
        return self._failure(
            request,
            started_at,
            started,
            ExecutionErrorCategory.CANCELLED,
            "execution cancelled",
            status=ExecutionStatus.CANCELLED,
        )
